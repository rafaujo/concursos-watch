import pytest

from src.classifier import (
    RuleBasedAnalyzer,
    classify_formal_eligibility,
    institution_type,
    thematic_score,
)


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


class TestInstitutionType:
    """Separating university/IF postings from municipal basic education.

    Every case below is a real institution from the radar. The municipal ones
    matter most: they advertise "Professor Assistente" and "Professor Adjunto"
    for basic education, so a cargo-based rule reads them as university posts.
    """

    @pytest.mark.parametrize("institution", [
        "UFMG - Universidade Federal de Minas Gerais",
        "IFBA - Instituto Federal da Bahia",
        "UNESP - Universidade Estadual Paulista",
        "UTFPR - Universidade Tecnológica Federal do Paraná",
        "CEFET-MG",
        "UEMS - Universidade Estadual de Mato Grosso do Sul",
    ])
    def test_higher_education_institutions(self, institution):
        assert institution_type({"institution": institution}) == "SUPERIOR"

    @pytest.mark.parametrize("institution", [
        "Prefeitura de Blumenau",
        "SME - Secretaria Municipal de Educação de Belo Horizonte",
        "SEDUC - Secretaria da Educação do Estado do Ceará",
        "Câmara de Redentora",
    ])
    def test_basic_education_institutions(self, institution):
        assert institution_type({"institution": institution}) == "BASICA"

    def test_municipal_cargo_words_do_not_promote_to_higher_education(self):
        # Real case: the cargo is "Professor Assistente de Educação Básica I".
        vacancy = {
            "institution": "Prefeitura de Ribeirão Bonito",
            "position": "Professor Assistente",
            "title": "Prefeitura de Ribeirão Bonito - SP abre processo seletivo para a educação",
        }
        assert institution_type(vacancy) == "BASICA"

    def test_municipal_foundation_running_a_college_is_higher_education(self):
        vacancy = {"institution": "FIMES - Fundação Integrada Municipal de Ensino Superior"}
        assert institution_type(vacancy) == "SUPERIOR"

    def test_explicit_higher_education_wording_overrides_a_municipal_name(self):
        vacancy = {
            "institution": "Prefeitura de Exemplo",
            "title": "Concurso para Professor do Magistério Superior",
        }
        assert institution_type(vacancy) == "SUPERIOR"

    def test_unclassifiable_stays_undefined(self):
        # Guessing here would either hide a real opportunity or pollute the list.
        assert institution_type({"institution": "Fundação InoversaSul"}) == "INDEFINIDA"
        assert institution_type({"institution": "Marinha do Brasil"}) == "INDEFINIDA"
