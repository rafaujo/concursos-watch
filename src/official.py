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
    employment_markers = (
        "concurso publico para professor", "concurso para professor",
        "processo seletivo simplificado", "teste seletivo",
        "selecao para professor", "selecao de professor", "vaga para professor",
        "cargo de professor", "magisterio superior", "docente temporario",
    )
    if not any(marker in text for marker in employment_markers):
        return False, "O documento não foi identificado como seleção para cargo docente."

    identifier_pattern = re.compile(
        r"(?:edital|concurso)\s*(?:n(?:o|º|°)?\.?\s*)?(\d{1,5}(?:\s*[/.-]\s*\d{2,4})?)",
        re.I,
    )
    vacancy_text = " ".join(str(vacancy.get(key) or "") for key in ("title", "raw_text", "description"))
    identifiers = {normalize_text(match) for match in identifier_pattern.findall(vacancy_text)}
    document_identifiers = {normalize_text(match) for match in identifier_pattern.findall(text)}
    if identifiers & document_identifiers:
        return True, "Número do edital/concurso coincide com o anúncio do PCI."

    area = normalize_text(str(vacancy.get("area") or ""))
    if area and area != "nao identificada":
        area_tokens = _tokens(area)
        overlap = sorted(token for token in area_tokens if token in text)
        if overlap:
            return True, f"Documento docente associado à área por: {', '.join(overlap[:5])}."
        return False, "O documento docente não menciona a área identificada no anúncio."

    title_tokens = _tokens(" ".join(str(vacancy.get(key) or "") for key in ("title", "description")))
    normalized_url = normalize_text(document_url)
    overlap = sorted(token for token in title_tokens if token in text or token in normalized_url)
    if len(overlap) >= 2:
        return True, f"Documento docente associado ao anúncio por: {', '.join(overlap[:5])}."
    return False, "A vaga não tem área/número identificável e o documento não coincide com contexto suficiente."


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
    opportunities: list[dict[str, Any]] = []
    marker = re.compile(
        r"(?:área|area)\s+de\s+conhecimento\s+ou\s+mat[eé]ria\(s\)\s+",
        re.I,
    )
    end_marker = re.compile(r"tipos?\s+de\s+prova", re.I)
    for page_number, page_text in pages:
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
    return list(unique.values())


def score_candidate_link(label: str, url: str, vacancy: Mapping[str, Any]) -> int:
    normalized_label = normalize_text(label)
    normalized_url = normalize_text(url)
    combined = f"{normalized_label} {normalized_url}"
    score = 0
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
    score += min(30, 6 * sum(token in combined for token in vacancy_context_tokens(vacancy)))
    if any(term in combined for term in (
        "resultado", "gabarito", "homologacao", "isencao", "login",
        "vestibular", "premio", "bolsa", "residencia", "licitacao",
        "fale conosco", "meus concursos", "register", "password",
        "procuracao", "recurso", "requerimento", "comprovacao",
        "impugnacao", "formulario", "sorteio", "transmissao",
    )):
        score -= 120
    return score


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
    for anchor in anchors:
        href = anchor.get("href", "").strip()
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = canonical_url(href, base_url)
        if url == canonical_url(base_url) or urlsplit(url).scheme not in ("http", "https"):
            continue
        host = (urlsplit(url).hostname or "").lower()
        if host == "x.com" or any(
            host == domain or host.endswith(f".{domain}")
            for domain in (
                "twitter.com", "facebook.com", "linkedin.com", "whatsapp.com",
                "instagram.com", "youtube.com", "youtu.be", "t.me",
            )
        ):
            continue
        label = clean_text(" ".join((anchor.get_text(" ", strip=True), anchor.get("title", ""))))
        context_node = anchor.find_parent(("p", "li"))
        context = clean_text(context_node.get_text(" ", strip=True) if context_node else label)[:500]
        score = score_candidate_link(f"{label} {context}", url, vacancy)
        if score < 20:
            continue
        current = candidates.get(url)
        if current is None or score > current["score"]:
            candidates[url] = {"url": url, "label": label, "score": score}
    return sorted(candidates.values(), key=lambda item: (-item["score"], item["url"]))


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
        seeds = []
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
        errors: list[str] = []
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
                    }
                    if len(structured) > 1 and (
                        not vacancy.get("area")
                        or normalize_text(str(vacancy.get("area"))) == "nao identificada"
                    ):
                        if best_multi is None or len(structured) > len(best_multi["opportunities"]):
                            best_multi = {
                                "document": document, "opportunities": structured,
                                "reason": "Edital multiárea lido; os blocos serão avaliados como sub-vagas independentes.",
                            }
                elif "html" in content_type or data.lstrip().startswith((b"<!DOCTYPE", b"<html", b"<HTML")):
                    pages, title, page_blocked = self._extract_html_page(data)
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
                "reason": best["reason"], "errors": errors[:5],
                "pci_protected_documents": pci_protected_documents,
            }
        if best_multi:
            return {
                "status": "READ_MULTI", "checked_at": checked,
                "reader_version": config.OFFICIAL_READER_VERSION,
                "documents": documents, "document_url": best_multi["document"]["url"],
                "document_type": "PDF", "content_hash": best_multi["document"]["content_hash"],
                "confidence": "STRUCTURED", "applicable": False,
                "tls_unverified": bool(best_multi["document"].get("tls_unverified")),
                "opportunities": best_multi["opportunities"],
                "reason": best_multi["reason"], "errors": errors[:5],
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
