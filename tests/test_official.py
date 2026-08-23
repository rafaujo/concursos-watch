from datetime import date

from src.official import (
    assess_document_relevance,
    extract_candidate_links,
    extract_requirement_evidence,
    extract_structured_opportunities,
    score_candidate_link,
    should_check_official,
)


def test_pdf_evidence_is_scoped_to_vacancy_area():
    pages = [
        (1, "Área: Clínica Médica\nRequisitos\nGraduação em Medicina\nDoutorado em Medicina"),
        (
            2,
            "Área: Gestão Ambiental e Sustentabilidade\nRequisitos mínimos\n"
            "Graduação em Administração\nDoutorado em Ciências Ambientais",
        ),
    ]
    result = extract_requirement_evidence(
        pages,
        {"area": "Gestão Ambiental e Sustentabilidade", "title": "Professor de Gestão Ambiental"},
        allow_unscoped=True,
    )
    assert result["applicable"] is True
    assert result["confidence"] == "HIGH"
    assert "Administração" in result["requirements"]["graduation_requirement_raw"]
    assert "Ciências Ambientais" in result["requirements"]["doctorate_requirement_raw"]
    assert {item["page"] for item in result["evidence"]} == {2}


def test_multi_area_edital_without_context_stays_ambiguous():
    pages = [
        (number, f"Área específica {number}\nGraduação em Curso {number}\nDoutorado em Área {number}")
        for number in range(1, 7)
    ]
    result = extract_requirement_evidence(
        pages,
        {"area": "Não identificada", "title": "Universidade publica edital para professores"},
        allow_unscoped=True,
    )
    assert result["applicable"] is False
    assert result["confidence"] == "AMBIGUOUS"


def test_aggregated_pci_card_never_combines_many_official_requirements():
    pages = [(1, "\n".join(
        f"Área {number}\nRequisito(s) Graduação em Curso {number}\nDoutorado em Campo {number}\nProva Didática e Avaliação de Títulos"
        for number in range(1, 8)
    ))]
    result = extract_requirement_evidence(
        pages,
        {
            "area": "Não identificada",
            "title": "Universidade abre teste seletivo para professores colaboradores",
            "description": "Prova didática e avaliação de títulos",
        },
        allow_unscoped=True,
    )
    assert result["applicable"] is False
    assert result["confidence"] == "AMBIGUOUS"
    assert "várias áreas" in result["reason"]


def test_structured_table_blocks_become_independent_opportunities():
    page = """
    Área de conhecimento ou matéria(s) (01) Gestão Socioambiental
    Nº de vaga(s) e carga horária 1 vaga / 40 horas semanais - DTD 00014-2-0-40
    Local de atuação Câmpus Sede
    Requisito(s) Graduação em Administração; Doutorado em Ciências Ambientais
    Tipos de prova Prova Didática e Avaliação de Títulos e Currículo
    Área de conhecimento ou matéria(s) (02) Engenharia Agrícola
    Nº de vaga(s) e carga horária 1 vaga / 20 horas semanais - DTD 00015-2-0-20
    Local de atuação Câmpus Regional
    Requisito(s) Graduação em Engenharia Agrícola; Doutorado em Engenharia
    Tipos de prova Prova Didática
    """
    opportunities = extract_structured_opportunities([(4, page)])
    assert len(opportunities) == 2
    assert opportunities[0]["area"] == "(01) Gestão Socioambiental"
    assert opportunities[0]["reference"] == "DTD 00014-2-0-40"
    assert opportunities[0]["page"] == 4
    assert "Administração" in opportunities[0]["graduation_requirement_raw"]
    assert opportunities[1]["workload"] == "20 horas semanais"


def test_document_relevance_rejects_unrelated_institutional_pdf():
    vacancy = {"area": "Não identificada", "title": "UEL abre teste seletivo para professores temporários"}
    relevant, reason = assess_document_relevance(
        [(1, "Manual do Vestibular 2027. Inscrições para ingresso nos cursos de graduação.")],
        vacancy,
        "https://sites.uel.br/manual-vestibular.pdf",
    )
    assert relevant is False
    assert "seleção para cargo docente" in reason


def test_document_relevance_accepts_matching_teaching_area():
    relevant, reason = assess_document_relevance(
        [(1, "Processo seletivo simplificado para professor. Área: Gestão Ambiental e Sustentabilidade.")],
        {"area": "Gestão Ambiental", "title": "Professor de Gestão Ambiental"},
        "https://universidade.example/edital-42.pdf",
    )
    assert relevant is True
    assert "gestao" in reason.lower()


def test_candidate_links_prioritize_edital_pdf_and_ignore_results():
    html = '''<html><body>
      <a href="/docs/edital-professor-gestao-ambiental.pdf">Edital Professor Gestão Ambiental</a>
      <a href="/docs/resultado-final.pdf">Resultado final</a>
      <a href="/contato">Contato</a>
      <a href="https://twitter.com/universidade/status/1">Edital do concurso</a>
      <a href="/concursos/formulario-de-recurso.pdf">Formulário de recurso</a>
    </body></html>'''.encode("utf-8")
    vacancy = {"area": "Gestão Ambiental", "title": "Professor de Gestão Ambiental"}
    links = extract_candidate_links(html, "https://universidade.example/concursos/", vacancy)
    assert links[0]["url"] == "https://universidade.example/docs/edital-professor-gestao-ambiental.pdf"
    assert links[0]["score"] > score_candidate_link("Resultado final", "https://universidade.example/docs/resultado-final.pdf", vacancy)
    assert all("contato" not in item["url"] for item in links)
    assert all("twitter.com" not in item["url"] for item in links)
    assert all("formulario-de-recurso" not in item["url"] for item in links)


def test_official_cache_ttl_depends_on_status():
    today = date(2026, 8, 23)
    assert should_check_official(None, today)
    current = {"reader_version": 2}
    assert not should_check_official({**current, "checked_at": "2026-08-20", "status": "READ"}, today)
    assert should_check_official({**current, "checked_at": "2026-08-01", "status": "READ"}, today)
    assert should_check_official({**current, "checked_at": "2026-08-20", "status": "ERROR"}, today)
    assert should_check_official({"reader_version": 1, "checked_at": "2026-08-20", "status": "READ"}, today)
