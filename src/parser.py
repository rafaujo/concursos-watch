"""Pure parsing helpers for Brazilian dates and PCI detail pages."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .requirements import extract_requirement_fields


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
    return extract_requirement_fields(text)


TEACHING_ROLE = re.compile(
    r"\b(?:professor(?:a)?|docente|instrutor(?:a)?|regente\s+de\s+classe)\b", re.I
)

# Titles PCI puts before the discipline. Stripping them leaves the course, which
# is what a reader actually filters by.
ROLE_PREFIX = re.compile(
    r"^(?:professor(?:a)?|docente|instrutor(?:a)?)"
    r"(?:\s+(?:licenciad[oa]|habilitad[oa]|n[aã]o\s+habilitad[oa]|substitut[oa]|"
    r"tempor[aá]ri[oa]|titular|adjunt[oa]|assistente|doutor(?:a)?|visitante|"
    r"colaborador(?:a)?|auxiliar|com|de\s+n[ií]vel\s+\w+|"
    r"(?:d[ao]\s+)?(?:carreira\s+d[ao]\s+)?magist[eé]rio(?:\s+superior)?|"
    r"/pesquisador(?:a)?|do\s+ensino\s+superior))*"
    r"\s*(?::?\s*[aá]rea\s*[-–:]\s*)?"
    r"(?:\s*(?:de|da|do|das|dos|em|na|no|para)\s+)?"
    r"(?:[aá]reas?\s+de\s+)?",
    re.I,
)

VACANCY_HINT = re.compile(
    r"\(\s*(?P<count>\d+)\s*vagas?(?P<reserve>\s*\+\s*(?:CR|cadastro\s+de\s+reserva))?\s*\)|"
    r"\(\s*(?P<only_reserve>CR|cadastro\s+de\s+reserva)\s*\)",
    re.I,
)


# PCI groups municipal teaching posts into "Área I/II/III"; the roman numeral is
# the grouping, not the course the reader filters by.
AREA_GROUP = re.compile(r"^[aá]rea\s+(?:[IVXLC]+|\d+)\s*[-–/:]\s*", re.I)

# Some municipalities number the rows of their cargo table ("Professor 1 - Arte").
ITEM_NUMBER = re.compile(r"^\d{1,3}\s*[-–.)]\s*")

# What is left when the cargo names a career level or a shift rather than a
# subject: "Professor II", "Professor 20h". These are not courses, and when they
# appear the discipline is usually in the parenthetical instead.
DEGENERATE_COURSE = re.compile(
    r"^(?:[IVX]{1,4}|\d{1,3}|\d{1,3}\s*h(?:oras)?|[a-z]{1,2})$", re.I
)

# A parenthetical naming the required qualification, as opposed to one naming
# the discipline. "(Magistério)" qualifies; "(Educação Física)" is the subject.
FORMATION_HINT = re.compile(
    r"licenciatur|bacharel|magist[eé]rio|gradua[cç][aã]o|p[oó]s\b|especializa|"
    r"mestrado|doutorado|forma[cç][aã]o|habilita[cç][aã]o|n[ií]vel\s+(?:m[eé]dio|superior)|"
    r"curso\s+(?:superior|normal)|aperfei[cç]oamento",
    re.I,
)


def _strip_role_prefix(value: str) -> str:
    """Remove the role wrapper, possibly nested ("Professor Licenciado: Área - Professor de X")."""
    course = value
    for _ in range(3):
        reduced = ROLE_PREFIX.sub("", course, count=1).strip(" -–:;,.")
        if reduced == course or not reduced:
            break
        course = reduced
        if not TEACHING_ROLE.match(course):
            break
    course = SECTION_HEADING.sub("", course).strip(" -–:;,.")
    course = AREA_GROUP.sub("", course).strip(" -–:;,.")
    return ITEM_NUMBER.sub("", course).strip(" -–:;,.")


def parse_cargo_item(text: str, *, require_role: bool = True) -> dict[str, Any] | None:
    """Turn one PCI cargo bullet into a structured teaching vacancy.

    Returns None for non-teaching cargos so a mixed notice (Enfermeiro,
    Psicólogo, Professor de Geografia) yields only the teaching rows. Set
    require_role=False for a list the notice itself introduced as teaching
    areas, where the items are bare subjects and no role word appears.
    """
    label = clean_text(text)
    if not label or len(label) > 220:
        return None
    if require_role and not TEACHING_ROLE.search(label):
        return None

    vacancies_count = None
    reserve = False
    hint_match = VACANCY_HINT.search(label)
    if hint_match:
        if hint_match.group("count"):
            vacancies_count = int(hint_match.group("count"))
            reserve = bool(hint_match.group("reserve"))
        else:
            reserve = True

    # Every trailing parenthetical that is not a vacancy count describes the
    # required qualification, e.g. "(Licenciatura Curta)", "(Magistério)".
    parentheticals = [
        clean_text(item) for item in re.findall(r"\(([^()]{2,90})\)", label)
        if not VACANCY_HINT.fullmatch(f"({item.strip()})")
    ]
    bare = clean_text(re.sub(r"\([^()]*\)", " ", label))
    course = _strip_role_prefix(bare)

    # Separate the parentheticals that state a qualification from the ones that
    # state the subject. "Professor II (Educação Física)" names its discipline
    # there, and without this the level ("II") would be filed as the course.
    hints = [item for item in parentheticals if FORMATION_HINT.search(item)]
    subjects = [item for item in parentheticals if not FORMATION_HINT.search(item)]
    if not course or DEGENERATE_COURSE.match(course):
        # "Professor II (Educação Física)" states its subject in the
        # parenthetical; "Professor 20h (5 vagas)" states none anywhere, and
        # inventing one would put "20h" in the course filter as if it were a
        # discipline. Leave it empty and let the cargo label carry the row.
        course = subjects[0] if subjects else None

    return {
        "cargo": label,
        "course": course,
        "requirement_hint": " / ".join(dict.fromkeys(hints)) or None,
        "vacancies_count": vacancies_count,
        "reserve_only": reserve and vacancies_count is None,
    }


CARGO_LIST_MARKER = re.compile(
    r"(?:as oportunidades s[aã]o para|as vagas s[aã]o para|o edital contempla)\s+"
    r"(?:os?\s+cargos?\s+de|as?\s+(?:seguintes\s+)?(?P<areas>[aá]reas?))\s*:\s*",
    re.I,
)

# In flattened prose every cargo ends with its own count or reserve marker, so
# the closing parenthesis is a reliable boundary between one and the next.
CARGO_TEXT_BOUNDARY = re.compile(
    r"(.{3,180}?\(\s*(?:\d+\s*vagas?(?:\s*\+\s*(?:CR|cadastro de reserva))?|"
    r"CR|cadastro de reserva)\s*\))",
    re.I,
)


# Headings that group a list of areas — UFRN separates "Magistério Superior"
# from "EBTT" — and end up glued to the first item once the markup is gone.
SECTION_HEADING = re.compile(
    r"^(?:magist[eé]rio\s+superior|ebtt|ensino\s+b[aá]sico,?\s+t[eé]cnico\s+e\s+tecnol[oó]gico|"
    r"quadro\s+(?:de\s+)?(?:cargos|vagas)|[aá]reas?\s+de\s+atua[cç][aã]o)\s+(?=[A-ZÀ-Ú])",
    re.I,
)

# Where the cargo list stops and the notice resumes its prose.
LIST_END = re.compile(
    r"\s+(?:A\s+jornada|A\s+carga\s+hor[aá]ria|As\s+inscri[cç][oõ]es|A\s+remunera[cç][aã]o|"
    r"O\s+sal[aá]rio|Os?\s+sal[aá]rios|O\s+contrato|A\s+sele[cç][aã]o|O\s+processo\s+seletivo\s+ser[aá]|"
    r"O\s+edital\s+(?:completo|est[aá])|As\s+provas|Mais\s+informa[cç][oõ]es)\b"
)

# Each cargo in a flattened list begins with its role word.
ROLE_BOUNDARY = re.compile(r"(?=\bProfessor(?:a)?\b|\bDocente\b|\bInstrutor(?:a)?\b)", re.I)


def _before_trailing_prose(body: str) -> str:
    """Stop where the notice goes back to talking about the selection."""
    match = LIST_END.search(body)
    return body[:match.start()] if match else body


def extract_pci_opportunities_from_text(raw_text: Any) -> list[dict[str, Any]]:
    """Recover the cargo list from prose when the markup is unavailable.

    The <li> reading below is better and stays the first choice, but it only
    runs when a notice is fetched again. Records stored before it existed keep
    their flattened raw_text, and this recovers their cargos at render time
    instead of leaving the vacancy unexpanded until its recheck window expires.
    """
    text = clean_text(str(raw_text or ""))
    marker = CARGO_LIST_MARKER.search(text)
    if not marker:
        return []

    # A list introduced as "áreas" belongs entirely to this teaching selection —
    # UFRN writes "as seguintes áreas: Demografia (Cadastro de reserva)" with no
    # role word anywhere, and demanding one there loses every item. A list of
    # "cargos" is different: municipal notices mix Merendeira and Motorista into
    # it, so there the role word is what separates teaching from the rest.
    areas_mode = bool(marker.group("areas"))
    body = _before_trailing_prose(text[marker.end():])

    chunks = [match.group(1) for match in CARGO_TEXT_BOUNDARY.finditer(body)]
    if not chunks:
        # No count or reserve marker anywhere: Catanduva lists "Professor
        # Berçarista Professor I Professor II - Arte" with nothing between the
        # items, so the role word is the only boundary left.
        chunks = [body]

    opportunities: list[dict[str, Any]] = []
    seen: set[str] = set()
    for chunk in chunks:
        # One chunk can hold several cargos when the ones before the last carry
        # no count of their own, and it can start with an edital heading. Both
        # are separated by the role word.
        pieces = [chunk] if areas_mode else ROLE_BOUNDARY.split(chunk)
        for piece in pieces:
            parsed = parse_cargo_item(piece.strip(" .;,-–"), require_role=not areas_mode)
            if not parsed:
                continue
            key = normalize_text(parsed["cargo"])
            if key in seen:
                continue
            seen.add(key)
            opportunities.append(parsed)
    return opportunities


def extract_pci_opportunities(body: Any) -> list[dict[str, Any]]:
    """Read the cargo list PCI renders as <li> items in the news body.

    Flattening the body into prose destroys these boundaries, which is how a
    whole position table used to end up inside one requirement field.
    """
    if body is None:
        return []
    opportunities: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in body.select("li"):
        if item.select_one("a.edital-pdf-link"):
            continue
        parsed = parse_cargo_item(item.get_text(" ", strip=True))
        if not parsed:
            continue
        key = normalize_text(parsed["cargo"])
        if key in seen:
            continue
        seen.add(key)
        opportunities.append(parsed)
    return opportunities


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
        context = normalize_text(anchor.parent.get_text(" ", strip=True) if anchor.parent else "")
        if any(term in f"{label} {context}" for term in ("inscri", "edital", "concurso", "selecao")):
            official_url = official_url or href
        else:
            institution_url = institution_url or href
    return official_url, institution_url


def extract_pci_document_references(container: Any, source_url: str) -> list[dict[str, Any]]:
    """Capture PCI edital controls without attempting to bypass human verification."""
    references: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in container.select("a.edital-pdf-link"):
        href = anchor.get("href", "").strip()
        direct_url = None
        if href.startswith(("http://", "https://", "/")):
            direct_url = urljoin(source_url, href)
        link_id = anchor.get("data-link")
        news_code = anchor.get("data-code")
        key = direct_url or f"{news_code}:{link_id}"
        if not key or key in seen:
            continue
        seen.add(key)
        references.append({
            "label": clean_text(anchor.get_text(" ", strip=True) or anchor.get("title", "Edital")),
            "url": direct_url,
            "pci_link_id": link_id,
            "pci_news_code": news_code,
            "access": "DIRECT" if direct_url else "HUMAN_VERIFICATION_REQUIRED",
        })
    return references


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
    pci_opportunities = extract_pci_opportunities(body)
    body_text = clean_text(body.get_text(" ", strip=True))
    description_node = article.select_one('[itemprop="description"]')
    description = clean_text(description_node.get_text(" ", strip=True)) if description_node else ""
    full_text = clean_text(" ".join((title, description, body_text)))
    requirements = extract_requirement_sentences(body_text)
    start, end = parse_registration_period(body_text)
    official_url, institution_url = _external_links(body, source_url)
    pci_documents = extract_pci_document_references(article, source_url)
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
        "pci_documents": pci_documents,
        "pci_opportunities": pci_opportunities,
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
        "postgraduate_requirement_raw": requirements["postgraduate_requirement"],
        "masters_requirement_raw": requirements["masters_requirement"],
        "doctorate_requirement_raw": requirements["doctorate_requirement"],
        "other_requirements": None,
    }
