from datetime import datetime
from zoneinfo import ZoneInfo

import config
from monitor import _apply_official_result
from src.classifier import RuleBasedAnalyzer


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
    assert vacancy["graduation_requirement_raw"] == "Requisito resumido do PCI."
    assert "concatenados" not in vacancy["graduation_requirement_raw"]
