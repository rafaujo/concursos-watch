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
                rows.append({
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
                })
        else:
            rows.append({**vacancy, "_is_subvacancy": False, "_parent_title": vacancy.get("title")})
    return rows


POST_TERM_PATTERN = r"(?:mestrado|doutorado|especializa[cç][aã]o|p[oó]s-gradua[cç][aã]o)"


def _clean_requirement_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    title_marker = re.search(r"titula[cç][aã]o\s+m[ií]nima\s+exigida\s*:\s*", text, re.I)
    if title_marker:
        text = text[title_marker.end():]
    text = re.sub(r"\bInscri[cç][oõ]es?\s*:.*$", "", text, flags=re.I)
    text = re.sub(r"^(?:Requisito\(s\)|Requisitos?)\s*:\s*", "", text, flags=re.I)
    return text.strip(" .;,:")


def _split_requirement_text(value: Any) -> tuple[str, str]:
    text = _clean_requirement_text(value)
    if not text:
        return "", ""
    post_match = re.search(rf"\b{POST_TERM_PATTERN}\b", text, flags=re.I)
    graduation = text[:post_match.start()] if post_match else text
    post = text[post_match.start():] if post_match else ""
    graduation = re.sub(r"[,;:]?\s*(?:e|ou)?\s*$", "", graduation, flags=re.I).strip()
    graduation = re.sub(
        r"^(?:gradua[cç][aã]o|bacharelado|licenciatura|tecnologia)\s+"
        r"(?:(?:em|nas?\s+[aá]reas?\s+de|na\s+[aá]rea\s+de)\s+)?",
        "",
        graduation,
        flags=re.I,
    ).strip(" .;,:")
    post = re.sub(r"^(?:e|ou)\s+", "", post, flags=re.I).strip(" .;,:")
    return graduation, post


def _structured_requirements(v: dict[str, Any]) -> tuple[str, str]:
    if v.get("_is_subvacancy"):
        graduation, fallback_post = _split_requirement_text(v.get("requirement_text"))
        explicit_post_parts: list[str] = []
        for source in (v.get("masters_requirement_raw"), v.get("doctorate_requirement_raw")):
            _, post = _split_requirement_text(source)
            if post and post not in explicit_post_parts:
                explicit_post_parts.append(post)
        post = " / ".join(explicit_post_parts) or fallback_post
        return graduation or "Não informado", post or "Não informado"
    else:
        raw_sources = [
            v.get("graduation_requirement_raw") or v.get("graduation_requirement"),
            v.get("masters_requirement_raw") or v.get("masters_requirement"),
            v.get("doctorate_requirement_raw") or v.get("doctorate_requirement"),
        ]
        sources = []
        for source in raw_sources:
            if source and source not in sources:
                sources.append(source)

    graduation_parts: list[str] = []
    post_parts: list[str] = []
    for source in sources:
        graduation, post = _split_requirement_text(source)
        if graduation and graduation not in graduation_parts:
            graduation_parts.append(graduation)
        if post and post not in post_parts:
            post_parts.append(post)
    return " / ".join(graduation_parts) or "Não informado", " / ".join(post_parts) or "Não informado"


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
    elif protected_count:
        source_note = f"{protected_count} edital(is) no PCI · verificação humana"
    else:
        source_note = f"Leitura: {v.get('official_check_status') or 'pendente'}"

    search = " ".join(str(item or "") for item in (
        v.get("title"), v.get("_parent_title"), v.get("institution"), v.get("area"),
        v.get("state"), v.get("city"), graduation, post,
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

    return f'''<article role="row" class="vacancy-card vacancy-row eligibility-{_escape(eligibility).lower()}" data-state="{_escape(v.get("state"), '')}" data-institution="{_escape(v.get("institution"), '')}" data-eligibility="{_escape(eligibility)}" data-score="{score}" data-open="{str(is_open).lower()}" data-new="{str(is_new).lower()}" data-search="{_escape(search, '')}">
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
    sections = (
        "".join(_contest_section(vacancy, index) for index, vacancy in enumerate(visible, start=1))
        if rows else '<div class="empty">Nenhuma vaga relevante registrada ainda. Execute o monitor para atualizar o radar.</div>'
    )
    options_state = "".join(f'<option value="{_escape(s)}">{_escape(s)}</option>' for s in states)
    options_inst = "".join(f'<option value="{_escape(i)}">{_escape(i)}</option>' for i in institutions)
    profile_json = html.escape(json.dumps({
        "area": config.PROFILE["doctorate"]["capes_evaluation_area"],
        "broad": config.PROFILE["doctorate"]["capes_broad_area"],
    }, ensure_ascii=False))
    document = f'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Radar automático de concursos acadêmicos"><title>Concursos Watch</title>
<link rel="stylesheet" href="assets/style.css?v=4"></head><body data-profile="{profile_json}">
<header class="hero"><div class="hero-inner"><p class="eyebrow">CONCURSOS WATCH</p><h1>Radar de Concursos Acadêmicos</h1><p class="intro">Oportunidades docentes públicas organizadas por concurso, edital, vaga e requisitos de formação.</p>
<div class="summary"><div><strong>{generated_at.strftime('%d/%m/%Y %H:%M')}</strong><span>Última atualização (BRT)</span></div><div><strong>{open_count}</strong><span>Vagas abertas listadas</span></div><div><strong>{new_count}</strong><span>Novas hoje</span></div><div><strong>PCI</strong><span>Fonte monitorada</span></div></div></div></header>
<main><aside class="notice"><strong>Triagem, não decisão jurídica.</strong> “Elegível” não substitui a decisão da instituição sobre equivalência de títulos. <span class="official-count">Editais oficiais lidos com evidência aplicável: {official_read_count}.</span></aside>
<section class="filters" aria-label="Filtros"><label>Buscar<input id="search" type="search" placeholder="Área, cidade, instituição…"></label><label>Estado<select id="state"><option value="">Todos</option>{options_state}</select></label><label>Instituição<select id="institution"><option value="">Todas</option>{options_inst}</select></label><label>Elegibilidade<select id="eligibility"><option value="">Todas</option><option>YES</option><option>UNCERTAIN</option><option>UNKNOWN</option><option>NO</option></select></label><label>Aderência mínima<input id="score" type="range" min="0" max="100" value="0"><output id="score-value">0</output></label><label class="check"><input id="open-only" type="checkbox"> Somente abertas</label><label class="check"><input id="new-only" type="checkbox"> Somente novas</label><button id="clear" type="button">Limpar filtros</button></section>
<div class="results-heading"><h2>Todas as vagas por concurso</h2><span id="result-count">{len(rows)} vaga(s) em {len(visible)} concurso(s)</span></div><div id="cards" class="contest-list">{sections}</div>
</main><footer>Gerado automaticamente · Fonte de descoberta: <a href="{config.PCI_LISTING_URL}">PCI Concursos</a> · Consulte sempre o edital oficial.</footer><script src="assets/app.js?v=4"></script></body></html>'''
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8", newline="\n")
