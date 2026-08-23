"""Generate a dependency-free, responsive GitHub Pages report."""

from __future__ import annotations

import html
import json
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


def _card(v: dict[str, Any]) -> str:
    links = [f'<a class="button" href="{_escape(v.get("source_url"))}" target="_blank" rel="noopener">Ver no PCI</a>']
    official_target = v.get("official_document_url") or v.get("official_url")
    if official_target:
        official_label = "Abrir edital oficial" if v.get("official_document_url") else "Fonte oficial / inscrições"
        links.append(f'<a class="button secondary" href="{_escape(official_target)}" target="_blank" rel="noopener">{official_label}</a>')
    if v.get("institution_url"):
        links.append(f'<a class="button secondary" href="{_escape(v["institution_url"])}" target="_blank" rel="noopener">Site da instituição</a>')
    search = " ".join(str(v.get(key) or "") for key in ("title", "institution", "area", "state", "city")).lower()
    is_open = str(v.get("status")) not in ("CLOSED",)
    is_new = str(v.get("status")) == "NEW"
    details = (
        ("Campus", v.get("campus")), ("Estado / cidade", " / ".join(x for x in (v.get("state"), v.get("city")) if x)),
        ("Cargo", v.get("position")), ("Área", v.get("area")), ("Regime", v.get("employment_type")),
        ("Carga horária", v.get("workload")), ("Remuneração", v.get("salary_text")),
        ("Inscrições até", _format_date(v.get("registration_end"))), ("Encontrada em", _format_date(v.get("first_seen"))),
    )
    detail_html = "".join(f"<div><dt>{_escape(label)}</dt><dd>{_escape(value)}</dd></div>" for label, value in details)
    requirements = (
        ("Graduação exigida", v.get("graduation_requirement_raw") or v.get("graduation_requirement")),
        ("Mestrado exigido", v.get("masters_requirement_raw") or v.get("masters_requirement")),
        ("Doutorado exigido", v.get("doctorate_requirement_raw") or v.get("doctorate_requirement")),
    )
    req_html = "".join(f"<p><strong>{_escape(label)}:</strong> {_escape(value)}</p>" for label, value in requirements)
    official_status = v.get("official_check_status") or "NÃO CONSULTADO"
    evidence_items = []
    for item in (v.get("official_requirement_evidence") or [])[:3]:
        page = item.get("page")
        page_label = f"Página {page}: " if page else ""
        evidence_items.append(
            f'<li><strong>{_escape(page_label, "")}</strong>{_escape(str(item.get("text") or "")[:600])}</li>'
        )
    evidence_html = f'<ul class="evidence">{"".join(evidence_items)}</ul>' if evidence_items else ""
    opportunity_items = []
    relevant_official = [
        item for item in (v.get("official_opportunities") or [])
        if int(item.get("thematic_score") or 0) > 0
    ]
    relevant_official.sort(key=lambda item: -int(item.get("thematic_score") or 0))
    if v.get("official_check_status") == "READ_MULTI" and v.get("official_opportunities"):
        req_html = (
            '<p class="multi-area-note">O anúncio do PCI não individualiza os requisitos. '
            'Use os blocos do edital abaixo, analisados separadamente.</p>'
        )
    for item in relevant_official[:8]:
        requirement = item.get("requirement_text") or "Requisito não extraído"
        opportunity_items.append(
            f'<li><div><strong>{_escape(item.get("area"))}</strong>'
            f'<span class="opportunity-metrics">Aderência {int(item.get("thematic_score") or 0)}/100 · '
            f'{_escape(item.get("formal_eligibility"), "UNKNOWN")} · página {_escape(item.get("page"), "?")}</span></div>'
            f'<p>{_escape(requirement)}</p></li>'
        )
    opportunities_html = (
        f'<div class="official-opportunities"><h4>Sub-vagas relevantes encontradas no edital</h4>'
        f'<ol>{"".join(opportunity_items)}</ol></div>'
        if opportunity_items else ""
    )
    official_html = (
        f'<div class="official-audit"><p><strong>Leitura do edital:</strong> {_escape(official_status)}'
        f' · Fonte dos requisitos: {_escape(v.get("requirements_source"), "Resumo do PCI")}</p>'
        f'<p>{_escape(v.get("official_check_reason"), "Fonte oficial ainda não consultada.")}</p>'
        f'{opportunities_html}{evidence_html}</div>'
    )
    return f'''<article class="vacancy-card eligibility-{_escape(v.get("formal_eligibility", "UNKNOWN")).lower()}" data-state="{_escape(v.get("state"), '')}" data-institution="{_escape(v.get("institution"), '')}" data-eligibility="{_escape(v.get("formal_eligibility", "UNKNOWN"))}" data-score="{int(v.get("thematic_score") or 0)}" data-open="{str(is_open).lower()}" data-new="{str(is_new).lower()}" data-search="{_escape(search, '')}">
      <header><span class="status">{_escape(v.get("visual_category"), "⚪ Informação insuficiente")}</span><span class="state">{_escape(v.get("state"), "BR")}</span></header>
      <h3>{_escape(v.get("institution"))}</h3><p class="title">{_escape(v.get("title"))}</p>
      <div class="metrics"><div><span>{int(v.get("thematic_score") or 0)}</span>/100<small>Aderência temática</small></div><div><span>{_escape(v.get("formal_eligibility", "UNKNOWN"))}</span><small>{_escape(ELIGIBILITY_LABELS.get(v.get("formal_eligibility"), "Informação insuficiente"))}</small></div></div>
      <p class="reason"><strong>Por quê:</strong> {_escape(v.get("formal_reason"))} {_escape(v.get("thematic_reason"), '')}</p>
      <dl>{detail_html}</dl><details><summary>Requisitos e evidências</summary>{req_html}{official_html}</details>
      <div class="actions">{''.join(links)}</div>
    </article>'''


