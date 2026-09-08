"""Bounded reader for official HTML pages and public edital PDFs.

The PCI remains the discovery source. This module follows a small number of
scored links, never bypasses CAPTCHA, blocks private-network targets, and only
applies extracted requirements when they can be scoped conservatively.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import socket
import time
import heapq
import itertools
import bisect
import unicodedata
from datetime import date, datetime
from io import BytesIO
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

import config
from .parser import (
    clean_text,
    extract_pci_document_references,
    extract_requirement_sentences,
    normalize_text,
    parse_registration_period,
)
from .requirements import extract_requirement_fields, split_academic_requirement


LOGGER = logging.getLogger(__name__)
GENERIC_CONTEXT = {
    "professor", "professora", "concurso", "processo", "seletivo", "publico",
    "vaga", "vagas", "universidade", "instituto", "federal", "estadual",
    "substituto", "titular", "adjunto", "assistente", "campus", "edital",
    "abre", "publica", "para", "area", "areas", "atuacao", "cargo",
    "avaliacao", "didatica", "prova", "provas", "titulos", "curriculo",
    "requisito", "requisitos", "local", "carga", "horaria", "semanal",
    "temporario", "temporarios", "colaborador", "colaboradores",
}


# Seeds — the PCI notice and the institution links it named — outrank any
# link discovered later, and keep their given order among themselves.
SEED_PRIORITY = 10_000

# Some institutions remove a selection from their public index as soon as
# applications close, although the stable detail page and edital remain
# public.  These source-specific seeds keep already-discovered PCI notices
# auditable instead of making the crawler guess numeric archive URLs.
OFFICIAL_SEED_OVERRIDES = {
    "/noticias/uel-pr-abre-processo-seletivo-para-professores-temporarios-com-diversas-areas-de-atuacao":
        "https://www.cops.uel.br/v2/download.php?Acesso=YzlmNzU2YTBiMWIzYTM4MDZiM2RmN2FiYWEzZDdkMWE5NTZkZWZhMTg5NzM4MmFhNDEyYjA0NmY1MmJlZmJhNjg1ZjE3Y2Q0ZjJhMTQ4MzU5Y2NlYmQyMDM2MGM4OThmYzMzZTRjZmYxMjI0OTUwYjkxZDgwZTYzODI2ODhlNTNlOWFlZTE2ZmZhMjQ1OWQ5NDJkNWVmNzI1NmQ1OTk3MzU4NjQ1YjMyZmU2MjdjNGFjNjc0NmU4MmU2ZmI4Njhj",
}


class OfficialReadError(RuntimeError):
    pass


def canonical_url(url: str, base_url: str | None = None) -> str:
    absolute = urljoin(base_url or "", (url or "").strip())
    parts = urlsplit(absolute)
    path = re.sub(r"/+", "/", parts.path or "/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def is_public_http_url(url: str) -> bool:
    """Reject local/private targets before issuing a request (basic SSRF guard)."""
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return False
        if parts.port not in (None, 80, 443):
            return False
        host = parts.hostname.rstrip(".").lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
            return False
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM)}
        if not addresses:
            return False
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                return False
        return True
    except (OSError, ValueError):
        return False


def _tokens(value: str | None) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]{4,}", normalize_text(value))
        if token not in GENERIC_CONTEXT
    }


def vacancy_context_tokens(vacancy: Mapping[str, Any]) -> set[str]:
    area = normalize_text(str(vacancy.get("area") or ""))
    fields = [vacancy.get("area"), vacancy.get("subarea")]
    if not area or area == "nao identificada":
        fields.extend((vacancy.get("title"), vacancy.get("description")))
    result: set[str] = set()
    for field in fields:
        result.update(_tokens(str(field or "")))
    return result


def assess_document_relevance(
    pages: Iterable[tuple[int, str]], vacancy: Mapping[str, Any], document_url: str
) -> tuple[bool, str]:
    """Require evidence that the document concerns this teaching selection."""
    text = normalize_text(" ".join(page_text for _, page_text in pages))
    # Two independent signals instead of one exact phrase. Requiring a fixed
    # wording rejected 94 real editais: a municipal notice writes "CONCURSO
    # PÚBLICO Nº 001/2026" in its heading and "PROFESSOR DE EDUCAÇÃO BÁSICA" in
    # a table, and matches none of the phrases the previous list demanded. The
    # scoping checks below still decide whether the document is about *this*
    # vacancy, so this gate only has to exclude documents that are not a
    # teaching selection at all — cookie notices, privacy policies, decrees.
    selection_markers = (
        "edital", "concurso publico", "concurso", "processo seletivo",
        "selecao publica", "teste seletivo", "chamada publica",
    )
    teaching_markers = (
        "professor", "professora", "docente", "magisterio", "regente de classe",
    )
    if not any(marker in text for marker in selection_markers):
        return False, "O documento não é um edital ou aviso de seleção."
    if not any(marker in text for marker in teaching_markers):
        return False, "O documento é uma seleção, mas não menciona cargo docente."

    identifiers = known_edital_numbers(vacancy)
    document_identifiers = {
        f"{int(m.group(1))}/{m.group(2)[-2:]}" for m in LOOSE_IDENTIFIER.finditer(text)
    }
    shared = identifiers & document_identifiers
    if shared:
        return True, f"Número do edital coincide com o anunciado no PCI: {', '.join(sorted(shared))}."

    area = normalize_text(str(vacancy.get("area") or ""))
    if area and area != "nao identificada":
        area_tokens = _tokens(area)
        overlap = sorted(token for token in area_tokens if token in text)
        if overlap:
            return True, f"Documento docente associado à área por: {', '.join(overlap[:5])}."
        return False, "O documento docente não menciona a área identificada no anúncio."

    # No usable area. The institution's own distinctive name is then the best
    # signal left — deliberately checked only here, because for a vacancy whose
    # area IS known the area must match: a UNESP edital for Fonoaudiologia is
    # the wrong document for a UNESP vacancy in Música, however right the
    # institution is.
    institution_tokens = _tokens(str(vacancy.get("institution") or "")) - {
        "prefeitura", "municipal", "municipio", "estado", "estadual", "federal",
        "secretaria", "educacao", "camara", "instituto", "fundacao",
    }
    named = sorted(token for token in institution_tokens if token in text)
    if named:
        return True, f"Documento de seleção docente da instituição: {', '.join(named[:3])}."

    title_tokens = _tokens(" ".join(str(vacancy.get(key) or "") for key in ("title", "description")))
    normalized_url = normalize_text(document_url)
    overlap = sorted(token for token in title_tokens if token in text or token in normalized_url)
    if len(overlap) >= 2:
        return True, f"Documento docente associado ao anúncio por: {', '.join(overlap[:5])}."
    return False, "A vaga não tem área/número identificável e o documento não coincide com contexto suficiente."


EDITAL_IDENTIFIER = re.compile(
    r"(?:edital|concurso)\s*(?:n(?:o|º|°)?\.?\s*)?(\d{1,5}(?:\s*[/.-]\s*\d{2,4})?)",
    re.I,
)
LOOSE_IDENTIFIER = re.compile(r"(\d{1,5})\s*[/.-]\s*(\d{2,4})")


def known_edital_numbers(vacancy: Mapping[str, Any]) -> set[str]:
    """Edital numbers we can state for this vacancy.

    PCI hides the PDF behind human verification but leaves the link's label in
    the page — "EDITAL DE ABERTURA Nº 005/2026". That label names the document
    precisely, and it is present for 120 of the 149 blocked vacancies against
    only 7 whose prose happens to state a number. Ignoring it wastes the single
    strongest identifier available for the ones we cannot download.
    """
    numbers: set[str] = set()
    labels = " ".join(
        str(item.get("label") or "")
        for item in (vacancy.get("official_pci_protected_documents") or [])
    )
    prose = " ".join(str(vacancy.get(key) or "") for key in ("title", "raw_text", "description"))
    for match in LOOSE_IDENTIFIER.finditer(labels):
        numbers.add(f"{int(match.group(1))}/{match.group(2)[-2:]}")
    for match in EDITAL_IDENTIFIER.finditer(prose):
        loose = LOOSE_IDENTIFIER.search(match.group(1))
        if loose:
            numbers.add(f"{int(loose.group(1))}/{loose.group(2)[-2:]}")
    return numbers


def edital_numbers_for_display(vacancy: Mapping[str, Any]) -> list[str]:
    """The edital numbers as the institution writes them, for a human to search.

    Matching normalises the year to two digits so "005/2026" and "5/26" compare
    equal; a reader looking for the document needs it spelled the way the
    edital is actually named.
    """
    labels = " ".join(
        str(item.get("label") or "")
        for item in (vacancy.get("official_pci_protected_documents") or [])
    )
    prose = " ".join(str(vacancy.get(key) or "") for key in ("title", "raw_text"))
    shown: dict[str, str] = {}
    for source in (labels, prose if not labels else ""):
        for match in LOOSE_IDENTIFIER.finditer(source):
            year = match.group(2)
            year = year if len(year) == 4 else f"20{year}"
            key = f"{int(match.group(1))}/{year[-2:]}"
            shown.setdefault(key, f"{match.group(1)}/{year}")
    return sorted(shown.values())


def _requirement_kinds(text: str) -> list[str]:
    parts = split_academic_requirement(text)
    kinds = []
    if parts["graduation"]:
        kinds.append("graduation")
    post = " ".join(parts["postgraduate"])
    if re.search(r"\bmestrado\b|\bt[ií]tulo\s+de\s+mestre\b|\bgrau\s+de\s+mestre\b", post, re.I):
        kinds.append("masters")
    if re.search(r"\bdoutorado\b|\bt[ií]tulo\s+de\s+doutor\b|\bgrau\s+de\s+doutor\b", post, re.I):
        kinds.append("doctorate")
    if parts["postgraduate"]:
        kinds.append("postgraduate")
    return kinds


def _page_segments(text: str) -> list[str]:
    lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]
    if len(lines) < 4:
        lines = [clean_text(item) for item in re.split(r"(?<=[.;:])\s+", clean_text(text)) if clean_text(item)]
    return lines


def extract_requirement_evidence(
    pages: Iterable[tuple[int, str]],
    vacancy: Mapping[str, Any],
    *,
    allow_unscoped: bool,
) -> dict[str, Any]:
    """Return page-addressable evidence, refusing ambiguous multi-area text."""
    context = vacancy_context_tokens(vacancy)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page_number, page_text in pages:
        lines = _page_segments(page_text)
        for index, line in enumerate(lines):
            kinds = _requirement_kinds(line)
            if not kinds:
                continue
            start, end = max(0, index - 2), min(len(lines), index + 3)
            excerpt = clean_text(" ".join(lines[start:end]))[:1400]
            key = normalize_text(excerpt)
            if not key or key in seen:
                continue
            seen.add(key)
            normalized = normalize_text(excerpt)
            overlap = sorted(token for token in context if token in normalized)
            candidates.append({
                "page": page_number,
                "text": excerpt,
                "kinds": _requirement_kinds(excerpt),
                "context_terms": overlap,
                "context_score": len(overlap),
            })

    if not candidates:
        return {
            "applicable": False, "confidence": "NONE", "requirements": {},
            "evidence": [], "reason": "O documento não contém requisitos textuais reconhecíveis.",
        }

    area = normalize_text(str(vacancy.get("area") or ""))
    area_identified = bool(area and area != "nao identificada")
    if not area_identified and len(candidates) > 3:
        return {
            "applicable": False, "confidence": "AMBIGUOUS", "requirements": {},
            "evidence": candidates[:10],
            "reason": "O edital contém várias áreas/requisitos, mas o anúncio agregado do PCI não identifica qual bloco corresponde à oportunidade.",
        }

    maximum = max(item["context_score"] for item in candidates)
    if maximum > 0:
        selected = [item for item in candidates if item["context_score"] >= max(1, maximum - 1)]
        confidence = "HIGH" if maximum >= 2 else "MEDIUM"
        reason = "Requisitos associados à área/cargo por termos de contexto."
    elif allow_unscoped and len(candidates) <= 3:
        selected = candidates
        confidence = "MEDIUM"
        reason = "Documento com poucos blocos de requisitos; associação conservadora à vaga."
    else:
        return {
            "applicable": False, "confidence": "AMBIGUOUS", "requirements": {},
            "evidence": candidates[:10],
            "reason": "O edital contém múltiplos requisitos e nenhum bloco pôde ser associado com segurança à área da vaga.",
        }

    requirements: dict[str, str] = {}
    parsed = extract_requirement_fields(
        " ".join(dict.fromkeys(item["text"] for item in selected))
    )
    field_names = {
        "graduation_requirement": "graduation_requirement_raw",
        "postgraduate_requirement": "postgraduate_requirement_raw",
        "masters_requirement": "masters_requirement_raw",
        "doctorate_requirement": "doctorate_requirement_raw",
    }
    for parsed_field, stored_field in field_names.items():
        if parsed.get(parsed_field):
            requirements[stored_field] = str(parsed[parsed_field])[:3000]
    return {
        "applicable": bool(requirements), "confidence": confidence,
        "requirements": requirements, "evidence": selected[:10], "reason": reason,
    }


def extract_structured_opportunities(pages: Iterable[tuple[int, str]]) -> list[dict[str, Any]]:
    """Extract repeated area/requirements table blocks from multi-area editais."""
    page_list = list(pages)
    opportunities: list[dict[str, Any]] = []
    marker = re.compile(
        r"(?:área|area)\s+de\s+conhecimento\s+ou\s+mat[eé]ria\(s\)\s+",
        re.I,
    )
    end_marker = re.compile(r"tipos?\s+de\s+prova", re.I)
    for page_number, page_text in page_list:
        matches = list(marker.finditer(page_text))
        for index, match in enumerate(matches):
            block_end = matches[index + 1].start() if index + 1 < len(matches) else len(page_text)
            block = clean_text(page_text[match.end():block_end])
            proof = end_marker.search(block)
            if proof:
                block = block[: proof.end() + 220]
            area_match = re.search(
                r"^(.*?)(?=n[º°o]\s*de\s+vaga|n[uú]mero\s+de\s+vaga|local\s+de\s+atua[cç][aã]o|requisito\(s\))",
                block,
                re.I,
            )
            requirement_match = re.search(
                r"requisito\(s\)\s+(.*?)(?=tipos?\s+de\s+prova|$)",
                block,
                re.I,
            )
            if not area_match or not requirement_match:
                continue
            area = clean_text(area_match.group(1))
            requirement_text = clean_text(requirement_match.group(1))
            if len(area) < 3 or len(requirement_text) < 5:
                continue
            requirements = extract_requirement_fields(f"Requisitos: {requirement_text}")
            reference_match = re.search(r"\bDTD\s*[\d-]+", block, re.I)
            workload_match = re.search(r"\b\d{1,3}\s+horas?\s+semanais\b", block, re.I)
            campus_match = re.search(r"local\s+de\s+atua[cç][aã]o\s+(.+?)(?=requisito\(s\)|$)", block, re.I)
            vacancy_match = re.search(r"\b(\d+)\s+vagas?\b", block, re.I)
            opportunities.append({
                "area": area,
                "requirement_text": requirement_text,
                "graduation_requirement_raw": requirements["graduation_requirement"],
                "postgraduate_requirement_raw": requirements["postgraduate_requirement"],
                "masters_requirement_raw": requirements["masters_requirement"],
                "doctorate_requirement_raw": requirements["doctorate_requirement"],
                "page": page_number,
                "reference": clean_text(reference_match.group(0)) if reference_match else None,
                "workload": clean_text(workload_match.group(0)) if workload_match else None,
                "campus": clean_text(campus_match.group(1)) if campus_match else None,
                "vacancies_count": int(vacancy_match.group(1)) if vacancy_match else None,
            })
    unique: dict[str, dict[str, Any]] = {}
    for item in opportunities:
        key = normalize_text(f"{item.get('reference')}|{item['area']}|{item['requirement_text']}")
        unique[key] = item
    if len(unique) > 1:
        return list(unique.values())
    labelled = extract_labelled_area_requirements(page_list)
    if len(labelled) > len(unique):
        return labelled
    numbered = extract_numbered_requirements_table(page_list)
    return numbered if len(numbered) > len(unique) else list(unique.values())


def extract_labelled_area_requirements(pages: Iterable[tuple[int, str]]) -> list[dict[str, Any]]:
    """Extract one row per ``Area / Requisito Minimo`` block in an annex.

    UEL's teaching notices use a prose-like labelled table rather than PDF
    table geometry.  Joining the pages before splitting the blocks matters:
    an area's heading can be at the bottom of one page while its requirement
    starts on the next.  The strict set of labels prevents ordinary edital
    prose and the later syllabus annex from becoming vacancies.
    """
    page_list = list(pages)
    combined_parts: list[str] = []
    page_starts: list[int] = []
    page_numbers: list[int] = []
    cursor = 0
    for page_number, page_text in page_list:
        page_text = unicodedata.normalize("NFC", page_text)
        page_text = "".join(char for char in page_text if unicodedata.category(char) != "Cf")
        lines = page_text.splitlines()
        cleaned_lines: list[str] = []
        for line_index, line in enumerate(lines):
            normalized_line = normalize_text(line)
            compact_line = re.sub(r"\s+", "", normalized_line)
            if line_index < 10 and (
                "campus universitario:" in normalized_line
                or compact_line.startswith("londrina")
            ):
                continue
            cleaned_lines.append(line)
        page_text = "\n".join(cleaned_lines)
        page_starts.append(cursor)
        page_numbers.append(page_number)
        combined_parts.append(page_text)
        cursor += len(page_text) + 2
    text = "\n\n".join(combined_parts)

    annex = re.search(r"(?im)^\s*ANEXO\s+I\s*$", text)
    if not annex:
        return []
    annex_start = annex.end()
    following = re.search(r"(?im)^\s*ANEXO\s+II\b", text[annex_start:])
    annex_end = annex_start + following.start() if following else len(text)
    annex_text = text[annex_start:annex_end]
    normalized_annex = normalize_text(annex_text[:12000])
    if not all(label in normalized_annex for label in (
        "requisito minimo", "taxa de inscricao", "forma de selecao",
    )):
        return []

    area_marker = re.compile(r"(?im)^\s*[ÁA]rea(?:\s*/\s*sub[áa]rea)?\s*:\s*")
    markers = list(area_marker.finditer(annex_text))
    if len(markers) < 2:
        return []
    vacancy_label = re.compile(
        r"(?im)^\s*N(?:[º°o]|[uú]mero)?\s*de\s+Vagas?\s*:\s*"
    )
    requirement_label = re.compile(r"(?im)^\s*Requisito\s+M[ií]nimo\s*:\s*")
    fee_label = re.compile(r"(?im)^\s*Taxa\s+de\s+Inscri[cç][aã]o\s*:\s*")
    workload_label = re.compile(r"(?im)^\s*Regime\s+de\s+Trabalho\s*:\s*")
    selection_label = re.compile(r"(?im)^\s*Forma\s+de\s+Sele[cç][aã]o\s*:\s*")
    department_label = re.compile(r"(?im)^\s*(DEPARTAMENTO\s+DE[^\r\n]+?)\s*$")
    academic_start = re.compile(
        r"\b(?:gradua[cç][aã]o|licenciatura|bacharelado|curso\s+superior|"
        r"especializa[cç][aã]o|mestrado|doutorado|resid[eê]ncia\s+m[eé]dica)\b",
        re.I,
    )

    opportunities: list[dict[str, Any]] = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(annex_text)
        block = annex_text[marker.end():end]
        vacancy = vacancy_label.search(block)
        requirement = requirement_label.search(block)
        fee = fee_label.search(block)
        workload = workload_label.search(block)
        selection = selection_label.search(block)
        if not vacancy or not fee or not selection:
            continue
        fallback_requirement = None
        if not requirement and workload:
            fallback_requirement = academic_start.search(block, workload.end(), fee.start())
        requirement_start = requirement.start() if requirement else (
            fallback_requirement.start() if fallback_requirement else None
        )
        requirement_end = requirement.end() if requirement else requirement_start
        if requirement_start is None or requirement_end is None:
            continue
        if not (vacancy.start() < requirement_start < fee.start() < selection.start()):
            continue

        area = clean_text(block[:vacancy.start()]).strip(" .;:-\u2013")
        vacancy_end = workload.start() if workload and workload.start() > vacancy.end() else requirement.start()
        vacancy_text = clean_text(block[vacancy.end():vacancy_end]).strip(" .;:-\u2013")
        requirement_text = clean_text(block[requirement_end:fee.start()]).strip(" .;:-\u2013")
        if len(area) < 2 or len(requirement_text) < 3:
            continue

        workload_text = None
        if workload and workload.start() < requirement_start:
            workload_value = block[workload.end():requirement_start]
            workload_match = re.search(
                r"\b\d{1,3}\s*(?:\([^)]+\)\s*)?horas?\s+semanais\b",
                workload_value,
                re.I,
            )
            workload_text = clean_text(
                workload_match.group(0) if workload_match else workload_value
            ).strip(" .;:-\u2013")
        count_match = re.search(r"\b(\d+)\b", vacancy_text)
        reserve_only = "cadastro de reserva" in normalize_text(vacancy_text)
        requirements = extract_requirement_fields(f"Requisitos: {requirement_text}")

        prefix = annex_text[:marker.start()]
        departments = list(department_label.finditer(prefix))
        department = clean_text(departments[-1].group(1)) if departments else None
        absolute_offset = annex_start + marker.start()
        page_index = max(0, bisect.bisect_right(page_starts, absolute_offset) - 1)
        reference = "Cadastro de reserva" if reserve_only else None
        if count_match and not reserve_only:
            count = int(count_match.group(1))
            reference = f"{count} vaga" if count == 1 else f"{count} vagas"
        opportunities.append({
            "area": area,
            "requirement_text": requirement_text,
            "graduation_requirement_raw": requirements["graduation_requirement"],
            "postgraduate_requirement_raw": requirements["postgraduate_requirement"],
            "masters_requirement_raw": requirements["masters_requirement"],
            "doctorate_requirement_raw": requirements["doctorate_requirement"],
            "page": page_numbers[page_index] if page_numbers else None,
            "reference": reference,
            "department": department,
            "workload": workload_text,
            "vacancies_count": int(count_match.group(1)) if count_match and not reserve_only else None,
            "reserve_only": reserve_only,
            "requirements_complete": True,
        })

    unique: dict[str, dict[str, Any]] = {}
    for item in opportunities:
        key = normalize_text(f"{item.get('department')}|{item['area']}|{item['requirement_text']}")
        unique[key] = item
    return list(unique.values())


def extract_numbered_requirements_table(pages: Iterable[tuple[int, str]]) -> list[dict[str, Any]]:
    """Extract numbered campus/area rows from consolidated annex tables.

    UNIOESTE's Anexo V is a 52-page table flattened by PDF extraction.  Its
    reliable row key is ``sequence + campus``; numbered syllabus items cannot
    be mistaken for rows because they are not followed by a campus name.
    """
    page_list = list(pages)
    combined_parts: list[str] = []
    page_starts: list[int] = []
    page_numbers: list[int] = []
    cursor = 0
    for page_number, page_text in page_list:
        page_text = unicodedata.normalize("NFC", page_text)
        page_text = "".join(char for char in page_text if unicodedata.category(char) != "Cf")
        page_starts.append(cursor)
        page_numbers.append(page_number)
        combined_parts.append(page_text)
        cursor += len(page_text) + 2
    text = "\n\n".join(combined_parts)
    normalized_head = normalize_text(text[:8000])
    if not (
        "requisitos minimos" in normalized_head
        and "conteudos programaticos" in normalized_head
        and "anexo v" in normalized_head
    ):
        return []
    campus_pattern = (
        r"CASCAVEL|FOZ\s+DO\s+IGUA[CÇ]U|FRANCISCO\s+BELTR[AÃ]O|"
        r"MARECHAL\s+C[AÂ]NDIDO\s+RONDON|TOLEDO"
    )
    row_marker = re.compile(
        rf"(?m)^\s*(?P<seq>\d{{1,3}})\s+(?P<campus>{campus_pattern})\s+",
        re.I,
    )
    matches = list(row_marker.finditer(text))
    opportunities: list[dict[str, Any]] = []
    academic_start = re.compile(
        r"\b(?:gradua[cç][aã]o|licenciatura|bacharelado|curso\s+superior|"
        r"especializa[cç][aã]o|mestrado|doutorado|t[ií]tulo\s+de\s+(?:mestre|doutor))\b",
        re.I,
    )
    syllabus_start = re.compile(r"(?m)^\s*1\.\s+")
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.end():end]
        requirement_match = academic_start.search(block)
        if not requirement_match:
            continue
        heading = clean_text(block[: requirement_match.start()]).strip(" .;:-–")
        requirement_block = block[requirement_match.start():]
        syllabus = syllabus_start.search(requirement_block)
        if syllabus:
            requirement_block = requirement_block[: syllabus.start()]
        requirement_text = clean_text(requirement_block).strip(" .;:-–")
        # A page break may leave a bare page number at the edge of the cell.
        requirement_text = re.sub(r"\s+\d{1,2}\s*$", "", requirement_text).strip()
        if len(heading) < 2 or len(requirement_text) < 5:
            continue
        area = re.sub(r"^[A-ZÀ-Ú]{2,12}\s*[-–]\s*", "", heading).strip()
        if not area:
            area = heading
        requirements = extract_requirement_fields(f"Requisitos: {requirement_text}")
        absolute_offset = match.start()
        page_index = max(0, bisect.bisect_right(page_starts, absolute_offset) - 1)
        opportunities.append({
            "area": area,
            "requirement_text": requirement_text,
            "graduation_requirement_raw": requirements["graduation_requirement"],
            "postgraduate_requirement_raw": requirements["postgraduate_requirement"],
            "masters_requirement_raw": requirements["masters_requirement"],
            "doctorate_requirement_raw": requirements["doctorate_requirement"],
            "page": page_numbers[page_index] if page_numbers else None,
            "reference": f"Seq. {int(match.group('seq'))}",
            "campus": clean_text(match.group("campus")).title(),
            "vacancies_count": None,
            "reserve_only": True,
            "requirements_complete": True,
        })
    unique: dict[str, dict[str, Any]] = {}
    for item in opportunities:
        unique[normalize_text(f"{item['reference']}|{item['campus']}|{item['area']}")] = item
    return list(unique.values())


def extract_structured_html_opportunities(html_bytes: bytes) -> list[dict[str, Any]]:
    """Read one row per vacancy from a labelled requirements table.

    This deliberately requires both an area/cargo column and a requirements
    column.  It is therefore useful for official multi-vacancy pages without
    turning arbitrary layout tables into positions.  UFSCar's edital is a
    large HTML document whose first column combines code, cargo, department
    and campus; that documented shape is handled explicitly here.
    """
    soup = BeautifulSoup(html_bytes, "html.parser")
    opportunities: list[dict[str, Any]] = []
    for table in soup.select("table"):
        rows = table.find_all("tr", recursive=False)
        if not rows:
            rows = table.select("tr")
        header_index = None
        headers: list[str] = []
        for index, row in enumerate(rows[:5]):
            candidate = [normalize_text(cell.get_text(" ", strip=True)) for cell in row.find_all(("th", "td"), recursive=False)]
            joined = " | ".join(candidate)
            if any(term in joined for term in ("requisito", "escolaridade", "titulacao minima")) and any(
                term in joined for term in ("area", "cargo", "funcao", "disciplina")
            ):
                header_index, headers = index, candidate
                break
        if header_index is None:
            continue

        def column(*terms: str) -> int | None:
            for i, value in enumerate(headers):
                if any(term in value for term in terms):
                    return i
            return None

        requirement_i = column("requisito", "escolaridade", "titulacao minima", "formacao exigida")
        area_i = column("area", "disciplina")
        cargo_i = column("cargo", "funcao")
        combined_i = next((i for i, value in enumerate(headers) if "codigo" in value and "cargo" in value), None)
        if requirement_i is None or (area_i is None and cargo_i is None):
            continue
        vacancies_i = column("vaga")
        subarea_i = column("subarea", "sub-area")
        campus_i = column("campus", "local")
        workload_i = column("regime", "jornada", "carga horaria")
        reference_i = column("codigo", "referencia", "subedital")

        for row in rows[header_index + 1:]:
            cells = row.find_all(("th", "td"), recursive=False)
            if len(cells) < len(headers):
                continue
            values = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
            requirement_text = values[requirement_i] if requirement_i < len(values) else ""
            area = values[area_i] if area_i is not None and area_i < len(values) else ""
            cargo = values[cargo_i] if cargo_i is not None and cargo_i < len(values) else ""
            reference = values[reference_i] if reference_i is not None and reference_i < len(values) else None
            campus = values[campus_i] if campus_i is not None and campus_i < len(values) else None
            department = None
            position = cargo or None
            if combined_i is not None and combined_i < len(cells):
                parts = [clean_text(item) for item in cells[combined_i].stripped_strings if clean_text(item)]
                if parts:
                    reference = parts[0]
                if len(parts) >= 2:
                    position = parts[1]
                if len(parts) >= 3:
                    department = parts[2]
                if len(parts) >= 4:
                    campus = parts[-1]
            if len(area) < 2 or len(requirement_text) < 3:
                continue
            if not re.search(r"\b(?:professor|docente|magisterio)\b", normalize_text(
                f"{position or ''} {cargo} {soup.title.get_text(' ', strip=True) if soup.title else ''}"
            )):
                continue
            requirements = extract_requirement_fields(f"Requisitos: {requirement_text}")
            count_match = re.search(r"\d+", values[vacancies_i]) if vacancies_i is not None and vacancies_i < len(values) else None
            item = {
                "area": area,
                "subarea": values[subarea_i] if subarea_i is not None and subarea_i < len(values) else None,
                "position": position,
                "department": department,
                "requirement_text": requirement_text,
                "graduation_requirement_raw": requirements["graduation_requirement"],
                "postgraduate_requirement_raw": requirements["postgraduate_requirement"],
                "masters_requirement_raw": requirements["masters_requirement"],
                "doctorate_requirement_raw": requirements["doctorate_requirement"],
                "reference": reference,
                "campus": campus,
                "workload": values[workload_i] if workload_i is not None and workload_i < len(values) else None,
                "vacancies_count": int(count_match.group(0)) if count_match else None,
                "requirements_complete": True,
            }
            opportunities.append(item)

    unique: dict[str, dict[str, Any]] = {}
    for item in opportunities:
        key = normalize_text(f"{item.get('reference')}|{item['area']}|{item['requirement_text']}")
        unique[key] = item
    return list(unique.values())


def is_ufscar_portal_vacancy(vacancy: Mapping[str, Any]) -> bool:
    context = normalize_text(" ".join(str(vacancy.get(field) or "") for field in (
        "institution", "title", "official_url", "institution_url",
    )))
    return "ufscar" in context or "universidade federal de sao carlos" in context


def score_candidate_link(label: str, url: str, vacancy: Mapping[str, Any]) -> int:
    normalized_label = normalize_text(label)
    normalized_url = normalize_text(url)
    combined = f"{normalized_label} {normalized_url}"
    score = 0
    # A link that carries this vacancy's own edital number is the document, not
    # a candidate for it. Nothing else on an institution's site competes.
    numbers = known_edital_numbers(vacancy)
    if numbers:
        found = {
            f"{int(m.group(1))}/{m.group(2)[-2:]}" for m in LOOSE_IDENTIFIER.finditer(combined)
        }
        if numbers & found:
            score += 120
    if urlsplit(url).path.lower().endswith(".pdf") or "pdf" in normalized_label:
        score += 25
    if "edital" in combined:
        score += 45
    if any(term in combined for term in ("concurso", "processo-seletivo", "processo seletivo", "selecao")):
        score += 24
    if "inscricoes" in combined or "inscricao" in combined:
        score += 24
    if any(term in combined for term in ("professor", "docente", "magisterio")):
        score += 20
    # Archive pages often list years of identically labelled selections.  The
    # active year's entry must outrank lexicographically earlier old URLs.
    if str(date.today().year) in combined:
        score += 30
    score += min(30, 6 * sum(token in combined for token in vacancy_context_tokens(vacancy)))
    if any(term in combined for term in config.DEPRIORITIZED_LINK_TERMS):
        # Not disqualifying: a small city hall may announce its edital in a news
        # item. Just never ahead of a link that names the edital itself.
        score -= 30
    if any(term in combined for term in (
        "resultado", "gabarito", "homologacao", "isencao", "login",
        "vestibular", "premio", "bolsa", "residencia", "licitacao",
        "fale conosco", "meus concursos", "register", "password",
        "procuracao", "recurso", "requerimento", "comprovacao",
        "impugnacao", "formulario", "sorteio", "transmissao",
    )):
        score -= 120
    return score


def is_excluded_link(url: str, base_host: str = "") -> bool:
    """Reject links that cannot hold an edital, before they cost an access.

    The crawler's budget is the binding constraint — 79% of vacancies spent it
    in full — so an access wasted on a podcast episode or a login form is an
    edital not read. Everything here was observed being followed in production.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host:
        return True
    if any(host == domain or host.endswith(f".{domain}") for domain in config.EXCLUDED_LINK_DOMAINS):
        return True
    if host.split(".")[0] in config.EXCLUDED_LINK_SUBDOMAINS:
        return True
    path = (parts.path or "").lower().rstrip("/")
    if any(path == item or path.startswith(item + "/") for item in config.EXCLUDED_LINK_PATHS):
        return True
    # PCI is the discovery source, not a document host: its study-guide and
    # listing pages were being followed as if they were institutional sources.
    if host.endswith("pciconcursos.com.br") and "/noticias/" not in path:
        return True
    return False


