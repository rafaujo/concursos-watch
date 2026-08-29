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
    """Turn structured multi-area notices into one comparable row per relevant area."""
    rows: list[dict[str, Any]] = []
    for vacancy in vacancies:
        opportunities = [
            item for item in (vacancy.get("official_opportunities") or [])
            if int(item.get("thematic_score") or 0) > 0
        ]
        opportunities.sort(key=lambda item: -int(item.get("thematic_score") or 0))
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


def _post_requirement(v: dict[str, Any]) -> str:
    if v.get("_is_subvacancy"):
        masters = v.get("masters_requirement_raw")
        doctorate = v.get("doctorate_requirement_raw")
    else:
        masters = v.get("masters_requirement_raw") or v.get("masters_requirement")
        doctorate = v.get("doctorate_requirement_raw") or v.get("doctorate_requirement")
    parts: list[str] = []
    if masters:
        parts.append(f"Mestrado: {masters}")
    if doctorate and doctorate != masters:
        parts.append(f"Doutorado: {doctorate}")
    if not parts and v.get("_is_subvacancy"):
        requirement = str(v.get("requirement_text") or "")
        post_match = re.search(
            r"\b(mestrado|doutorado|especializa[cç][aã]o|p[oó]s-gradua[cç][aã]o)\b.*$",
            requirement,
            flags=re.IGNORECASE,
        )
        if post_match:
            parts.append(f"Pós-graduação: {post_match.group(0)}")
    return "\n".join(parts) or "Não informado"


def _detail_items(v: dict[str, Any]) -> list[tuple[str, str]]:
    details: list[tuple[str, str]] = []
    if v.get("employment_type"):
        details.append(("Seleção", str(v["employment_type"])))
    if v.get("vacancies_count"):
        count = int(v["vacancies_count"])
        details.append(("Vagas", f"{count} vaga" if count == 1 else f"{count} vagas"))
    if v.get("workload"):
        details.append(("Jornada", str(v["workload"])))
    if v.get("salary_text"):
        details.append(("Remuneração", str(v["salary_text"])))
    return details


def _row(v: dict[str, Any]) -> str:
    links = [f'<a href="{_escape(v.get("source_url"))}" target="_blank" rel="noopener">PCI</a>']
    official_target = v.get("official_document_url") or v.get("official_url")
    if official_target:
        label = "Edital" if v.get("official_document_url") else "Fonte oficial"
        links.append(f'<a href="{_escape(official_target)}" target="_blank" rel="noopener">{label}</a>')

    graduation = (
        v.get("graduation_requirement_raw")
        or v.get("graduation_requirement")
        or (v.get("requirement_text") if v.get("_is_subvacancy") else None)
        or "Não informado"
    )
    post = _post_requirement(v)
    area = str(v.get("area") or "")
    area_identified = area and normalize_for_report(area) != "nao identificada"
    vacancy_name = area if area_identified else (v.get("title") or v.get("position"))
    vacancy_context = v.get("_parent_title") if v.get("_is_subvacancy") else v.get("position")
    location = " / ".join(item for item in (v.get("state"), v.get("city"), v.get("campus")) if item)
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
    publication = _format_date(v.get("publication_date"))
    registration_start = _format_date(v.get("registration_start"))
    registration_end = _format_date(v.get("registration_end"))
    registration_period = (
        f"{registration_start} a {registration_end}"
        if v.get("registration_start") else f"até {registration_end}"
    )
    details = "".join(
        f'<div><dt>{_escape(label)}</dt><dd>{_escape(value)}</dd></div>'
        for label, value in _detail_items(v)
    )
    post_lines = "".join(f"<span>{_escape(line)}</span>" for line in post.split("\n"))

    return f'''<article role="row" class="vacancy-card vacancy-row eligibility-{_escape(eligibility).lower()}" data-state="{_escape(v.get("state"), '')}" data-institution="{_escape(v.get("institution"), '')}" data-eligibility="{_escape(eligibility)}" data-score="{score}" data-open="{str(is_open).lower()}" data-new="{str(is_new).lower()}" data-search="{_escape(search, '')}">
      <div role="cell" class="list-cell institution-cell" data-label="Universidade ou Instituto"><strong>{_escape(v.get("institution"))}</strong><span>{_escape(location)}</span></div>
      <div role="cell" class="list-cell vacancy-cell" data-label="Vaga"><strong>{_escape(vacancy_name)}</strong><span class="vacancy-context">{_escape(vacancy_context)}</span><dl class="dates"><div><dt>Divulgação</dt><dd>{_escape(publication)}</dd></div><div><dt>Inscrições</dt><dd>{_escape(registration_period)}</dd></div></dl><dl class="vacancy-details">{details}</dl><div class="row-badges"><span class="eligibility-badge">{_escape(ELIGIBILITY_LABELS.get(eligibility, eligibility))}</span><span>Aderência {score}/100</span></div><details class="row-details"><summary>Mais detalhes e fontes</summary><p>{_escape(analysis)}</p><nav>{' · '.join(links)}</nav></details></div>
      <div role="cell" class="list-cell requirement-cell" data-label="Requisito de graduação"><p>{_escape(graduation)}</p></div>
      <div role="cell" class="list-cell requirement-cell post-cell" data-label="Requisito de pós-graduação"><p>{post_lines}</p><small>{_escape(source_note)}</small></div>
    </article>'''