def generate_report(vacancies: list[dict[str, Any]], output_path: Path, generated_at: datetime | None = None) -> None:
    generated_at = generated_at or datetime.now(ZoneInfo("America/Sao_Paulo"))
    today = generated_at.date()
    visible = [v for v in vacancies if _is_recently_closed(v, today) and v.get("thematic_score", 0) > 0]
    visible.sort(key=_sort_key)
    open_count = sum(v.get("status") != "CLOSED" for v in visible)
    new_count = sum(v.get("status") == "NEW" for v in visible)
    official_read_count = sum(v.get("official_check_status") in ("READ", "READ_MULTI") for v in visible)
    states = sorted({v.get("state") for v in visible if v.get("state")})
    institutions = sorted({v.get("institution") for v in visible if v.get("institution")})
    assigned: set[str] = set()

    def take(predicate: Any) -> list[dict[str, Any]]:
        result = [v for v in visible if v.get("id", v["source_url"]) not in assigned and predicate(v)]
        assigned.update(v.get("id", v["source_url"]) for v in result)
        return result

    groups = [
        ("Novas vagas", take(lambda v: v.get("status") == "NEW")),
        ("Forte aderência", take(lambda v: v.get("visual_category") == "🔥 Forte oportunidade" and v.get("status") != "CLOSED")),
        ("Elegibilidade incerta", take(lambda v: v.get("formal_eligibility") in ("UNCERTAIN", "UNKNOWN") and v.get("status") != "CLOSED")),
        ("Outras potencialmente interessantes", take(lambda v: v.get("status") != "CLOSED")),
        ("Encerradas recentemente", take(lambda v: v.get("status") == "CLOSED")),
    ]
    sections = "\n".join(
        f'<section class="vacancy-group"><h3>{_escape(label)}</h3><div class="cards">{"".join(_card(v) for v in items)}</div></section>'
        for label, items in groups if items
    ) or '<div class="empty">Nenhuma vaga relevante registrada ainda. Execute o monitor para atualizar o radar.</div>'
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
<div class="results-heading"><h2>Oportunidades monitoradas</h2><span id="result-count">{len(visible)} resultado(s)</span></div><div id="cards" class="groups">{sections}</div>
</main><footer>Gerado automaticamente · Fonte de descoberta: <a href="{config.PCI_LISTING_URL}">PCI Concursos</a> · Consulte sempre o edital oficial.</footer><script src="assets/app.js"></script></body></html>'''
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8", newline="\n")
