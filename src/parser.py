"""Pure parsing helpers for Brazilian dates and PCI detail pages."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Iterable
from urllib.parse import urlparse

from bs4 import BeautifulSoup


MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}


def normalize_text(value: str | None) -> str:
    """Normalize text for matching while leaving stored display text untouched."""
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value).strip().lower()


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def parse_brazilian_dates(text: str, default_year: int | None = None) -> list[date]:
    """Extract all valid dates from common Brazilian date expressions."""
    if not text:
        return []
    default_year = default_year or date.today().year
    normalized = normalize_text(text)
    found: list[tuple[int, date]] = []

    numeric = re.compile(r"(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?!\d)")
    for match in numeric.finditer(normalized):
        try:
            found.append((match.start(), date(int(match.group(3)), int(match.group(2)), int(match.group(1)))))
        except ValueError:
            continue

    written = re.compile(
        r"(?<!\d)(\d{1,2})\s+de\s+(" + "|".join(MONTHS) + r")(?:\s+de\s+(\d{4}))?"
    )
    written_matches = list(written.finditer(normalized))
    explicit_years = [int(m.group(3)) for m in written_matches if m.group(3)]
    inferred_year = explicit_years[-1] if explicit_years else default_year
    for match in written_matches:
        try:
            year = int(match.group(3)) if match.group(3) else inferred_year
            found.append((match.start(), date(year, MONTHS[match.group(2)], int(match.group(1)))))
        except ValueError:
            continue

    unique: list[date] = []
    for _, parsed in sorted(found, key=lambda item: item[0]):
        if parsed not in unique:
            unique.append(parsed)
    return unique


def parse_first_date(text: str) -> str | None:
    dates = parse_brazilian_dates(text)
    return dates[0].isoformat() if dates else None


def parse_registration_period(text: str) -> tuple[str | None, str | None]:
    """Prefer dates in sentences that discuss applications/registration."""
    candidates = re.split(r"(?<=[.!?])\s+|\n+", clean_text(text))
    relevant = [s for s in candidates if re.search(r"inscri|candidat", normalize_text(s))]
    dates: list[date] = []
    for sentence in relevant:
        dates.extend(d for d in parse_brazilian_dates(sentence) if d not in dates)
    if not dates:
        return None, None
    if len(dates) == 1:
        return None, dates[0].isoformat()
    return dates[0].isoformat(), dates[-1].isoformat()


def extract_requirement_sentences(text: str) -> dict[str, str | None]:
    sentences = re.split(r"(?<=[.!?;])\s+|\n+", clean_text(text))

    def select(terms: Iterable[str]) -> str | None:
        matches = []
        for sentence in sentences:
            normalized = normalize_text(sentence)
            if any(term in normalized for term in terms) and any(
                cue in normalized
                for cue in ("exig", "requis", "titul", "gradu", "formacao", "doutor", "mestre")
            ):
                matches.append(clean_text(sentence))
        return " ".join(dict.fromkeys(matches)) or None

    return {
        "graduation_requirement": select(("graduacao", "graduado", "bacharel", "formacao superior")),
        "masters_requirement": select(("mestrado", "mestre")),
        "doctorate_requirement": select(("doutorado", "doutor")),
    }


def _external_links(article: Any, source_url: str) -> tuple[str | None, str | None]:
    institution_url = None
    official_url = None
    source_host = urlparse(source_url).netloc
    for anchor in article.select("a[href]"):
        href = anchor.get("href", "").strip()
        if not href.startswith(("http://", "https://")):
            continue
        host = urlparse(href).netloc
        if source_host in host or "pci.app.br" in host:
            continue
        label = normalize_text(anchor.get_text(" ", strip=True))
        if any(term in label for term in ("inscri", "edital", "concurso", "selecao")):
            official_url = official_url or href
        else:
            institution_url = institution_url or href
    return official_url, institution_url


def _find_money(text: str) -> str | None:
    values = re.findall(r"R\$\s*[\d.]+,\d{2}(?:\s+por\s+[\w-]+)?", text, re.I)
    return " a ".join(dict.fromkeys(values)) if values else None


def _find_workload(text: str) -> str | None:
    match = re.search(r"\b(\d{1,3})\s*horas?(?:\s+semanais)?\b", text, re.I)
    return clean_text(match.group(0)) if match else None


def _infer_area(text: str) -> tuple[str, str | None]:
    patterns = (
        r"(?:\bna\s+)?(?:área|area)\s+(?:é\s+|e\s+|de\s+)?([^.;()]{3,120}?)(?=\s*,\s*sub(?:área|area)|\s*\(|[.;]|$)",
        r"(?:disciplina|departamento)\s+(?:de\s+)?([^.;()]{3,100}?)(?=\s*\(|[.;]|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            area = clean_text(match.group(1))
            nested = re.split(r"\s+na\s+(?:área|area)\s+de\s+", area, flags=re.I)
            area = clean_text(nested[-1])
            if normalize_text(area) in {"atuacao", "especifica", "conhecimento", "conhecimentos"}:
                continue
            subarea_match = re.search(r"sub(?:área|area)\s+(?:de\s+)?([^.;()]{2,80}?)(?=\s*\(|[.;]|$)", text, re.I)
            subarea = clean_text(subarea_match.group(1)) if subarea_match else None
            return area, subarea
    return "Não identificada", None


def _infer_position(text: str, fallback: str = "Professor") -> str:
    match = re.search(
        r"\b(Professor(?:a)?(?:\s+(?:Substituto|Titular|Adjunto|Assistente|Doutor|do Magistério Superior))?)\b",
        text,
        re.I,
    )
    return clean_text(match.group(1)) if match else fallback


def parse_pci_detail(html: str, source_url: str, listing: dict[str, Any] | None = None) -> dict[str, Any]:
    listing = listing or {}
    soup = BeautifulSoup(html, "html.parser")
    article = soup.select_one("article#noticia")
    if article is None:
        raise ValueError("PCI detail parser could not find article#noticia")
    headline = article.select_one('[itemprop="headline"]')
    body = article.select_one('[itemprop="articleBody"]')
    if headline is None or body is None:
        raise ValueError("PCI detail parser found an incomplete news article")

    title = clean_text(headline.get_text(" ", strip=True))
    body_text = clean_text(body.get_text(" ", strip=True))
    description_node = article.select_one('[itemprop="description"]')
    description = clean_text(description_node.get_text(" ", strip=True)) if description_node else ""
    full_text = clean_text(" ".join((title, description, body_text)))
    requirements = extract_requirement_sentences(body_text)
    start, end = parse_registration_period(body_text)
    official_url, institution_url = _external_links(body, source_url)
    area, subarea = _infer_area(body_text)

    published = article.select_one("abbr.published[title]")
    publication_date = None
    if published:
        try:
            publication_date = datetime.fromisoformat(published["title"]).date().isoformat()
        except (ValueError, TypeError):
            publication_date = parse_first_date(published.get_text(" ", strip=True))

    vacancy_match = re.search(r"(\d+)\s+vagas?", body_text, re.I)
    city = None
    state = listing.get("state")
    city_state = re.search(r"\b(?:campus\s+de\s+)?([A-ZÁ-Ú][\wÀ-ÿ' -]{2,50})\s*/\s*([A-Z]{2})\b", body_text)
    if city_state:
        city, state = clean_text(city_state.group(1)), city_state.group(2)

    return {
        "title": title,
        "description": description,
        "raw_text": body_text,
        "institution": listing.get("institution") or title.split(" abre ", 1)[0].split(" publica ", 1)[0],
        "institution_url": institution_url,
        "official_url": official_url,
        "campus": clean_text(re.search(r"campus\s+(?:de\s+)?([^.,;]{2,60})", body_text, re.I).group(1))
        if re.search(r"campus\s+(?:de\s+)?([^.,;]{2,60})", body_text, re.I) else None,
        "state": state or "",
        "city": city,
        "position": _infer_position(full_text, listing.get("position") or "Professor"),
        "area": area,
        "subarea": subarea,
        "employment_type": "Processo seletivo" if "processo seletivo" in normalize_text(full_text) else "Concurso público",
        "workload": _find_workload(body_text),
        "salary": None,
        "salary_text": _find_money(body_text) or listing.get("salary_text"),
        "registration_start": start,
        "registration_end": end or listing.get("registration_end"),
        "publication_date": publication_date,
        "vacancies_count": int(vacancy_match.group(1)) if vacancy_match else listing.get("vacancies_count"),
        **requirements,
        "graduation_requirement_raw": requirements["graduation_requirement"],
        "masters_requirement_raw": requirements["masters_requirement"],
        "doctorate_requirement_raw": requirements["doctorate_requirement"],
        "other_requirements": None,
    }
