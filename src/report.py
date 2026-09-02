"""Generate a dependency-free, responsive GitHub Pages report."""

from __future__ import annotations

import html
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import config
from .classifier import institution_type
from .official import edital_numbers_for_display
from .parser import extract_pci_opportunities_from_text, parse_cargo_item
from .requirements import (
    clean_requirement_context,
    condense_requirement,
    extract_requirement_fields,
    graduation_for_display,
    split_academic_requirement,
)


ELIGIBILITY_LABELS = {
    "YES": "Elegível em triagem",
    "NO": "Formalmente incompatível",
    "UNCERTAIN": "Elegibilidade incerta",
    "UNKNOWN": "Informação insuficiente",
}

STATUS_LABELS = {
    "NEW": "Novo",
    "UPDATED": "Atualizado",
    "OPEN": "Aberto",
    "CLOSING_SOON": "Encerrando em breve",
    "CLOSED": "Encerrado",
}


def _escape(value: Any, fallback: str = "Não informado") -> str:
    if value is None or value == "":
        value = fallback
    return html.escape(str(value), quote=True)


def _format_date(value: str | None) -> str:
    if not value:
        return "Não informada"
    try:
        return date.fromisoformat(value).strftime("%d/%m/%Y")
    except ValueError:
        return value


def _is_recently_closed(vacancy: dict[str, Any], today: date) -> bool:
    if vacancy.get("status") != "CLOSED":
        return True
    try:
        end = date.fromisoformat(vacancy.get("registration_end", ""))
        return (today - end).days <= config.CLOSED_VISIBLE_DAYS
    except ValueError:
        return False


def _sort_key(vacancy: dict[str, Any]) -> tuple[Any, ...]:
    eligibility = {"YES": 0, "UNCERTAIN": 1, "UNKNOWN": 2, "NO": 3}
    deadline = vacancy.get("registration_end") or "9999-12-31"
    return (
        0 if vacancy.get("status") == "NEW" else 1,
        eligibility.get(vacancy.get("formal_eligibility"), 4),
        -int(vacancy.get("thematic_score") or 0),
        int(vacancy.get("geographic_priority") or 4),
        deadline,
    )


