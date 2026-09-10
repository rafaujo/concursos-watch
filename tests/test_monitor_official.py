from datetime import datetime
from zoneinfo import ZoneInfo

import config
from monitor import _apply_official_result, _matches_official_filter
from src.classifier import RuleBasedAnalyzer


def test_short_official_filter_matches_acronym_not_name_fragment():
    assert _matches_official_filter(
        {"institution": "UEL - Universidade Estadual de Londrina", "title": "PSS docente"},
        "UEL",
    )
    assert not _matches_official_filter(
        {"institution": "Prefeitura de São Miguel do Araguaia", "title": "Seleção docente"},
        "UEL",
    )


def test_multi_area_official_result_classifies_each_sub_vacancy_independently():
    vacancy = {
        "title": "Universidade abre edital com vagas docentes",
        "position": "Professor Colaborador",
        "area": "Não identificada",
        "raw_text": "Edital com várias áreas.",
        "pci_graduation_requirement_raw": "Requisito resumido do PCI.",
        "graduation_requirement_raw": "Requisitos indevidamente concatenados.",
        "state": "PR",
        "formal_eligibility": "UNKNOWN",
        "thematic_score": 20,
        "status": "OPEN",
    }
    result = {
        "status": "READ_MULTI", "checked_at": "2026-08-23T08:17:00-03:00",
        "document_url": "https://universidade.example/edital.pdf", "document_type": "PDF",
        "content_hash": "abc", "confidence": "STRUCTURED", "applicable": False,
        "reason": "Edital multiárea lido.", "documents": [], "errors": [],
        "opportunities": [
            {
                "area": "Gestão Socioambiental e Desenvolvimento Sustentável",
                "requirement_text": "Graduação em Engenharia Ambiental. Doutorado em Ciências Ambientais.",
                "graduation_requirement_raw": "Graduação em Engenharia Ambiental.",
                "masters_requirement_raw": None,
                "doctorate_requirement_raw": "Doutorado em Ciências Ambientais.",
                "page": 3,
            },
            {
                "area": "Administração Pública e Sustentabilidade",
                "requirement_text": "Graduação em Administração. Doutorado em Ciências Ambientais.",
                "graduation_requirement_raw": "Graduação em Administração.",
                "masters_requirement_raw": None,
                "doctorate_requirement_raw": "Doutorado em Ciências Ambientais.",
                "page": 7,
            },
        ],
    }
    changed = _apply_official_result(
        vacancy, result, RuleBasedAnalyzer(),
        datetime(2026, 8, 23, 8, 17, tzinfo=ZoneInfo("America/Sao_Paulo")),
    )
    assert changed is True
    assert vacancy["formal_eligibility"] == "YES"
    assert vacancy["requirements_source"] == "OFFICIAL_PDF_MULTI"
    assert vacancy["official_opportunities"][0]["formal_eligibility"] == "NO"
    assert vacancy["official_opportunities"][1]["formal_eligibility"] == "YES"
    assert vacancy["thematic_score"] >= config.STRONG_YES_SCORE
    assert vacancy["graduation_requirement_raw"] is None


def test_discarded_document_reparses_pci_summary_instead_of_using_stale_snapshot():
    vacancy = {
        "title": "Concurso para Professor Doutor no Departamento de Administração",
        "raw_text": "Concurso para Professor Doutor no Departamento de Administração. Consulte o edital.",
        "state": "SP", "area": "Políticas Públicas", "status": "OPEN",
        "formal_eligibility": "NO", "thematic_score": 20,
        "doctorate_requirement_raw": "Professor Doutor no Departamento de Administração.",
        "pci_doctorate_requirement_raw": "Professor Doutor no Departamento de Administração.",
    }
    result = {
        "status": "BLOCKED", "checked_at": "2026-08-29T08:17:00-03:00",
        "reason": "O PCI exige verificação humana.", "documents": [], "errors": [],
        "applicable": False, "pci_protected_documents": [{"pci_link_id": "123"}],
    }
    changed = _apply_official_result(
        vacancy, result, RuleBasedAnalyzer(),
        datetime(2026, 8, 29, 8, 17, tzinfo=ZoneInfo("America/Sao_Paulo")),
    )
    assert changed is True
    assert vacancy["doctorate_requirement_raw"] is None
    assert vacancy["formal_eligibility"] == "UNKNOWN"


