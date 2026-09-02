from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
import requests

import config

from src.official import (
    assess_document_relevance,
    extract_candidate_links,
    extract_requirement_evidence,
    extract_structured_opportunities,
    is_excluded_link,
    edital_numbers_for_display,
    known_edital_numbers,
    OfficialDocumentReader,
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
    assert "não é um edital" in reason


class TestRelevanceGate:
    """What counts as a teaching selection at all.

    Requiring one of ten fixed phrases rejected 94 real editais out of 192
    downloaded PDFs — a municipal notice heads itself "CONCURSO PÚBLICO Nº
    001/2026" and names the cargo in a table, matching none of them. The gate
    now asks two independent questions, and the scoping checks that follow
    still decide whether the document concerns this particular vacancy.
    """

    VAGA = {
        "area": "Não identificada",
        "institution": "Prefeitura de Riolândia",
        "title": "Prefeitura de Riolândia - SP abre concurso para professores",
    }

    def test_accepts_a_municipal_edital_that_never_uses_the_old_phrasing(self):
        texto = (
            "PREFEITURA MUNICIPAL DE RIOLÂNDIA. CONCURSO PÚBLICO Nº 001/2026. "
            "O Prefeito faz saber que estarão abertas as inscrições. "
            "QUADRO DE CARGOS: PROFESSOR DE EDUCAÇÃO BÁSICA I — 5 vagas — "
            "requisito: licenciatura plena em Pedagogia."
        )
        relevant, _ = assess_document_relevance([(1, texto)], self.VAGA, "https://x/edital.pdf")
        assert relevant is True

    @pytest.mark.parametrize("texto", [
        "Aviso de cookies. Este site utiliza cookies para melhorar sua experiência.",
        "Aviso de privacidade. Tratamos seus dados conforme a LGPD.",
        "Decreto Municipal nº 05 de 03 de janeiro de 2024. Dispõe sobre o horário de expediente.",
    ])
    def test_still_rejects_documents_that_are_not_selections(self, texto):
        relevant, _ = assess_document_relevance([(1, texto)], self.VAGA, "https://x/y.pdf")
        assert relevant is False

    def test_rejects_a_selection_with_no_teaching_cargo(self):
        texto = ("CONCURSO PÚBLICO Nº 003/2026 para provimento de cargos de "
                 "Agente Administrativo e Fiscal de Tributos. Inscrições abertas.")
        relevant, reason = assess_document_relevance([(1, texto)], self.VAGA, "https://x/y.pdf")
        assert relevant is False
        assert "cargo docente" in reason


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


def test_pci_candidate_links_use_article_context_and_ignore_recommendations():
    html = '''<html><body><article id="noticia">
      <div itemprop="articleBody">
        <p>As inscrições são feitas no <a href="https://universidade.example/admissao">portal da universidade</a>.</p>
      </div>
      <aside><a href="/noticias/outro-edital-para-professor">Outro edital para professor</a></aside>
    </article></body></html>'''.encode("utf-8")
    links = extract_candidate_links(
        html, "https://www.pciconcursos.com.br/noticias/vaga-atual",
        {"title": "Concurso para professor", "area": "Não identificada"},
    )
    assert [item["url"] for item in links] == ["https://universidade.example/admissao"]


def test_reader_starts_at_pci_and_records_protected_edital(monkeypatch):
    pci_url = "https://www.pciconcursos.com.br/noticias/vaga-docente"
    official_url = "https://universidade.example/concursos"
    pci_html = b'''<html><body><article id="noticia">
      <div itemprop="articleBody"><p>Concurso para professor.</p></div>
      <a class="edital-pdf-link" href="javascript:void(0)" data-code="abc" data-link="123">EDITAL 4/2026</a>
    </article></body></html>'''
    official_html = b"<html><body>Portal geral da universidade</body></html>"
    reader = OfficialDocumentReader(requests.Session(), delay=0)
    calls = []

    def fake_fetch(url):
        calls.append(url)
        if url == pci_url:
            return pci_html, pci_url, "text/html", False
        return official_html, official_url, "text/html", False

    monkeypatch.setattr(reader, "_fetch", fake_fetch)
    result = reader.read(
        {"source_url": pci_url, "official_url": official_url, "title": "Concurso para professor", "area": "Não identificada"},
        datetime(2026, 8, 29, 8, 17, tzinfo=ZoneInfo("America/Sao_Paulo")),
    )
    assert calls[0] == pci_url
    assert result["status"] == "BLOCKED"
    assert result["pci_protected_documents"][0]["pci_link_id"] == "123"
    assert "verificação humana" in result["reason"]


def test_official_cache_ttl_depends_on_status():
    today = date(2026, 8, 23)
    assert should_check_official(None, today)
    current = {"reader_version": config.OFFICIAL_READER_VERSION}
    assert not should_check_official({**current, "checked_at": "2026-08-20", "status": "READ"}, today)
    assert should_check_official({**current, "checked_at": "2026-08-01", "status": "READ"}, today)
    assert should_check_official({**current, "checked_at": "2026-08-20", "status": "AMBIGUOUS"}, today)
    assert should_check_official({**current, "checked_at": "2026-08-20", "status": "ERROR"}, today)
    assert should_check_official({"reader_version": config.OFFICIAL_READER_VERSION - 1, "checked_at": "2026-08-20", "status": "READ"}, today)


class TestIncompleteTlsChain:
    """Reading from a server that omits its intermediate certificate.

    UNESP, UNICAMP and UFMG serve valid certificates but do not send the
    intermediate, so the chain cannot be built and 18 university vacancies were
    left without requirements. The retry exists for that case only.
    """

    def _reader(self, monkeypatch, error_message, on_retry=b"conteudo"):
        reader = OfficialDocumentReader(requests.Session(), delay=0)
        calls = []

        class FakeResponse:
            url = "https://universidade.example/edital.pdf"
            headers = {"Content-Type": "application/pdf"}
            history = []

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size=0):
                yield on_retry

        def fake_get(url, **kwargs):
            calls.append(kwargs.get("verify", True))
            if kwargs.get("verify", True) is not False:
                raise requests.exceptions.SSLError(error_message)
            return FakeResponse()

        monkeypatch.setattr(reader.session, "get", fake_get)
        monkeypatch.setattr("src.official.is_public_http_url", lambda url: True)
        return reader, calls

    def test_missing_intermediate_is_retried_and_marked(self, monkeypatch):
        reader, calls = self._reader(
            monkeypatch,
            "HTTPSConnectionPool: certificate verify failed: unable to get local issuer certificate",
        )
        data, url, content_type, tls_unverified = reader._fetch("https://universidade.example/edital.pdf")
        assert data == b"conteudo"
        assert tls_unverified is True
        assert calls == [True, False], "deve tentar verificado antes de reler sem verificação"

    @pytest.mark.parametrize("message", [
        "certificate verify failed: certificate has expired",
        "certificate verify failed: Hostname mismatch, certificate is not valid for 'x'",
        "certificate verify failed: self signed certificate",
    ])
    def test_other_certificate_problems_stay_refused(self, monkeypatch, message):
        reader, calls = self._reader(monkeypatch, message)
        with pytest.raises(requests.exceptions.SSLError):
            reader._fetch("https://universidade.example/edital.pdf")
        assert calls == [True], "não pode reler sem verificação nesses casos"

    def test_the_retry_can_be_switched_off(self, monkeypatch):
        monkeypatch.setattr(config, "OFFICIAL_ALLOW_INCOMPLETE_CHAIN", False)
        reader, calls = self._reader(
            monkeypatch, "certificate verify failed: unable to get local issuer certificate"
        )
        with pytest.raises(requests.exceptions.SSLError):
            reader._fetch("https://universidade.example/edital.pdf")
        assert calls == [True]


