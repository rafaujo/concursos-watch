from src.parser import extract_requirement_sentences
from src.requirements import graduation_for_display, split_academic_requirement


def test_program_name_is_not_an_undergraduate_requirement():
    result = extract_requirement_sentences(
        "Professor Visitante no Programa de Pós-Graduação em Engenharia Civil e Ambiental. "
        "A seleção exige doutorado em Engenharia Civil ou áreas afins."
    )
    assert result["graduation_requirement"] is None
    assert result["postgraduate_requirement"] == "doutorado em Engenharia Civil ou áreas afins"


def test_alternating_compound_requirements_keep_every_branch():
    parts = split_academic_requirement(
        "Graduação em Engenharia de Produção e Mestrado em Engenharias ou áreas afins; "
        "ou Graduação em Engenharias ou Ciências da Computação e Mestrado em Engenharia de Produção"
    )
    assert [graduation_for_display(value) for value in parts["graduation"]] == [
        "Engenharia de Produção",
        "Engenharias ou Ciências da Computação",
    ]
    assert parts["postgraduate"] == [
        "Mestrado em Engenharias ou áreas afins",
        "Mestrado em Engenharia de Produção",
    ]


def test_clinical_medicine_keeps_only_medicine_in_graduation():
    result = extract_requirement_sentences(
        "O certame exige graduação em Medicina, com residência médica de dois anos e "
        "título de especialista em Clínica Médica ou áreas afins, além de mestrado."
    )
    assert result["graduation_requirement"] == "graduação em Medicina"
    assert result["postgraduate_requirement"] == (
        "residência médica de dois anos e título de especialista em Clínica Médica "
        "ou áreas afins, além de mestrado"
    )


def test_postgraduate_only_requirement_does_not_invent_graduation():
    result = extract_requirement_sentences(
        "A titulação mínima exigida é Mestrado em Ciência da Computação ou áreas afins."
    )
    assert result["graduation_requirement"] is None
    assert result["masters_requirement"] == "Mestrado em Ciência da Computação ou áreas afins"


def test_citology_doctorate_stays_whole_in_postgraduate_field():
    result = extract_requirement_sentences(
        "O certame exige doutorado nas áreas de Ciências Biológicas, Ciências da Saúde, "
        "Zootecnia, Medicina Veterinária, Bioengenharia ou áreas afins."
    )
    assert result["graduation_requirement"] is None
    assert result["postgraduate_requirement"] == (
        "doutorado nas áreas de Ciências Biológicas, Ciências da Saúde, Zootecnia, "
        "Medicina Veterinária, Bioengenharia ou áreas afins"
    )