def extract_candidate_links(html_bytes: bytes, base_url: str, vacancy: Mapping[str, Any]) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html_bytes, "html.parser")
    candidates: dict[str, dict[str, Any]] = {}
    base_host = (urlsplit(base_url).hostname or "").lower()
    if base_host == "pciconcursos.com.br" or base_host.endswith(".pciconcursos.com.br"):
        anchors = soup.select(
            'article#noticia [itemprop="articleBody"] a[href], '
            'article#noticia a.edital-pdf-link[href]'
        )
    else:
        anchors = soup.select("a[href]")
    for anchor_order, anchor in enumerate(anchors):
        href = anchor.get("href", "").strip()
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = canonical_url(href, base_url)
        if url == canonical_url(base_url) or urlsplit(url).scheme not in ("http", "https"):
            continue
        if is_excluded_link(url, base_host):
            continue
        label = clean_text(" ".join((anchor.get_text(" ", strip=True), anchor.get("title", ""))))
        context_node = anchor.find_parent(("p", "li"))
        context = clean_text(context_node.get_text(" ", strip=True) if context_node else label)[:500]
        score = score_candidate_link(f"{label} {context}", url, vacancy)
        if score < 20:
            continue
        current = candidates.get(url)
        if current is None or score > current["score"]:
            candidates[url] = {
                "url": url, "label": label, "score": score,
                "source_order": current.get("source_order", anchor_order) if current else anchor_order,
            }
    # Institution archive pages normally present the newest selection first.
    # Preserve that source order for equal scores instead of sorting old numeric
    # URLs ahead of the active process.
    return sorted(candidates.values(), key=lambda item: (-item["score"], item["source_order"]))