def test_frontier_explores_the_best_link_before_weaker_ones(monkeypatch):
    """The budget must go to the strongest candidate found anywhere.

    With a deque, the links a generic homepage offers were explored before the
    next seed, so eight mediocre links from one portal could exhaust the budget
    while the edital sat one seed away.
    """
    pci_url = "https://www.pciconcursos.com.br/noticias/vaga"
    portal = "https://universidade.example/"
    pci_html = b'<html><body><article id="noticia"><div itemprop="articleBody">' \
               b"<p>Concurso para professor.</p></div></article></body></html>"
    portal_html = (
        '<html><body>'
        '<a href="https://universidade.example/fale-conosco">Fale conosco</a>'
        '<a href="https://universidade.example/noticias">Notícias da semana</a>'
        '<a href="https://universidade.example/edital-professor.pdf">Edital de concurso para professor</a>'
        "</body></html>"
    ).encode()

    reader = OfficialDocumentReader(requests.Session(), delay=0)
    visited = []

    def fake_fetch(url):
        visited.append(url)
        if url == pci_url:
            return pci_html, pci_url, "text/html", False
        return portal_html, url, "text/html", False

    monkeypatch.setattr(reader, "_fetch", fake_fetch)
    reader.read(
        {"source_url": pci_url, "official_url": portal, "title": "Concurso para professor",
         "area": "Não identificada"},
        datetime(2026, 8, 30, 8, 17, tzinfo=ZoneInfo("America/Sao_Paulo")),
    )
    followed = [u for u in visited if u not in (pci_url, portal)]
    assert followed, "o crawler deveria seguir algum link do portal"
    assert "edital-professor.pdf" in followed[0], f"seguiu {followed[0]} antes do edital"


