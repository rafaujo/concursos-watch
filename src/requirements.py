"""Conservative separation of undergraduate and postgraduate requirements."""

from __future__ import annotations

import re
from typing import Any


GRADUATION_MARKER = re.compile(
    r"(?P<graduation>"
    r"(?<!p[oó]s[- ])\bgradua[cç][aã]o\b|"
    r"\bgraduad[oa]s?\b|"
    r"\bbacharelado\b|"
    r"\blicenciatura\b|"
    r"\bcurso\s+superior\b|"
    r"\bforma[cç][aã]o\s+superior\b"
    r")",
    re.I,
)

POSTGRADUATE_MARKER = re.compile(
    r"(?P<postgraduate>"
    r"\bmestrado\b|\bdoutorado\b|\bt[ií]tulo\s+de\s+mestre\b|"
    r"\bt[ií]tulo\s+de\s+doutor\b|\bgrau\s+de\s+mestre\b|\bgrau\s+de\s+doutor\b|"
    r"\bp[oó]s[- ]doutorado\b|"
    r"\bespecializa[cç][aã]o\b|\bresid[eê]ncia\s+m[eé]dica\b|"
    r"\bt[ií]tulo\s+de\s+especialista\b|\bp[oó]s[- ]gradua[cç][aã]o\b"
    r")",
    re.I,
)

ACADEMIC_MARKER = re.compile(
    f"(?:{GRADUATION_MARKER.pattern}|{POSTGRADUATE_MARKER.pattern})",
    re.I,
)

# A requirement statement is a phrase, not a document. These bounds stop a run
# of same-level markers from swallowing an entire enumerated list of cargos,
# which is how PCI renders multi-position notices once the <li> markup is
# flattened into prose.
MAX_SEGMENT_CHARS = 300
MAX_RUN_GAP_CHARS = 90
CARGO_ENUMERATION = re.compile(
    r"\b(?:professor(?:a)?|coordenador(?:a)?|instrutor(?:a)?|monitor(?:a)?)\b", re.I
)
# "(Cadastro de Reserva)" / "(CR)" only ever appears in a position table, never
# inside a prose statement of the required qualification.
RESERVE_MARKER = re.compile(r"\(\s*(?:cadastro\s+de\s+reserva|cr)\s*\)", re.I)
MAX_CARGO_MENTIONS = 1


def looks_like_cargo_list(segment: str) -> bool:
    """Detect an enumerated list of positions masquerading as one requirement."""
    if RESERVE_MARKER.search(segment):
        return True
    return len(CARGO_ENUMERATION.findall(segment)) > MAX_CARGO_MENTIONS


# Where the requirement ends and the rest of the edital begins. These openings
# start a new subject — pay, hours, how to apply — and everything from there on
# belongs to the notice, not to the qualification.
NEXT_SUBJECT = re.compile(
    r"\s+(?:A\s+carga\s+hor[aá]ria|As\s+inscri[cç][oõ]es|A\s+remunera[cç][aã]o|"
    r"O\s+sal[aá]rio|O\s+prazo|O\s+vencimento|A\s+jornada|O\s+contrato|"
    r"A\s+prova|As\s+provas|O\s+edital\s+(?:est[aá]|pode)|A\s+sele[cç][aã]o\s+ser[aá])\b"
)

# A value that ends in a one or two letter fragment was cut mid-word by an
# upstream bound; showing "Graduação e/ou d" is worse than showing nothing.
TRUNCATED_TAIL = re.compile(r"(?:^|\s)\S{1,2}$")


def _cut_at_next_subject(segment: str) -> str:
    match = NEXT_SUBJECT.search(segment)
    return segment[:match.start()] if match else segment


def _trim_to_boundary(segment: str, limit: int = MAX_SEGMENT_CHARS) -> str:
    """Cut an over-long segment back to the last clean separator."""
    if len(segment) <= limit:
        return segment
    window = segment[:limit]
    cut = max(window.rfind(";"), window.rfind(","), window.rfind(" "))
    return window[:cut] if cut > 0 else window