def should_check_official(cache_entry: Mapping[str, Any] | None, today: date) -> bool:
    if not cache_entry:
        return True
    if cache_entry.get("reader_version") != config.OFFICIAL_READER_VERSION:
        return True
    checked = cache_entry.get("checked_at")
    try:
        age = (today - date.fromisoformat(str(checked)[:10])).days
    except ValueError:
        return True
    status = cache_entry.get("status")
    threshold = (
        config.OFFICIAL_RECHECK_AFTER_DAYS
        if status in ("READ", "READ_MULTI")
        else config.OFFICIAL_RETRY_AFTER_DAYS
    )
    return age >= threshold


class OfficialDocumentReader:
    def __init__(self, session: requests.Session, delay: float | None = None):
        self.session = session
        self.delay = config.OFFICIAL_REQUEST_DELAY_SECONDS if delay is None else delay
        self._last_request_at = 0.0

    def _request(self, url: str) -> tuple[requests.Response, bool]:
        """Fetch with verification; retry unverified only for a missing intermediate.

        Several Brazilian universities — UNESP, UNICAMP, UFMG among them — serve
        a perfectly valid certificate but omit the intermediate, so the chain
        cannot be built. Browsers paper over this by fetching the intermediate
        themselves; requests does not, and the edital becomes unreadable.

        The retry is deliberately narrow. "Unable to get local issuer" means the
        chain is incomplete; an expired, self-signed or wrong-hostname
        certificate means the server's identity is genuinely in question, and
        those stay refused. Anything read this way is marked, surfaced in the
        page, and never allowed to produce a confident verdict.
        """
        kwargs = dict(timeout=config.REQUEST_TIMEOUT_SECONDS, stream=True, allow_redirects=True)
        try:
            return self.session.get(url, **kwargs), False
        except requests.exceptions.SSLError as exc:
            if not config.OFFICIAL_ALLOW_INCOMPLETE_CHAIN:
                raise
            if "unable to get local issuer certificate" not in str(exc):
                raise
            LOGGER.warning("Cadeia TLS incompleta em %s; relendo sem verificação e marcando", url)
            return self.session.get(url, verify=False, **kwargs), True

    def _fetch(self, url: str) -> tuple[bytes, str, str, bool]:
        if not is_public_http_url(url):
            raise OfficialReadError("URL oficial recusada por validação de segurança")
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at and elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        LOGGER.info("Consultando fonte oficial: %s", url)
        response, tls_unverified = self._request(url)
        self._last_request_at = time.monotonic()
        for item in [*response.history, response]:
            if not is_public_http_url(item.url):
                raise OfficialReadError("Redirecionamento oficial recusado por validação de segurança")
        response.raise_for_status()
        declared = int(response.headers.get("Content-Length") or 0)
        if declared > config.OFFICIAL_MAX_DOCUMENT_BYTES:
            raise OfficialReadError("Documento oficial excede o limite configurado")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            size += len(chunk)
            if size > config.OFFICIAL_MAX_DOCUMENT_BYTES:
                raise OfficialReadError("Documento oficial excede o limite configurado")
            chunks.append(chunk)
        data = b"".join(chunks)
        if not data:
            raise OfficialReadError("Documento oficial vazio")
        return data, response.url, response.headers.get("Content-Type", "").lower(), tls_unverified

    def _fetch_post(self, url: str, form_data: Mapping[str, str]) -> tuple[bytes, str, str]:
        """Fetch an official document exposed only through a public HTML form."""
        if not is_public_http_url(url):
            raise OfficialReadError("URL oficial recusada por validação de segurança")
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at and elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        LOGGER.info("Consultando formulário oficial: %s", url)
        response = self.session.post(
            url, data=dict(form_data), timeout=config.REQUEST_TIMEOUT_SECONDS,
            stream=True, allow_redirects=True,
        )
        self._last_request_at = time.monotonic()
        for item in [*response.history, response]:
            if not is_public_http_url(item.url):
                raise OfficialReadError("Redirecionamento oficial recusado por validação de segurança")
        response.raise_for_status()
        declared = int(response.headers.get("Content-Length") or 0)
        if declared > config.OFFICIAL_MAX_DOCUMENT_BYTES:
            raise OfficialReadError("Documento oficial excede o limite configurado")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            size += len(chunk)
            if size > config.OFFICIAL_MAX_DOCUMENT_BYTES:
                raise OfficialReadError("Documento oficial excede o limite configurado")
            chunks.append(chunk)
        data = b"".join(chunks)
        if not data:
            raise OfficialReadError("Documento oficial vazio")
        return data, response.url, response.headers.get("Content-Type", "").lower()

    def _read_ufscar(self, vacancy: Mapping[str, Any], checked_at: datetime) -> dict[str, Any] | None:
        """Follow UFSCar's POST-only portal to its single HTML edital.

        The public portal represents every useful route as JavaScript that
        submits a form.  A normal link crawler consequently sees only the home
        page.  One campus list leads to a detail page, whose ``arquivo`` form
        exposes the unified edital containing the complete vacancy table.
        """
        base = "https://concursos.ufscar.br/"
        list_url = urljoin(base, "lista.php")
        detail_url = urljoin(base, "detalhe.php")
        archive_url = urljoin(base, "arquivo.php")
        position = normalize_text(str(vacancy.get("position") or ""))
        preferred_type = "2" if "substitut" in position else "1"
        types = list(dict.fromkeys((preferred_type, "1", "2", "6")))
        cargo_id = None
        list_document = None
        for type_id in types:
            for campus_id in ("1", "2", "3", "4", "7"):
                data, _, _ = self._fetch_post(list_url, {
                    "status": "1", "tipo": type_id, "campus": campus_id,
                })
                soup = BeautifulSoup(data, "html.parser")
                match = next((
                    re.search(r"concurso\((\d+)\)", anchor.get("href", ""), re.I)
                    for anchor in soup.select('a[href*="concurso("]')
                ), None)
                if match:
                    cargo_id = match.group(1)
                    list_document = data
                    break
            if cargo_id:
                break
        if not cargo_id:
            return None

        detail, _, _ = self._fetch_post(detail_url, {"idCargo": cargo_id})
        detail_soup = BeautifulSoup(detail, "html.parser")
        archive_match = None
        for anchor in detail_soup.select("a[href]"):
            if normalize_text(anchor.get_text(" ", strip=True)) != "edital":
                continue
            archive_match = re.search(
                r"arquivo\(\s*(\d+)\s*,\s*(\d+)\s*\)", anchor.get("href", ""), re.I
            )
            if archive_match:
                break
        if not archive_match:
            return None

        edital, _, content_type = self._fetch_post(archive_url, {
            "idArquivo": archive_match.group(1), "versao": archive_match.group(2),
        })
        opportunities = extract_structured_html_opportunities(edital)
        if len(opportunities) < 2:
            return None
        edital_soup = BeautifulSoup(edital, "html.parser")
        edital_text = clean_text(edital_soup.get_text(" ", strip=True))
        registration_start, registration_end = parse_registration_period(edital_text)
        digest = hashlib.sha256(edital).hexdigest()
        documents = [{
            "url": base,
            "type": "HTML_FORM",
            "content_hash": digest,
            "content_type": content_type,
            "page_count": 1,
            "extracted_chars": len(edital_text),
            "truncated": False,
            "title": clean_text(edital_soup.title.get_text(" ", strip=True)) if edital_soup.title else "Edital UFSCar",
            "relevant": True,
            "relevance_reason": "Edital único recuperado do formulário público oficial da UFSCar.",
            "opportunities_count": len(opportunities),
            "form_reference": f"arquivo {archive_match.group(1)}, versão {archive_match.group(2)}",
        }]
        if list_document:
            documents.insert(0, {
                "url": list_url, "type": "HTML_FORM_INDEX",
                "content_hash": hashlib.sha256(list_document).hexdigest(),
                "relevant": True,
                "relevance_reason": "Lista oficial de cargos em fase de inscrição.",
            })
        return {
            "status": "READ_MULTI",
            "checked_at": checked_at.isoformat(timespec="seconds"),
            "reader_version": config.OFFICIAL_READER_VERSION,
            "documents": documents,
            "document_url": None,
            "document_type": "HTML",
            "content_hash": digest,
            "confidence": "STRUCTURED",
            "applicable": False,
            "opportunities": opportunities,
            "registration_start": registration_start,
            "registration_end": registration_end,
            "reason": (
                f"Edital único em HTML lido no portal oficial da UFSCar; "
                f"{len(opportunities)} sub-vagas extraídas da tabela de requisitos mínimos."
            ),
            "errors": [],
            "pci_protected_documents": [],
        }

    @staticmethod
    def _extract_pdf_pages(data: bytes) -> tuple[list[tuple[int, str]], dict[str, Any]]:
        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise OfficialReadError("PDF oficial criptografado") from exc
        page_count = len(reader.pages)
        pages: list[tuple[int, str]] = []
        extracted_chars = 0
        truncated = page_count > config.OFFICIAL_MAX_PDF_PAGES
        for number, page in enumerate(reader.pages[: config.OFFICIAL_MAX_PDF_PAGES], start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                LOGGER.warning("Falha ao extrair página %s do PDF: %s", number, exc)
                text = ""
            remaining = config.OFFICIAL_MAX_EXTRACTED_CHARS - extracted_chars
            if remaining <= 0:
                truncated = True
                break
            text = text[:remaining]
            extracted_chars += len(text)
            pages.append((number, text))
        metadata_title = None
        try:
            metadata_title = clean_text(str(reader.metadata.title or "")) if reader.metadata else None
        except Exception:
            pass
        return pages, {
            "page_count": page_count, "extracted_chars": extracted_chars,
            "truncated": truncated, "title": metadata_title,
        }

    @staticmethod
    def _extract_html_page(data: bytes) -> tuple[list[tuple[int, str]], str | None, bool]:
        soup = BeautifulSoup(data, "html.parser")
        for node in soup.select("script, style, noscript, nav, footer"):
            node.decompose()
        title = clean_text(soup.title.get_text(" ", strip=True)) if soup.title else None
        text = clean_text(soup.get_text(" ", strip=True))[: config.OFFICIAL_MAX_EXTRACTED_CHARS]
        normalized = normalize_text(text)
        blocked = any(term in normalized for term in ("captcha", "cloudflare turnstile", "verificacao de seguranca")) and len(text) < 5000
        return [(1, text)], title, blocked

    def read(self, vacancy: Mapping[str, Any], checked_at: datetime) -> dict[str, Any]:
        special_errors: list[str] = []
        if is_ufscar_portal_vacancy(vacancy):
            try:
                special = self._read_ufscar(vacancy, checked_at)
                if special:
                    return special
            except Exception as exc:
                special_errors.append(f"UFSCar portal: {type(exc).__name__}: {exc}")
                LOGGER.warning("Falha no leitor do portal UFSCar: %s", exc)
        seeds = []
        source_path = urlsplit(str(vacancy.get("source_url") or "")).path.rstrip("/")
        override = OFFICIAL_SEED_OVERRIDES.get(source_path)
        if override:
            seeds.append(override)
        for document in vacancy.get("pci_documents") or []:
            value = document.get("url")
            if value and value not in seeds:
                seeds.append(str(value))
        for field in ("source_url", "official_url", "institution_url"):
            value = vacancy.get(field)
            if value and value not in seeds:
                seeds.append(str(value))
        if not seeds:
            return {
                "status": "NO_LINK", "checked_at": checked_at.isoformat(timespec="seconds"),
                "reader_version": config.OFFICIAL_READER_VERSION,
                "documents": [], "applicable": False,
                "reason": "A vaga não possui URL do PCI ou fonte oficial consultável.",
            }

        canonical_seeds = [canonical_url(url) for url in seeds]
        # A priority frontier, not a queue. With a deque, the eight links a
        # generic institution homepage offers are explored before the second
        # seed is ever tried, so the budget is spent on whatever that one page
        # happened to link to. Ordering globally by score means the most
        # promising candidate found anywhere goes next.
        order = itertools.count()
        frontier: list[tuple[int, int, str, int]] = []
        for index, url in enumerate(canonical_seeds):
            heapq.heappush(frontier, (-SEED_PRIORITY + index, next(order), url, 0))
        queued = set(canonical_seeds)
        visited: set[str] = set()
        documents: list[dict[str, Any]] = []
        best: dict[str, Any] | None = None
        best_multi: dict[str, Any] | None = None
        errors: list[str] = special_errors
        blocked = False
        pci_protected_documents: list[dict[str, Any]] = []

        while frontier and len(visited) < config.OFFICIAL_MAX_LINKS_PER_VACANCY:
            _, _, url, depth = heapq.heappop(frontier)
            if url in visited:
                continue
            visited.add(url)
            try:
                data, final_url, content_type, tls_unverified = self._fetch(url)
                digest = hashlib.sha256(data).hexdigest()
                is_pdf = data.startswith(b"%PDF-") or "application/pdf" in content_type
                if is_pdf:
                    pages, metadata = self._extract_pdf_pages(data)
                    registration_start, registration_end = parse_registration_period(
                        "\n".join(page_text for _, page_text in pages)
                    )
                    relevant, relevance_reason = assess_document_relevance(pages, vacancy, final_url)
                    structured = extract_structured_opportunities(pages) if relevant else []
                    evidence = (
                        extract_requirement_evidence(pages, vacancy, allow_unscoped=True)
                        if relevant else {
                            "applicable": False, "confidence": "IRRELEVANT", "requirements": {},
                            "evidence": [], "reason": relevance_reason,
                        }
                    )
                    document = {
                        "url": final_url, "type": "PDF", "content_hash": digest,
                        **metadata, "evidence_status": evidence["confidence"],
                        "tls_unverified": tls_unverified,
                        "relevant": relevant, "relevance_reason": relevance_reason,
                        "opportunities_count": len(structured),
                        "registration_start": registration_start,
                        "registration_end": registration_end,
                    }
                    if len(structured) > 1 and (
                        not vacancy.get("area")
                        or normalize_text(str(vacancy.get("area"))) == "nao identificada"
                        or len(vacancy.get("pci_opportunities") or []) > 1
                    ):
                        if best_multi is None or len(structured) > len(best_multi["opportunities"]):
                            best_multi = {
                                "document": document, "opportunities": structured,
                                "reason": "Edital multiárea lido; os blocos serão avaliados como sub-vagas independentes.",
                            }
                elif "html" in content_type or data.lstrip().startswith((b"<!DOCTYPE", b"<html", b"<HTML")):
                    pages, title, page_blocked = self._extract_html_page(data)
                    registration_start, registration_end = parse_registration_period(pages[0][1])
                    blocked = blocked or page_blocked
                    final_host = (urlsplit(final_url).hostname or "").lower()
                    is_pci_news = (
                        (final_host == "pciconcursos.com.br" or final_host.endswith(".pciconcursos.com.br"))
                        and "/noticias/" in urlsplit(final_url).path
                    )
                    if is_pci_news:
                        pci_refs = extract_pci_document_references(
                            BeautifulSoup(data, "html.parser"), final_url
                        )
                        known_refs = {
                            f"{item.get('pci_news_code')}:{item.get('pci_link_id')}"
                            for item in pci_protected_documents
                        }
                        for item in pci_refs:
                            ref_key = f"{item.get('pci_news_code')}:{item.get('pci_link_id')}"
                            if item.get("access") == "HUMAN_VERIFICATION_REQUIRED" and ref_key not in known_refs:
                                pci_protected_documents.append(item)
                                known_refs.add(ref_key)
                        relevant, relevance_reason = False, "Notícia do PCI usada somente para localizar o edital."
                    else:
                        relevant, relevance_reason = assess_document_relevance(pages, vacancy, final_url)
                    structured = extract_structured_html_opportunities(data) if relevant else []
                    evidence = (
                        extract_requirement_evidence(pages, vacancy, allow_unscoped=False)
                        if relevant else {
                            "applicable": False, "confidence": "IRRELEVANT", "requirements": {},
                            "evidence": [], "reason": relevance_reason,
                        }
                    )
                    document = {
                        "url": final_url, "type": "PCI_HTML" if is_pci_news else "HTML", "content_hash": digest,
                        "page_count": 1, "extracted_chars": len(pages[0][1]),
                        "truncated": False, "title": title,
                        "evidence_status": evidence["confidence"],
                        "tls_unverified": tls_unverified,
                        "relevant": relevant, "relevance_reason": relevance_reason,
                        "opportunities_count": len(structured),
                        "registration_start": registration_start,
                        "registration_end": registration_end,
                    }
                    if len(structured) > 1 and (
                        not vacancy.get("area")
                        or normalize_text(str(vacancy.get("area"))) == "nao identificada"
                        or len(vacancy.get("pci_opportunities") or []) > 1
                    ):
                        if best_multi is None or len(structured) > len(best_multi["opportunities"]):
                            best_multi = {
                                "document": document, "opportunities": structured,
                                "reason": "Página oficial multiárea lida; a tabela será exibida como sub-vagas independentes.",
                            }
                    if not page_blocked and depth < config.OFFICIAL_MAX_DEPTH:
                        links = extract_candidate_links(data, final_url, vacancy)
                        for item in links:
                            candidate = item["url"]
                            if candidate not in visited and candidate not in queued:
                                heapq.heappush(
                                    frontier, (-int(item["score"]), next(order), candidate, depth + 1)
                                )
                                queued.add(candidate)
                else:
                    documents.append({
                        "url": final_url, "type": "UNSUPPORTED", "content_hash": digest,
                        "content_type": content_type, "evidence_status": "NONE",
                    })
                    continue
                documents.append(document)
                if (
                    override
                    and canonical_url(final_url) == canonical_url(override)
                    and len(structured) > 1
                ):
                    break
                if evidence["applicable"]:
                    rank = (
                        {"HIGH": 3, "MEDIUM": 2}.get(evidence["confidence"], 0),
                        1 if document["type"] == "PDF" else 0,
                        len(evidence["requirements"]),
                    )
                    if best is None or rank > best["rank"]:
                        best = {
                            "rank": rank, "document": document,
                            "requirements": evidence["requirements"],
                            "evidence": evidence["evidence"], "reason": evidence["reason"],
                            "confidence": evidence["confidence"],
                        }
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}: {exc}")
                LOGGER.warning("Falha na leitura oficial de %s: %s", url, exc)

        checked = checked_at.isoformat(timespec="seconds")
        if best_multi:
            return {
                "status": "READ_MULTI", "checked_at": checked,
                "reader_version": config.OFFICIAL_READER_VERSION,
                "documents": documents, "document_url": best_multi["document"]["url"],
                "document_type": best_multi["document"]["type"], "content_hash": best_multi["document"]["content_hash"],
                "confidence": "STRUCTURED", "applicable": False,
                "tls_unverified": bool(best_multi["document"].get("tls_unverified")),
                "opportunities": best_multi["opportunities"],
                "registration_start": best_multi["document"].get("registration_start"),
                "registration_end": best_multi["document"].get("registration_end"),
                "reason": best_multi["reason"], "errors": errors[:5],
                "pci_protected_documents": pci_protected_documents,
            }
        if best:
            return {
                "status": "READ", "checked_at": checked, "documents": documents,
                "reader_version": config.OFFICIAL_READER_VERSION,
                "document_url": best["document"]["url"],
                "document_type": best["document"]["type"],
                "content_hash": best["document"]["content_hash"],
                "confidence": best["confidence"], "applicable": True,
                "tls_unverified": bool(best["document"].get("tls_unverified")),
                "requirements": best["requirements"], "evidence": best["evidence"],
                "registration_start": best["document"].get("registration_start"),
                "registration_end": best["document"].get("registration_end"),
                "reason": best["reason"], "errors": errors[:5],
                "pci_protected_documents": pci_protected_documents,
            }
        if pci_protected_documents:
            status, reason = (
                "BLOCKED",
                f"O PCI lista {len(pci_protected_documents)} edital(is), mas só libera os PDFs após verificação humana; fontes alternativas também foram tentadas.",
            )
        elif blocked:
            status, reason = "BLOCKED", "A fonte oficial exige verificação humana; nenhum bloqueio foi contornado."
        elif any(item.get("type") == "PDF" and not item.get("extracted_chars") for item in documents):
            status, reason = "NO_TEXT", "O edital parece ser digitalizado e não contém camada de texto extraível."
        elif documents:
            status, reason = "AMBIGUOUS", "Documentos consultados, mas os requisitos não puderam ser associados com segurança à vaga."
        else:
            status, reason = "ERROR", "Não foi possível ler uma fonte oficial pública."
        return {
            "status": status, "checked_at": checked, "documents": documents,
            "reader_version": config.OFFICIAL_READER_VERSION,
            "applicable": False, "reason": reason, "errors": errors[:5],
            "pci_protected_documents": pci_protected_documents,
        }