class TestLinkExclusion:
    """Not spending an access on a page that cannot hold an edital.

    79% of vacancies were exhausting the crawl budget, and 17% of accesses went
    to pages that never contain one — an access wasted here is an edital not
    read somewhere else.
    """

    @pytest.mark.parametrize("url", [
        "https://www.threads.com/login?next=x",
        "https://www.facebook.com/universidade",
        "https://podcast.unesp.br/18854/algum-episodio",
        "https://jornal.unesp.br/2026/08/28/a-odisseia-e-tema-do-prato-do-dia/",
        "https://universidade.example/busca?q=edital",
        "https://universidade.example/fale-conosco",
        "https://www.pciconcursos.com.br/apostilas/prefeitura-de-limeira-sp",
    ])
    def test_useless_links_are_excluded(self, url):
        assert is_excluded_link(url) is True

    @pytest.mark.parametrize("url", [
        # Real edital announcements found on municipal news pages. Blocking
        # /noticia/ outright cost three genuine documents.
        "https://www.vgsul.sp.gov.br/noticia/7307/a-prefeitura-publicou-edital-de-concurso-publico-para-diversos-cargos/",
        "https://www.vgsul.sp.gov.br/noticia/7308/estao-abertas-as-inscricoes-para-o-concurso-publico-n-0012026/",
        "https://anicuns.go.gov.br/noticias",
        "https://universidade.example/concursos/edital-12-2026.pdf",
        "https://inscricoes.unesp.br/",
        "https://www.pciconcursos.com.br/noticias/unesp-abre-concurso",
    ])
    def test_plausible_sources_are_kept(self, url):
        assert is_excluded_link(url) is False

    def test_news_links_are_deprioritised_below_edital_links(self):
        vacancy = {"area": "Fonoaudiologia", "title": "Concurso para professor"}
        edital = score_candidate_link(
            "Edital de concurso", "https://universidade.example/edital-12-2026.pdf", vacancy
        )
        noticia = score_candidate_link(
            "Notícias da semana", "https://universidade.example/noticias/semana", vacancy
        )
        assert edital > noticia

    def test_a_news_page_that_names_the_edital_still_competes(self):
        vacancy = {"area": "Não identificada", "title": "Concurso público"}
        assert score_candidate_link(
            "Prefeitura publicou edital de concurso público",
            "https://cidade.sp.gov.br/noticia/7307/edital-de-concurso-publico/",
            vacancy,
        ) >= 20, "precisa continuar acima do corte de aceitação"


