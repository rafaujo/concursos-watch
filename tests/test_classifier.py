import pytest

from src.classifier import RuleBasedAnalyzer, classify_formal_eligibility, thematic_score


@pytest.mark.parametrize(
    ("graduation", "doctorate", "expected"),
    [
        ("Graduação em Administração.", "Doutorado em Ciências Ambientais.", "YES"),
        ("Graduação em Engenharia Ambiental.", "Doutorado em Ciências Ambientais.", "NO"),
        ("Graduação em Administração.", "Doutorado na Grande Área Multidisciplinar da CAPES.", "YES"),
        ("Graduação em Administração.", "Doutorado na Área de Avaliação Interdisciplinar da CAPES.", "UNCERTAIN"),
        ("Graduação em Administração.", "Doutorado em Administração ou áreas afins.", "UNCERTAIN"),
        ("Graduação em Administração.", "Doutorado em Ciências Sociais Aplicadas.", "UNCERTAIN"),
    ],
)
def test_acceptance_formal_cases(graduation, doctorate, expected):
    result, reason = classify_formal_eligibility(graduation, None, doctorate)
    assert result == expected
    assert reason


def test_capes_interdisciplinary_is_not_environmental_sciences():
    result, reason = classify_formal_eligibility(
        "Graduação em Administração",
        None,
        "Doutorado na Área de Avaliação Interdisciplinar da CAPES",
    )
    assert result == "UNCERTAIN"
    assert "Área de Avaliação Ciências Ambientais" in reason
    assert "não cria equivalência automática" in reason


def test_thematic_and_formal_scores_are_independent():
    score, _ = thematic_score("Gestão Socioambiental e Desenvolvimento Sustentável")
    eligibility, _ = classify_formal_eligibility(
        "Graduação obrigatória em Engenharia Ambiental", None, None
    )
    assert score >= 85
    assert eligibility == "NO"


def test_rule_analyzer_never_turns_clear_incompatibility_into_yes():
    result = RuleBasedAnalyzer().analyze(
        {
            "title": "Professor de Gestão Ambiental",
            "area": "Gestão Socioambiental e Desenvolvimento Sustentável",
            "graduation_requirement_raw": "Graduação obrigatória em Engenharia Ambiental",
            "doctorate_requirement_raw": "Doutorado em Ciências Ambientais",
            "state": "BA",
        },
        {},
    )
    assert result["thematic_score"] >= 85
    assert result["formal_eligibility"] == "NO"
    assert result["geographic_priority"] == 1