def clean_requirement_context(value: Any) -> str:
    """Remove headings and trailing application text without rewriting evidence."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    title_marker = re.search(r"titula[cç][aã]o\s+m[ií]nima\s+exigida\s*:\s*", text, re.I)
    if title_marker:
        text = text[title_marker.end():]
    text = re.sub(r"\bInscri[cç][oõ]es?\s*:.*$", "", text, flags=re.I)
    text = re.sub(r"^(?:Requisito\(s\)|Requisitos?|Titula[cç][aã]o)\s*:?\s*", "", text, flags=re.I)
    return text.strip(" .;,:")


def _valid_markers(text: str) -> list[tuple[re.Match[str], str]]:
    markers: list[tuple[re.Match[str], str]] = []
    for match in ACADEMIC_MARKER.finditer(text):
        category = "graduation" if match.group("graduation") else "postgraduate"
        if category == "postgraduate" and re.fullmatch(
            r"p[oó]s[- ]gradua[cç][aã]o", match.group(0), re.I
        ):
            prefix = text[max(0, match.start() - 24):match.start()]
            if re.search(r"programa\s+de\s*$", prefix, re.I):
                continue
        markers.append((match, category))
    return markers


def split_academic_requirement(value: Any) -> dict[str, list[str]]:
    """Split one requirement statement into ordered qualification runs.

    Consecutive markers of the same level remain together, so expressions such
    as ``Mestrado e Doutorado em Música`` are not torn apart. Alternating
    constructions keep every branch, for example graduation/post/grad/post.
    """
    text = clean_requirement_context(value)
    markers = _valid_markers(text)
    if not markers:
        return {"graduation": [], "postgraduate": []}

    runs: list[dict[str, Any]] = []
    for match, category in markers:
        distant = bool(runs) and match.start() - runs[-1]["last_end"] > MAX_RUN_GAP_CHARS
        if not runs or runs[-1]["category"] != category or distant:
            runs.append({"category": category, "start": match.start(), "last_end": match.end()})
        else:
            runs[-1]["last_end"] = match.end()

    result: dict[str, list[str]] = {"graduation": [], "postgraduate": []}
    for index, run in enumerate(runs):
        end = runs[index + 1]["start"] if index + 1 < len(runs) else len(text)
        segment = _trim_to_boundary(text[run["start"]:end])
        segment = re.sub(
            r"[,;:]?\s*(?:(?:e|ou)\s+)?(?:al[eé]m\s+de\s+|com\s+)?$",
            "",
            segment,
            flags=re.I,
        ).strip(" .;,:")
        segment = _cut_at_next_subject(segment).strip(" .;,:")
        if not segment or looks_like_cargo_list(segment):
            continue
        if TRUNCATED_TAIL.search(segment) and len(segment.split()) > 1:
            segment = segment.rsplit(" ", 1)[0].strip(" .;,:/-–e")
            if not segment:
                continue
        if segment not in result[run["category"]]:
            result[run["category"]].append(segment)
    return result


# "Licenciatura Curta" / "Licenciatura Plena" name the degree itself, so
# dropping the label would leave a meaningless "Curta".
# A leading conjunction means the label was one alternative among several
# ("Licenciatura ou Pós"), so removing it would strand the rest.
DEGREE_MODIFIER = re.compile(
    r"^(?:curta|plena|completa|integral|espec[ií]fica|m[ií]nima|"
    r"ou|e|em\s+qualquer\s+[aá]rea)\b",
    re.I,
)


def graduation_for_display(value: str) -> str:
    """Drop only the leading degree label; preserve modalities and alternatives."""
    stripped = re.sub(
        r"^(?:(?<!p[oó]s[- ])gradua[cç][aã]o|graduad[oa]s?|bacharelado|licenciatura|"
        r"curso\s+superior|forma[cç][aã]o\s+superior)\s*"
        r"(?:(?:com\s+habilita[cç][aã]o\s+)?(?:em|nas?\s+[aá]reas?\s+de|na\s+[aá]rea\s+de)\s*)?",
        "",
        value,
        flags=re.I,
    ).strip(" .;,:")
    if DEGREE_MODIFIER.match(stripped):
        return value.strip(" .;,:")
    return stripped


def extract_requirement_fields(text: Any) -> dict[str, str | None]:
    """Extract separated raw fields from prose without matching program names."""
    sentences = re.split(r"(?<=[.!?;])\s+|\n+", re.sub(r"\s+", " ", str(text or "")).strip())
    graduation: list[str] = []
    postgraduate: list[str] = []
    masters: list[str] = []
    doctorate: list[str] = []

    for sentence in sentences:
        parts = split_academic_requirement(sentence)
        for value in parts["graduation"]:
            if value not in graduation:
                graduation.append(value)
        for value in parts["postgraduate"]:
            if value not in postgraduate:
                postgraduate.append(value)
            if re.search(r"\bmestrado\b|\bt[ií]tulo\s+de\s+mestre\b|\bgrau\s+de\s+mestre\b", value, re.I):
                if value not in masters:
                    masters.append(value)
            if re.search(r"\bdoutorado\b|\bt[ií]tulo\s+de\s+doutor\b|\bgrau\s+de\s+doutor\b", value, re.I):
                if value not in doctorate:
                    doctorate.append(value)

    join = lambda values: " / ".join(values) or None
    return {
        "graduation_requirement": join(graduation),
        "postgraduate_requirement": join(postgraduate),
        "masters_requirement": join(masters),
        "doctorate_requirement": join(doctorate),
    }