def test_right_institution_wrong_area_is_still_rejected():
    """Institution alone must not override a known area.

    UNESP runs many simultaneous concursos. A document that is unmistakably
    UNESP's is still the wrong document for a UNESP vacancy in another field,
    and attributing its requirements would be worse than reporting none.
    """
    relevant, reason = assess_document_relevance(
        [(1, "UNESP. Edital de concurso público para professor substituto na área de Fonoaudiologia.")],
        {"area": "Música e Tecnologia", "institution": "UNESP - Universidade Estadual Paulista",
         "title": "UNESP abre concurso para professor de Música"},
        "https://www2.unesp.br/edital.pdf",
    )
    assert relevant is False
    assert "área" in reason


def test_institution_name_scopes_a_notice_without_an_area():
    relevant, reason = assess_document_relevance(
        [(1, "PREFEITURA MUNICIPAL DE RIOLÂNDIA. CONCURSO PÚBLICO Nº 001/2026. "
             "Cargo: PROFESSOR DE EDUCAÇÃO BÁSICA I. Requisito: licenciatura em Pedagogia.")],
        {"area": "Não identificada", "institution": "Prefeitura de Riolândia",
         "title": "Prefeitura de Riolândia - SP abre concurso"},
        "https://riolandia.sp.gov.br/edital.pdf",
    )
    assert relevant is True
    assert "riolandia" in reason.lower()


class TestEditalNumberFromProtectedLabel:
    """Using the one thing PCI leaves visible on a gated edital.

    The PDF sits behind human verification, but the link's label — "EDITAL DE
    ABERTURA Nº 005/2026" — is in the page. That number is present for 120 of
    the 149 blocked vacancies, against 7 whose prose states one, and it is the
    strongest identifier available for exactly the documents we cannot fetch.
    """

    VAGA = {
        "institution": "Prefeitura de Massaranduba",
        "title": "Prefeitura de Massaranduba - SC abre concurso para professores",
        "official_pci_protected_documents": [
            {"label": "EDITAL DE ABERTURA Nº 005/2026", "url": None},
            {"label": "EDITAL RETIFICADO Nº 001/2026", "url": None},
        ],
    }

    def test_numbers_are_read_from_the_protected_labels(self):
        assert known_edital_numbers(self.VAGA) == {"5/26", "1/26"}

    def test_prose_numbers_still_count(self):
        vaga = {"title": "x", "raw_text": "Segundo o Edital nº 144/2026, as inscrições..."}
        assert known_edital_numbers(vaga) == {"144/26"}

    def test_a_vacancy_with_no_number_anywhere_yields_none(self):
        assert known_edital_numbers({"title": "Concurso para professor"}) == set()

    def test_a_matching_number_scopes_the_document(self):
        relevant, reason = assess_document_relevance(
            [(1, "PREFEITURA DE MASSARANDUBA. EDITAL DE CONCURSO PÚBLICO Nº 005/2026. "
                 "Cargo de PROFESSOR. Requisito: licenciatura plena.")],
            self.VAGA, "https://massaranduba.sc.gov.br/e.pdf",
        )
        assert relevant is True
        assert "5/26" in reason

    def test_a_link_carrying_the_number_outranks_everything_else(self):
        edital = score_candidate_link(
            "Edital 005/2026", "https://x.sc.gov.br/edital-005-2026.pdf", self.VAGA
        )
        outro = score_candidate_link(
            "Edital de concurso público", "https://x.sc.gov.br/concursos/edital.pdf", self.VAGA
        )
        assert edital > outro


def test_edital_number_is_displayed_as_the_institution_writes_it():
    vaga = {"official_pci_protected_documents": [
        {"label": "EDITAL DE ABERTURA Nº 005/2026"}, {"label": "EDITAL Nº 1/2026"},
    ]}
    # Matching normalises the year; the reader needs the original spelling to
    # find the document on the institution's site.
    assert edital_numbers_for_display(vaga) == ["005/2026", "1/2026"]
    assert known_edital_numbers(vaga) == {"5/26", "1/26"}