def test_html_multi_result_updates_source_and_registration_window():
    vacancy = {
        "title": "UFSCar abre concurso com diversas vagas",
        "position": "Professor Assistente A",
        "area": "Não identificada",
        "raw_text": "Edital com várias áreas.",
        "state": "SP",
        "formal_eligibility": "UNKNOWN",
        "thematic_score": 0,
        "status": "CLOSED",
        "registration_end": "2026-08-25",
    }
    result = {
        "status": "READ_MULTI", "checked_at": "2026-09-07T08:00:00-03:00",
        "document_type": "HTML", "content_hash": "html", "confidence": "STRUCTURED",
        "applicable": False, "reason": "Tabela oficial lida.", "documents": [], "errors": [],
        "registration_start": "2026-08-21", "registration_end": "2026-09-11",
        "opportunities": [{
            "area": "Organizações", "requirement_text": "Doutorado em Administração",
            "graduation_requirement_raw": None,
            "postgraduate_requirement_raw": "Doutorado em Administração",
            "masters_requirement_raw": None,
            "doctorate_requirement_raw": "Doutorado em Administração",
            "requirements_complete": True,
        }],
    }
    _apply_official_result(
        vacancy, result, RuleBasedAnalyzer(),
        datetime(2026, 9, 7, 8, tzinfo=ZoneInfo("America/Sao_Paulo")),
    )
    assert vacancy["requirements_source"] == "OFFICIAL_HTML_MULTI"
    assert vacancy["registration_end"] == "2026-09-11"
    assert vacancy["status"] != "CLOSED"


def test_old_official_date_does_not_replace_current_pci_registration_window():
    vacancy = {
        "title": "UFRPE abre concurso com vagas para professores",
        "position": "Professor do Magistério Superior",
        "institution": "UFRPE - Universidade Federal Rural de Pernambuco",
        "raw_text": (
            "As inscrições deverão ser feitas de 14 de setembro de 2026 a "
            "13 de outubro de 2026 pelo site da UFRPE."
        ),
        "registration_start": "2026-09-14",
        "registration_end": "2026-10-13",
        "status": "OPEN",
        "formal_eligibility": "UNKNOWN",
        "thematic_score": 0,
    }
    result = {
        "status": "READ_MULTI", "checked_at": "2026-09-10T13:02:00-03:00",
        "document_type": "PDF", "content_hash": "pdf", "confidence": "STRUCTURED",
        "applicable": False, "reason": "Tabela oficial lida.", "documents": [], "errors": [],
        # An unrelated 2025 procedural date found in the edital.
        "registration_start": None, "registration_end": "2025-06-03",
        "opportunities": [{
            "area": "História Antiga e Medieval",
            "requirement_text": "Licenciatura em História. Doutorado em História.",
            "graduation_requirement_raw": "Licenciatura em História",
            "postgraduate_requirement_raw": "Doutorado em História",
            "masters_requirement_raw": None,
            "doctorate_requirement_raw": "Doutorado em História",
            "requirements_complete": True,
        }],
    }

    _apply_official_result(
        vacancy, result, RuleBasedAnalyzer(),
        datetime(2026, 9, 10, 13, 2, tzinfo=ZoneInfo("America/Sao_Paulo")),
    )

    assert vacancy["registration_start"] == "2026-09-14"
    assert vacancy["registration_end"] == "2026-10-13"
    assert vacancy["status"] != "CLOSED"
    assert vacancy["official_check_status"] == "READ_MULTI"
    assert vacancy["official_opportunities"][0]["graduation_requirement_raw"] == "Licenciatura em História"
