"""Conservative separation of undergraduate and postgraduate requirements."""

from __future__ import annotations

import re
import unicodedata
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


DEGREE = re.compile(
    r"\b(gradua[cç][aã]o|licenciatura|bacharelado|curso superior|forma[cç][aã]o superior|"
    r"especializa[cç][aã]o|p[oó]s[- ]?gradua[cç][aã]o|mestrado|doutorado|p[oó]s[- ]doutorado|"
    r"resid[eê]ncia m[eé]dica|t[ií]tulo de mestre|t[ií]tulo de doutor|grau de mestre|grau de doutor)\b",
    re.I,
)
CONNECTOR = re.compile(
    r"^\s*(?:plena|curta|completa|integral|lato\s+sensu|stricto\s+sensu|"
    r"com\s+habilita[cç][aã]o)?\s*"
    r"(?:em|na[s]?\s+[aá]reas?(?:\s+de)?|de|do|da)\s*",
    re.I,
)
STOP = re.compile(
    r"\s+(?:com|conforme|desde que|sendo|obtido|reconhecid|expedid|na forma|nos termos|"
    r"h[aá]\s+\d|para\s+o\s+cargo|e\s+registro|R\$|\d{2}h\b|acompanhad|o\s+disposto|§|nos\s+termos|previst|conforme\s+o\s+art)",
    re.I,
)
# Degrees joined before a shared field: "Mestrado e Doutorado em Música".
CHAINED_DEGREE = re.compile(r"\s*(?:,|e|ou|e/ou)\s*", re.I)

TOC = re.compile(r"\.{4,}|\s\d{2,4}\s*$|sum[aá]rio", re.I)
BAD_AREA = re.compile(r"^(?:mesmo|o disposto|os? candidatos?|a[s]? qual|conforme|refer)", re.I)
NAV = re.compile(
    r"vestibular|portal da universidade|pr[oó][- ]reitor|assist[eê]ncia estudantil|alojamento|"
    r"\bEAD\b|bolsas|todas as unidades|coordenadoria|ingressar|biblioteca|ouvidoria|"
    r"programas de p[oó]s[- ]gradua[cç][aã]o\b.{0,40}\b(?:resid[eê]ncia|especializa)",
    re.I,
)
TABLE = re.compile(r"R\$\s*[\d.]+,\d{2}.*\b\d+\b|\b\d{2}h\b\s+\d")

CANON = {
    "graduacao": "Graduação", "licenciatura": "Licenciatura", "bacharelado": "Bacharelado",
    "curso superior": "Graduação", "formacao superior": "Graduação",
    "especializacao": "Especialização", "pos-graduacao": "Pós-graduação",
    "posgraduacao": "Pós-graduação", "pos graduacao": "Pós-graduação",
    "mestrado": "Mestrado", "doutorado": "Doutorado", "pos-doutorado": "Pós-doutorado",
    "residencia medica": "Residência médica",
    "titulo de mestre": "Mestrado", "titulo de doutor": "Doutorado",
    "grau de mestre": "Mestrado", "grau de doutor": "Doutorado",
}


def normalize_text_simple(value: str) -> str:
    value = unicodedata.normalize("NFKD", (value or "").lower())
    return "".join(c for c in value if not unicodedata.combining(c)).strip()


def _canon(word: str) -> str:
    k = unicodedata.normalize("NFKD", word.lower())
    k = "".join(c for c in k if not unicodedata.combining(c))
    return CANON.get(k, word.capitalize())


def condense_requirement(value: str | None, *, keep_degree: bool = True) -> str | None:
    """Reduce a requirement to the degree and its field, dropping the rest.

    A reader scanning a list needs "Mestrado em Educação ou áreas afins", not
    the paragraph around it. Values that are not requirements at all — a
    university site's navigation menu, a salary table, a table of contents —
    return None rather than a shortened version of nonsense.

    keep_degree=False drops the degree word itself, for a column already
    titled "Requisito de graduação": repeating it there is noise. In the
    postgraduate column the word carries the information — mestrado and
    doutorado are different requirements — so it stays.
    """
    if not value or value == "Não informado":
        return value
    if NAV.search(value) or TABLE.search(value) or TOC.search(value):
        return None
    parts, seen = [], set()
    consumed = 0
    matches = list(DEGREE.finditer(value))
    for index, match in enumerate(matches):
        # "X ou Doutorado em Y" is one requirement with alternatives, not two.
        # A degree already inside the previous item's area is part of it.
        if match.start() < consumed:
            continue
        degree = _canon(match.group(1))
        end = match.end()
        # "Mestrado e Doutorado em Música" states one requirement whose field is
        # shared: splitting it would leave the first degree with no area at all.
        for following in matches[index + 1:]:
            joiner = value[end:following.start()]
            if not CHAINED_DEGREE.fullmatch(joiner):
                break
            degree = f"{degree}{joiner}{_canon(following.group(1))}"
            end = following.end()
            consumed = end
        # Never read past where the next degree begins: its field belongs to
        # it, not to this one.
        stop_at = len(value)
        for following in matches[index + 1:]:
            if following.start() >= end:
                stop_at = following.start()
                break
        tail = value[end:stop_at]
        conn = CONNECTOR.match(tail)
        if not conn:
            # "Licenciatura Curta" names the degree with a modifier and no
            # field. Reducing it to "Licenciatura" would drop the only thing
            # that distinguishes it, so this degree's own phrase is kept — up
            # to where the next degree begins, never the whole value.
            phrase = value[match.start():stop_at].strip(" .;,:/-–")
            stop = STOP.search(phrase)
            if stop:
                phrase = phrase[:stop.start()].strip(" .;,:/-–")
            # Just the degree with nothing added: use the canonical spelling
            # rather than whatever case the source happened to use.
            if normalize_text_simple(phrase) == normalize_text_simple(match.group(1)):
                phrase = degree
            item = phrase if 0 < len(phrase) <= 60 else degree
        else:
            rest = tail[conn.end():]
            stop = STOP.search(rest)
            area = (rest[:stop.start()] if stop else rest).strip(" .,;:/-–()")
            area = re.sub(r"\s+", " ", area)
            if BAD_AREA.match(area):
                area = ""
            if len(area) > 80:
                area = area[:80].rsplit(" ", 1)[0] + "…"
            consumed = end + conn.end() + len(area)
            if not keep_degree and area:
                item = area
            else:
                item = f"{degree} em {area}" if area else degree
        if item.lower() not in seen:
            seen.add(item.lower())
            parts.append(item)
    # A bare degree alongside the same degree with a field is the same
    # requirement stated twice; the specific one is the useful one.
    parts = [
        item for item in parts
        if not any(other != item and other.lower().startswith(item.lower() + " em") for other in parts)
    ]
    if not parts:
        # No degree word at all. That is the normal shape for an already
        # stripped graduation ("Administração") and for a cargo's own hint
        # ("Pós ou cursos de aperfeiçoamento"). Both are short and were not
        # rejected as junk above, so keeping them beats discarding them; only
        # a long value with no degree in it is something we cannot vouch for.
        return value if len(value) <= 90 else None
    return " · ".join(parts[:3])