def _expanded_rows(vacancies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn structured multi-area notices into one row per vacancy in the notice."""
    rows: list[dict[str, Any]] = []
    for vacancy in vacancies:
        opportunities = list(vacancy.get("official_opportunities") or [])
        if vacancy.get("official_check_status") == "READ_MULTI" and opportunities:
            for opportunity in opportunities:
                row = {
                    **vacancy,
                    **opportunity,
                    "_is_subvacancy": True,
                    "_parent_title": vacancy.get("title"),
                    "title": opportunity.get("area"),
                    "area": opportunity.get("area"),
                    "formal_eligibility": opportunity.get("formal_eligibility", "UNKNOWN"),
                    "formal_reason": opportunity.get("formal_reason"),
                    "thematic_score": opportunity.get("thematic_score", 0),
                    "thematic_reason": opportunity.get("thematic_reason"),
                    "visual_category": opportunity.get("visual_category"),
                }
                for field in (
                    "graduation_requirement", "graduation_requirement_raw",
                    "postgraduate_requirement", "postgraduate_requirement_raw",
                    "masters_requirement", "masters_requirement_raw",
                    "doctorate_requirement", "doctorate_requirement_raw",
                ):
                    row[field] = opportunity.get(field)
                rows.append(row)
        elif vacancy.get("pci_opportunities"):
            rows.extend(_pci_opportunity_rows(vacancy))
        elif extract_pci_opportunities_from_text(vacancy.get("raw_text")):
            # Stored before the cargo reader existed. Recovering the list from
            # the prose here means the vacancy expands on the next build rather
            # than waiting weeks for its recheck window.
            rows.extend(_pci_opportunity_rows({
                **vacancy,
                "pci_opportunities": extract_pci_opportunities_from_text(vacancy.get("raw_text")),
            }))
        else:
            rows.append({**vacancy, "_is_subvacancy": False, "_parent_title": vacancy.get("title")})
    return rows


def _pci_opportunity_rows(vacancy: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per teaching cargo listed in the PCI notice.

    The notice never states eligibility per cargo, so each row inherits the
    contest-level classification unchanged rather than inventing one. What the
    row does carry is its own course, which is what the reader filters by.
    """
    from_official = str(vacancy.get("requirements_source") or "").startswith("OFFICIAL_")
    rows: list[dict[str, Any]] = []
    for stored in vacancy.get("pci_opportunities") or []:
        cargo = str(stored.get("cargo") or "").strip()
        if not cargo:
            continue
        # The cargo label is the evidence; everything else is derived from it.
        # Re-deriving here means a parser correction reaches the page on the
        # next build, instead of waiting for each notice's recheck window —
        # the same reason the PCI prose above is reparsed at render time.
        opportunity = parse_cargo_item(cargo) or stored
        # A cargo whose notice never names a discipline ("Professor 20h") still
        # deserves its row; it just stays out of the course filter rather than
        # appearing there as a subject it is not.
        course = str(opportunity.get("course") or "").strip()
        rows.append({
            **vacancy,
            "_is_subvacancy": True,
            "_parent_title": vacancy.get("title"),
            "title": cargo,
            "area": course or cargo,
            "course": course or None,
            "position": opportunity.get("cargo") or vacancy.get("position"),
            "requirement_text": opportunity.get("requirement_hint"),
            "vacancies_count": opportunity.get("vacancies_count"),
            "reference": "Cadastro de reserva" if opportunity.get("reserve_only") else None,
            "pci_opportunities": None,
            "_contest_requirement_is_official": from_official,
            # A contest-level requirement taken from the PCI summary is usually
            # the cargo table itself, and repeating it on every row would
            # attribute one cargo's qualification to all the others. Read from
            # the actual edital it is different: it states what the selection
            # requires, and suppressing it leaves the row emptier than the
            # source. So it is kept, and labelled as general rather than
            # specific to this cargo.
            **({} if from_official else {
                field: None
                for field in (
                    "graduation_requirement", "graduation_requirement_raw",
                    "postgraduate_requirement", "postgraduate_requirement_raw",
                    "masters_requirement", "masters_requirement_raw",
                    "doctorate_requirement", "doctorate_requirement_raw",
                )
            }),
        })
    return rows or [{**vacancy, "_is_subvacancy": False, "_parent_title": vacancy.get("title")}]


POST_HINT = re.compile(r"\bp[oó]s\b|p[oó]s[- ]gradua|especializa|mestrado|doutorado", re.I)

# A degree name with nothing else in it: "Mestrado", "Mestrado e Doutorado",
# "graduação". The source did not say in what field.
DEGREE_WORDS = re.compile(
    r"(?:p[oó]s[- ]?gradua[cç][aã]o|mestrado|doutorado|especializa[cç][aã]o|"
    r"gradua[cç][aã]o|bacharelado|licenciatura|t[ií]tulo\s+de\s+(?:mestre|doutor)|"
    r"grau\s+de\s+(?:mestre|doutor)|conclu[ií]d[oa]s?|stricto\s+sensu|lato\s+sensu|"
    r"\bem\b|\bna\b|\bde\b|\be\b|\bou\b|[\s,;()/-])+",
    re.I,
)


def _mark_if_area_is_missing(value: str) -> str:
    """Say when a degree comes without its field, instead of implying it did not.

    "Mestrado" on its own reads like a complete requirement. It is not — the
    notice simply never said in what, and the reader has to open the edital to
    find out. Naming that is more useful than a word that looks finished.
    """
    if not value or value == "Não informado":
        return value
    remainder = DEGREE_WORDS.sub("", value).strip(" .,;:-–/()")
    if remainder:
        return value
    return f"{value} — área não informada"


def _hint_category(value: Any) -> str | None:
    """Place a short PCI formation hint ("Magistério", "Pós") in the right column."""
    text = str(value or "").strip()
    if not text:
        return None
    return "postgraduate" if POST_HINT.search(text) else "graduation"


def _structured_requirements(v: dict[str, Any]) -> tuple[str, str]:
    """Build display fields from every explicit qualification run.

    PCI prose is reparsed at render time so older cached records benefit from
    parser corrections without waiting for their next network refresh.
    """
    sources: list[tuple[Any, str | None]] = []
    if v.get("_is_subvacancy"):
        sources.append((v.get("requirement_text"), _hint_category(v.get("requirement_text"))))
    elif not str(v.get("requirements_source") or "").startswith("OFFICIAL_"):
        pci_requirements = extract_requirement_fields(v.get("raw_text"))
        sources.extend((
            (pci_requirements.get("graduation_requirement"), "graduation"),
            (pci_requirements.get("postgraduate_requirement"), "postgraduate"),
        ))
    sources.extend((
        (v.get("graduation_requirement_raw") or v.get("graduation_requirement"), "graduation"),
        (v.get("postgraduate_requirement_raw") or v.get("postgraduate_requirement"), "postgraduate"),
        (v.get("masters_requirement_raw") or v.get("masters_requirement"), "postgraduate"),
        (v.get("doctorate_requirement_raw") or v.get("doctorate_requirement"), "postgraduate"),
    ))
    graduation_parts: list[str] = []
    post_parts: list[str] = []
    seen_sources: set[str] = set()
    for source, assumed_category in sources:
        normalized_source = re.sub(r"\s+", " ", str(source or "")).strip()
        if not normalized_source or normalized_source in seen_sources:
            continue
        seen_sources.add(normalized_source)
        separated = split_academic_requirement(normalized_source)
        if not separated["graduation"] and not separated["postgraduate"] and assumed_category:
            cleaned = clean_requirement_context(normalized_source)
            # A short already-structured value may omit the degree label. Long
            # prose is never guessed into a column.
            if cleaned and len(cleaned) <= 180:
                separated[assumed_category].append(cleaned)
        for value in separated["graduation"]:
            graduation = graduation_for_display(value)
            if graduation and graduation not in graduation_parts:
                graduation_parts.append(graduation)
        for post in separated["postgraduate"]:
            if post and post not in post_parts:
                post_parts.append(post)
    # Condense last: the reader wants "Mestrado em Educação ou áreas afins",
    # not the paragraph it came from. Anything the condenser refuses was not a
    # requirement to begin with — a site menu, a salary table — and showing
    # nothing is better than showing that.
    graduation = condense_requirement(
        _joined_for_display(graduation_parts), keep_degree=False
    )
    post = condense_requirement(_joined_for_display(post_parts))
    return (
        _mark_if_area_is_missing(graduation or "Não informado"),
        _mark_if_area_is_missing(post or "Não informado"),
    )


# Each run is bounded on its own, but a cell that concatenates several of them
# can still become a wall of text. Beyond a few statements the reader is better
# served by the edital itself.
MAX_DISPLAYED_REQUIREMENTS = 3
MAX_DISPLAYED_CHARS = 400


def _joined_for_display(parts: list[str]) -> str:
    if not parts:
        return "Não informado"
    kept: list[str] = []
    for part in parts[:MAX_DISPLAYED_REQUIREMENTS]:
        candidate = " / ".join([*kept, part])
        if kept and len(candidate) > MAX_DISPLAYED_CHARS:
            break
        kept.append(part)
    joined = " / ".join(kept)
    if len(joined) > MAX_DISPLAYED_CHARS:
        joined = joined[:MAX_DISPLAYED_CHARS].rsplit(" ", 1)[0] + "…"
    if len(kept) < len(parts):
        joined += f" (+{len(parts) - len(kept)} no edital)"
    return joined


def _detail_items(v: dict[str, Any]) -> list[tuple[str, str]]:
    details: list[tuple[str, str]] = []
    if v.get("registration_start") or v.get("registration_end"):
        registration_start = _format_date(v.get("registration_start"))
        registration_end = _format_date(v.get("registration_end"))
        registration_period = (
            f"{registration_start} a {registration_end}"
            if v.get("registration_start") else f"até {registration_end}"
        )
        details.append(("Inscrições", registration_period))
    if v.get("campus"):
        details.append(("Campus", str(v["campus"])))
    if v.get("vacancies_count"):
        count = int(v["vacancies_count"])
        details.append(("Vagas", f"{count} vaga" if count == 1 else f"{count} vagas"))
    if v.get("workload"):
        details.append(("Jornada", str(v["workload"])))
    if v.get("salary_text"):
        details.append(("Remuneração", str(v["salary_text"])))
    return details


def _common_detail_items(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    if not rows:
        return []
    row_details = [dict(_detail_items(row)) for row in rows]
    common: list[tuple[str, str]] = []
    for label, value in _detail_items(rows[0]):
        if value and all(details.get(label) == value for details in row_details[1:]):
            common.append((label, value))
    return common


def _row(v: dict[str, Any], common_detail_labels: set[str] | None = None) -> str:
    graduation, post = _structured_requirements(v)
    area = str(v.get("area") or "")
    area_identified = area and normalize_for_report(area) != "nao identificada"
    vacancy_name = area if area_identified else (v.get("title") or v.get("position"))
    vacancy_context = v.get("reference") or v.get("position")
    eligibility = str(v.get("formal_eligibility") or "UNKNOWN")
    score = int(v.get("thematic_score") or 0)
    protected_count = len(v.get("official_pci_protected_documents") or [])
    pages = sorted({
        item.get("page") for item in (v.get("official_requirement_evidence") or []) if item.get("page")
    })
    if v.get("page"):
        pages = [v["page"]]
    if v.get("official_check_status") in ("READ", "READ_MULTI"):
        source_note = "Edital lido" + (f" · página {', '.join(map(str, pages[:3]))}" if pages else "")
        if v.get("official_tls_unverified"):
            source_note += " · cadeia TLS incompleta no servidor"
        if v.get("_contest_requirement_is_official") and not v.get("requirement_text"):
            source_note += " · requisito geral do edital, não específico deste cargo"
    elif protected_count:
        source_note = f"{protected_count} edital(is) no PCI · verificação humana"
        # The PDF is gated, but its number is not. Showing it turns a dead end
        # into something the reader can search for on the institution's site.
        numbers = edital_numbers_for_display(v)
        if numbers:
            source_note += f" · Edital nº {', '.join(numbers)}"
    else:
        source_note = f"Leitura: {v.get('official_check_status') or 'pendente'}"

    search = " ".join(str(item or "") for item in (
        v.get("title"), v.get("_parent_title"), v.get("institution"), v.get("area"),
        v.get("course"), v.get("state"), v.get("city"), graduation, post,
        config.INSTITUTION_TYPE_LABELS.get(institution_type(v), ""),
    )).lower()
    is_open = str(v.get("status")) != "CLOSED"
    is_new = str(v.get("status")) == "NEW"
    analysis = " ".join(filter(None, (v.get("formal_reason"), v.get("thematic_reason"))))
    common_detail_labels = common_detail_labels or set()
    specific_details = [item for item in _detail_items(v) if item[0] not in common_detail_labels]
    details = "".join(
        f'<div><dt>{_escape(label)}</dt><dd>{_escape(value)}</dd></div>'
        for label, value in specific_details
    )
    details_content = (
        f'<dl class="vacancy-details">{details}</dl>'
        if details else '<span class="no-specific-details">Nenhum detalhe específico</span>'
    )
    post_lines = "".join(f"<span>{_escape(line)}</span>" for line in post.split(" / "))

    return f'''<article role="row" class="vacancy-card vacancy-row eligibility-{_escape(eligibility).lower()}" data-state="{_escape(v.get("state"), '')}" data-institution="{_escape(v.get("institution"), '')}" data-eligibility="{_escape(eligibility)}" data-score="{score}" data-open="{str(is_open).lower()}" data-new="{str(is_new).lower()}" data-course="{_escape(v.get("course"), '')}" data-institution-type="{_escape(institution_type(v))}" data-search="{_escape(search, '')}">
      <div role="cell" class="list-cell vacancy-cell" data-label="Vaga ou área"><strong>{_escape(vacancy_name)}</strong><span class="vacancy-context">{_escape(vacancy_context)}</span><div class="row-badges"><span class="eligibility-badge">{_escape(ELIGIBILITY_LABELS.get(eligibility, eligibility))}</span><span>Aderência {score}/100</span></div></div>
      <div role="cell" class="list-cell requirement-cell" data-label="Requisito de graduação"><p>{_escape(graduation)}</p></div>
      <div role="cell" class="list-cell requirement-cell post-cell" data-label="Requisito de pós-graduação"><p>{post_lines}</p><small>{_escape(source_note)}</small></div>
      <div role="cell" class="list-cell details-cell" data-label="Detalhes específicos">{details_content}<details class="row-details"><summary>Análise</summary><p>{_escape(analysis)}</p></details></div>
    </article>'''


def _contest_section(vacancy: dict[str, Any], index: int) -> str:
    rows = _expanded_rows([vacancy])
    common_details = _common_detail_items(rows)
    common_detail_labels = {label for label, _ in common_details}
    publication = _format_date(vacancy.get("publication_date"))
    location = " / ".join(item for item in (vacancy.get("state"), vacancy.get("city")) if item)
    contest_id = f"contest-{index}"
    links = [f'<a href="{_escape(vacancy.get("source_url"))}" target="_blank" rel="noopener">Ver no PCI</a>']
    official_target = vacancy.get("official_document_url") or vacancy.get("official_url")
    if official_target:
        label = "Abrir edital" if vacancy.get("official_document_url") else "Fonte oficial"
        links.append(f'<a href="{_escape(official_target)}" target="_blank" rel="noopener">{label}</a>')
    protected_count = len(vacancy.get("official_pci_protected_documents") or [])
    if vacancy.get("official_check_status") in ("READ", "READ_MULTI"):
        reading_note = "Edital lido e requisitos separados por vaga"
    elif protected_count:
        reading_note = f"{protected_count} edital(is) no PCI aguardando verificação humana"
    else:
        reading_note = f"Leitura do edital: {vacancy.get('official_check_status') or 'pendente'}"
    contest_meta = (
        ("Divulgação", publication),
        ("Seleção", vacancy.get("employment_type") or "Não informada"),
        ("Local", location or "Não informado"),
        ("Situação", STATUS_LABELS.get(vacancy.get("status"), vacancy.get("status") or "Não informada")),
    )
    meta_html = "".join(
        f'<div><dt>{_escape(label)}</dt><dd>{_escape(value)}</dd></div>'
        for label, value in contest_meta
    )
    common_details_html = "".join(
        f'<div><dt>{_escape(label)}</dt><dd>{_escape(value)}</dd></div>'
        for label, value in common_details
    )
    common_block = (
        '<div class="contest-common"><strong>Detalhes comuns a todas as vagas</strong>'
        f'<dl>{common_details_html}</dl></div>'
        if common_details else ""
    )
    table_rows = "".join(_row(row, common_detail_labels) for row in rows)
    row_label = "vaga listada" if len(rows) == 1 else "vagas listadas"
    return f'''<section class="contest-group" aria-labelledby="{contest_id}">
      <header class="contest-header"><div class="contest-title"><p>{_escape(vacancy.get("institution"))}</p><h3 id="{contest_id}">{_escape(vacancy.get("title"))}</h3><span>{len(rows)} {row_label} neste concurso · {_escape(reading_note)}</span></div><nav>{' '.join(links)}</nav></header>
      <dl class="contest-meta">{meta_html}</dl>
      {common_block}
      <div class="contest-table" role="table" aria-label="Vagas e requisitos de {_escape(vacancy.get('title'))}">
        <div class="list-header" role="row"><div role="columnheader">Vaga ou área</div><div role="columnheader">Requisito de graduação</div><div role="columnheader">Requisito de pós-graduação</div><div role="columnheader">Detalhes específicos</div></div>{table_rows}
      </div>
    </section>'''


def normalize_for_report(value: str) -> str:
    import unicodedata
    return "".join(
        character for character in unicodedata.normalize("NFKD", value.lower())
        if not unicodedata.combining(character)
    ).strip()


def generate_report(vacancies: list[dict[str, Any]], output_path: Path, generated_at: datetime | None = None) -> None:
    generated_at = generated_at or datetime.now(ZoneInfo("America/Sao_Paulo"))
    today = generated_at.date()
    visible = [v for v in vacancies if _is_recently_closed(v, today)]
    visible.sort(key=_sort_key)
    rows = _expanded_rows(visible)
    open_count = sum(v.get("status") != "CLOSED" for v in rows)
    new_count = sum(v.get("status") == "NEW" for v in rows)
    official_read_count = sum(v.get("official_check_status") in ("READ", "READ_MULTI") for v in visible)
    states = sorted({v.get("state") for v in visible if v.get("state")})
    institutions = sorted({v.get("institution") for v in visible if v.get("institution")})
    courses = sorted(
        {str(v["course"]).strip() for v in rows if v.get("course")},
        key=lambda value: normalize_for_report(value),
    )
    sections = (
        "".join(_contest_section(vacancy, index) for index, vacancy in enumerate(visible, start=1))
        if rows else '<div class="empty">Nenhuma vaga relevante registrada ainda. Execute o monitor para atualizar o radar.</div>'
    )
    options_state = "".join(f'<option value="{_escape(s)}">{_escape(s)}</option>' for s in states)
    options_inst = "".join(f'<option value="{_escape(i)}">{_escape(i)}</option>' for i in institutions)
    options_course = "".join(f'<option value="{_escape(c)}">{_escape(c)}</option>' for c in courses)
    profile_json = html.escape(json.dumps({
        "area": config.PROFILE["doctorate"]["capes_evaluation_area"],
        "broad": config.PROFILE["doctorate"]["capes_broad_area"],
    }, ensure_ascii=False))
    document = f'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Radar automático de concursos acadêmicos"><title>Concursos Watch</title>
<link rel="stylesheet" href="assets/style.css?v=9"></head><body data-profile="{profile_json}">
<header class="hero"><div class="hero-inner"><p class="eyebrow">CONCURSOS WATCH</p><h1>Radar de Concursos Acadêmicos</h1><p class="intro">Oportunidades docentes públicas organizadas por concurso, edital, vaga e requisitos de formação.</p>
<div class="summary"><div><strong>{generated_at.strftime('%d/%m/%Y %H:%M')}</strong><span>Última atualização (BRT)</span></div><div><strong>{open_count}</strong><span>Vagas abertas listadas</span></div><div><strong>{new_count}</strong><span>Novas hoje</span></div><div><strong>PCI</strong><span>Fonte monitorada</span></div></div></div></header>
<main><aside class="notice"><strong>Triagem, não decisão jurídica.</strong> “Elegível” não substitui a decisão da instituição sobre equivalência de títulos. <span class="official-count">Editais oficiais lidos com evidência aplicável: {official_read_count}.</span></aside>
<section class="filters" aria-label="Filtros"><label>Buscar<input id="search" type="search" placeholder="Área, cidade, instituição…"></label><label>Estado<select id="state"><option value="">Todos</option>{options_state}</select></label><label>Instituição<select id="institution"><option value="">Todas</option>{options_inst}</select></label><label>Tipo<select id="institution-type"><option value="">Todas</option><option value="SUPERIOR" selected>Universidades e IFs</option><option value="BASICA">Prefeituras e estados</option><option value="INDEFINIDA">Indefinida</option></select></label><label>Curso<select id="course"><option value="">Todos</option>{options_course}</select></label><label>Elegibilidade<select id="eligibility"><option value="">Todas</option><option>YES</option><option>UNCERTAIN</option><option>UNKNOWN</option><option>NO</option></select></label><label>Aderência mínima<input id="score" type="range" min="0" max="100" value="0"><output id="score-value">0</output></label><label class="check"><input id="open-only" type="checkbox"> Somente abertas</label><label class="check"><input id="new-only" type="checkbox"> Somente novas</label><button id="clear" type="button">Limpar filtros</button></section>
<div class="results-heading"><h2>Todas as vagas por concurso</h2><span id="result-count">{len(rows)} vaga(s) em {len(visible)} concurso(s)</span></div><div id="cards" class="contest-list">{sections}</div>
</main><footer>Gerado automaticamente · Fonte de descoberta: <a href="{config.PCI_LISTING_URL}">PCI Concursos</a> · Consulte sempre o edital oficial.</footer><script src="assets/app.js?v=9"></script></body></html>'''
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8", newline="\n")
