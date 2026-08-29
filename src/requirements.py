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
        if not runs or runs[-1]["category"] != category:
            runs.append({"category": category, "start": match.start()})

    result: dict[str, list[str]] = {"graduation": [], "postgraduate": []}
    for index, run in enumerate(runs):
        end = runs[index + 1]["start"] if index + 1 < len(runs) else len(text)
        segment = text[run["start"]:end]
        segment = re.sub(
            r"[,;:]?\s*(?:(?:e|ou)\s+)?(?:al[eé]m\s+de\s+|com\s+)?$",
            "",
            segment,
            flags=re.I,
        ).strip(" .;,:")
        if segment and segment not in result[run["category"]]:
            result[run["category"]].append(segment)
    return result


def graduation_for_display(value: str) -> str:
    """Drop only the leading degree label; preserve modalities and alternatives."""
    return re.sub(
        r"^(?:(?<!p[oó]s[- ])gradua[cç][aã]o|graduad[oa]s?|bacharelado|licenciatura|"
        r"curso\s+superior|forma[cç][aã]o\s+superior)\s*"
        r"(?:(?:com\s+habilita[cç][aã]o\s+)?(?:em|nas?\s+[aá]reas?\s+de|na\s+[aá]rea\s+de)\s*)?",
        "",
        value,
        flags=re.I,
    ).strip(" .;,:")


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