def normalize_for_report(value: str) -> str:
    import unicodedata
    return "".join(
        character for character in unicodedata.normalize("NFKD", value.lower())
        if not unicodedata.combining(character)
    ).strip()


def generate_report(vacancies: list[dict[str, Any]], output_path: Path, generated_at: datetime | None = None) -> None:
    generated_at = generated_at or datetime.now(ZoneInfo("America/Sao_Paulo"))
    today = generated_at.date()
    visible = [v for v in vacancies if _is_recently_closed(v, today) and v.get("thematic_score", 0) > 0]
    visible.sort(key=_sort_key)
    rows = _expanded_rows(visible)
    open_count = sum(v.get("status") != "CLOSED" for v in rows)
    new_count = sum(v.get("status") == "NEW" for v in rows)
    official_read_count = sum(v.get("official_check_status") in ("READ", "READ_MULTI") for v in visible)
    states = sorted({v.get("state") for v in visible if v.get("state")})
    institutions = sorted({v.get("institution") for v in visible if v.get("institution")})
    list_rows = "".join(_row(v) for v in rows)
    sections = (
        '<div class="list-header" role="row"><div role="columnheader">Universidade ou Instituto</div>'
        '<div role="columnheader">Vaga</div><div role="columnheader">Requisito de graduação</div>'
        '<div role="columnheader">Requisito de pós-graduação</div></div>' + list_rows
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
<link rel="stylesheet" href="assets/style.css"></head><body data-profile="{profile_json}">
<header class="hero"><div class="hero-inner"><p class="eyebrow">CONCURSOS WATCH</p><h1>Radar de Concursos Acadêmicos</h1><p class="intro">Triagem diária de oportunidades docentes públicas compatíveis com Administração e Ciências Ambientais.</p>
<div class="summary"><div><strong>{generated_at.strftime('%d/%m/%Y %H:%M')}</strong><span>Última atualização (BRT)</span></div><div><strong>{open_count}</strong><span>Vagas abertas relevantes</span></div><div><strong>{new_count}</strong><span>Novas hoje</span></div><div><strong>PCI</strong><span>Fonte monitorada</span></div></div></div></header>
<main><aside class="notice"><strong>Triagem, não decisão jurídica.</strong> “Elegível” não substitui a decisão da instituição sobre equivalência de títulos. <span class="official-count">Editais oficiais lidos com evidência aplicável: {official_read_count}.</span></aside>
<section class="filters" aria-label="Filtros"><label>Buscar<input id="search" type="search" placeholder="Área, cidade, instituição…"></label><label>Estado<select id="state"><option value="">Todos</option>{options_state}</select></label><label>Instituição<select id="institution"><option value="">Todas</option>{options_inst}</select></label><label>Elegibilidade<select id="eligibility"><option value="">Todas</option><option>YES</option><option>UNCERTAIN</option><option>UNKNOWN</option><option>NO</option></select></label><label>Aderência mínima<input id="score" type="range" min="0" max="100" value="0"><output id="score-value">0</output></label><label class="check"><input id="open-only" type="checkbox" checked> Somente abertas</label><label class="check"><input id="new-only" type="checkbox"> Somente novas</label><button id="clear" type="button">Limpar filtros</button></section>
<div class="results-heading"><h2>Lista de oportunidades</h2><span id="result-count">{len(rows)} resultado(s)</span></div><div id="cards" class="vacancy-list" role="table" aria-label="Oportunidades docentes e requisitos">{sections}</div>
</main><footer>Gerado automaticamente · Fonte de descoberta: <a href="{config.PCI_LISTING_URL}">PCI Concursos</a> · Consulte sempre o edital oficial.</footer><script src="assets/app.js"></script></body></html>'''
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8", newline="\n")
