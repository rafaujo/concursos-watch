import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.monitoring import compute_status, detect_changes
from src.report import generate_report
from src.storage import RepositoryState


def test_status_and_change_detection():
    today = date(2026, 8, 23)
    assert compute_status({"registration_end": "2026-08-24"}, today) == "CLOSING_SOON"
    assert compute_status({"registration_end": "2026-08-22"}, today) == "CLOSED"
    assert detect_changes(
        {"registration_end": "2026-08-24", "content_hash": "a"},
        {"registration_end": "2026-09-01", "content_hash": "b"},
    ) == ["prazo alterado"]


def test_atomic_json_state_roundtrip(tmp_path):
    store = RepositoryState(tmp_path / "data")
    values = ([{"id": "abc"}], {"url": {"processed": True}}, [{"run_at": "now"}], {"url": {"status": "READ"}})
    store.save(*values)
    assert store.load() == values
    for path in (store.vacancies_path, store.seen_path, store.history_path, store.official_documents_path):
        json.loads(path.read_text(encoding="utf-8"))


def test_report_is_responsive_and_escapes_content(tmp_path):
    output = tmp_path / "docs" / "index.html"
    vacancy = {
        "source_url": "https://example.test/vaga", "institution": "UFPR <Campus>",
        "state": "PR", "title": "Professor de Gestão", "area": "Gestão Ambiental",
        "formal_eligibility": "YES", "formal_reason": "Compatível", "thematic_score": 90,
        "thematic_reason": "Alta aderência", "geographic_priority": 2, "status": "NEW",
        "first_seen": "2026-08-23", "publication_date": "2026-08-20",
        "registration_start": "2026-09-01", "registration_end": "2026-09-15",
        "employment_type": "Concurso público", "vacancies_count": 1,
        "workload": "40 horas semanais", "salary_text": "R$ 10.000,00",
        "graduation_requirement_raw": "Graduação em Administração.",
        "doctorate_requirement_raw": "Doutorado em Administração.",
        "visual_category": "🔥 Forte oportunidade",
        "official_check_status": "READ", "requirements_source": "OFFICIAL_PDF",
        "official_check_reason": "Requisito associado à área.",
        "official_document_url": "https://example.test/edital.pdf",
        "official_requirement_evidence": [{"page": 7, "text": "Graduação em Administração."}],
        "official_pci_protected_documents": [{"label": "EDITAL Nº 1", "pci_link_id": "123"}],
    }
    generate_report([vacancy], output, datetime(2026, 8, 23, 8, 17, tzinfo=ZoneInfo("America/Sao_Paulo")))
    page = output.read_text(encoding="utf-8")
    assert '<meta name="viewport"' in page
    assert "UFPR &lt;Campus&gt;" in page
    assert "Somente abertas" in page
    assert "Triagem, não decisão jurídica" in page
    assert "Concursos e suas vagas" in page
    assert "Vaga ou área" in page
    assert "Requisito de graduação" in page
    assert "Requisito de pós-graduação" in page
    assert "Detalhes da vaga" in page
    assert "20/08/2026" in page
    assert "01/09/2026 a 15/09/2026" in page
    assert "Concurso público" in page
    assert "40 horas semanais" in page
    assert "Graduação em Administração." in page
    assert "Doutorado: Doutorado em Administração." in page
    assert "Edital lido · página 7" in page
    assert ">Abrir edital</a>" in page
    assert page.count('class="contest-group"') == 1
    assert page.count('class="contest-table"') == 1


def test_multi_area_report_does_not_repeat_parent_requirements(tmp_path):
    output = tmp_path / "docs" / "index.html"
    vacancy = {
        "source_url": "https://example.test/vaga", "institution": "UEM", "state": "PR",
        "title": "Professor colaborador", "area": "Não identificada", "status": "OPEN",
        "formal_eligibility": "UNKNOWN", "formal_reason": "Edital multiárea lido.",
        "thematic_score": 50, "thematic_reason": "Duas áreas relevantes.",
        "visual_category": "⚪ Informação insuficiente", "first_seen": "2026-08-23",
        "graduation_requirement_raw": "RESUMO GENÉRICO DO PAI",
        "masters_requirement": "MESTRADO GENÉRICO DO PAI",
        "official_check_status": "READ_MULTI", "requirements_source": "OFFICIAL_PDF_MULTI",
        "official_opportunities": [{
            "area": "Engenharia da Sustentabilidade", "thematic_score": 25,
            "formal_eligibility": "UNKNOWN", "page": 14,
            "requirement_text": "Graduação em Engenharia de Produção; e Mestrado em Engenharia de Produção.",
            "graduation_requirement_raw": "Graduação em Engenharia de Produção.",
        }, {
            "area": "Gestão Ambiental", "thematic_score": 40,
            "formal_eligibility": "YES", "page": 18,
            "requirement_text": "Graduação em Administração e doutorado na área.",
            "graduation_requirement_raw": "Graduação em Administração.",
            "doctorate_requirement_raw": "Doutorado em Ciências Ambientais.",
        }],
    }
    generate_report([vacancy], output, datetime(2026, 8, 23, 8, 17, tzinfo=ZoneInfo("America/Sao_Paulo")))
    page = output.read_text(encoding="utf-8")
    assert "RESUMO GENÉRICO DO PAI" not in page
    assert "MESTRADO GENÉRICO DO PAI" not in page
    assert page.count('class="contest-group"') == 1
    assert page.count('class="contest-table"') == 1
    assert page.count('class="vacancy-card vacancy-row') == 2
    assert "2 vagas relevantes neste concurso" in page
    assert "Engenharia da Sustentabilidade" in page
    assert "Gestão Ambiental" in page
    assert "Graduação em Engenharia de Produção." in page
    assert "Pós-graduação: Mestrado em Engenharia de Produção." in page
    assert "Doutorado: Doutorado em Ciências Ambientais." in page
    assert "Edital lido · página 14" in page
    assert "Edital lido · página 18" in page


def test_report_groups_different_contests_in_separate_tables(tmp_path):
    output = tmp_path / "docs" / "index.html"
    contests = [{
        "source_url": f"https://example.test/concurso-{index}",
        "institution": institution,
        "state": "PR",
        "title": title,
        "area": area,
        "status": "OPEN",
        "formal_eligibility": "UNKNOWN",
        "thematic_score": 20,
    } for index, (institution, title, area) in enumerate((
        ("UFPR", "Concurso nº 1", "Administração"),
        ("UEL", "Concurso nº 2", "Gestão Ambiental"),
    ), start=1)]
    generate_report(contests, output, datetime(2026, 8, 23, 8, 17, tzinfo=ZoneInfo("America/Sao_Paulo")))
    page = output.read_text(encoding="utf-8")
    assert page.count('class="contest-group"') == 2
    assert page.count('class="contest-table"') == 2
    assert page.count('class="vacancy-card vacancy-row') == 2
    assert "2 vaga(s) em 2 concurso(s)" in page
