"""Regression tests for cargo extraction and requirement bounding.

Every case here comes from a real PCI notice that the previous version got
wrong, so the file doubles as documentation of what "cortado errado" meant.
"""

from pathlib import Path

import pytest

from src.parser import (
    extract_pci_opportunities,
    extract_pci_opportunities_from_text,
    parse_cargo_item,
    parse_pci_detail,
)
from src.report import (
    _expanded_rows,
    _joined_for_display,
    _structured_requirements,
    generate_report,
)
from src.requirements import (
    condense_requirement,
    extract_requirement_fields,
    split_academic_requirement,
)

from bs4 import BeautifulSoup


FIXTURES = Path(__file__).parent / "fixtures"


def _body(html: str):
    return BeautifulSoup(html, "html.parser").select_one('[itemprop="articleBody"]')


@pytest.fixture(scope="module")
def blumenau():
    html = (FIXTURES / "detail_multi_cargo.html").read_text(encoding="utf-8")
    return parse_pci_detail(html, "https://www.pciconcursos.com.br/noticias/blumenau")


class TestCargoExtraction:
    def test_one_row_per_teaching_cargo(self, blumenau):
        courses = [item["course"] for item in blumenau["pci_opportunities"]]
        assert "Alemão" in courses
        assert "Ciências" in courses
        assert "Computação" in courses

    def test_non_teaching_cargos_are_excluded(self, blumenau):
        cargos = " ".join(item["cargo"] for item in blumenau["pci_opportunities"])
        assert "Enfermeiro" not in cargos
        assert "Psicólogo" not in cargos

    def test_nested_role_wrapper_reduces_to_the_course(self):
        parsed = parse_cargo_item("Professor Licenciado: Área - Professor de Geografia (CR)")
        assert parsed["course"] == "Geografia"
        assert parsed["reserve_only"] is True

    def test_area_group_is_not_part_of_the_course(self):
        parsed = parse_cargo_item("Professor Licenciado - Área III / Matemática (1 vaga + CR)")
        assert parsed["course"] == "Matemática"
        assert parsed["vacancies_count"] == 1

    def test_parenthetical_becomes_the_formation_hint(self):
        parsed = parse_cargo_item("Professor de Computação (Pós ou cursos de aperfeiçoamento)")
        assert parsed["course"] == "Computação"
        assert parsed["requirement_hint"] == "Pós ou cursos de aperfeiçoamento"

    def test_row_number_is_not_part_of_the_course(self):
        # "Professor 1 - Arte (6º ao 9º ano) (5 vagas)" — real PCI notice.
        parsed = parse_cargo_item("Professor 1 - Arte (6º ao 9º ano) (5 vagas)")
        assert parsed["course"] == "Arte"
        assert parsed["vacancies_count"] == 5

    def test_level_in_the_name_puts_the_subject_in_the_parenthetical(self):
        # "Professor II (Educação Física)" — the II is a career level, not a course.
        parsed = parse_cargo_item("Professor II (Educação Física) (cadastro de reserva)")
        assert parsed["course"] == "Educação Física"
        assert parsed["requirement_hint"] is None

    def test_cargo_without_a_stated_subject_has_no_course(self):
        # The notice says only "Professor 20h"; inventing a course would put a
        # workload in the course filter as if it were a discipline.
        assert parse_cargo_item("Professor 20h (5 vagas)")["course"] is None
        assert parse_cargo_item("Professor I (4 vagas + CR)")["course"] is None

    def test_formation_parenthetical_is_still_a_hint_not_a_course(self):
        parsed = parse_cargo_item("Professor de Espanhol (Magistério)")
        assert parsed["course"] == "Espanhol"
        assert parsed["requirement_hint"] == "Magistério"

    def test_edital_pdf_list_is_not_a_cargo(self):
        html = (
            '<div itemprop="articleBody"><ul>'
            '<li class="pdf"><a class="edital-pdf-link" href="#">EDITAL Nº 12/2026 professor</a></li>'
            "</ul></div>"
        )
        assert extract_pci_opportunities(_body(html)) == []

    def test_notice_without_a_cargo_list_yields_nothing(self):
        html = '<div itemprop="articleBody"><p>Vaga para Professor Adjunto.</p></div>'
        assert extract_pci_opportunities(_body(html)) == []


class TestRequirementBounding:
    def test_cargo_table_never_becomes_a_requirement(self, blumenau):
        assert not blumenau["graduation_requirement"]
        assert not blumenau["postgraduate_requirement"]

    def test_reserve_marker_disqualifies_a_segment(self):
        text = (
            "Licenciatura Plena em Pedagogia (Educação Infantil) (Cadastro de Reserva) "
            "Professor com Licenciatura Plena em Educação Física (Cadastro de Reserva)"
        )
        assert split_academic_requirement(text) == {"graduation": [], "postgraduate": []}

    def test_graduation_and_postgraduate_are_separated(self):
        fields = extract_requirement_fields(
            "O processo requer graduação em Ciências Biológicas (Licenciatura ou "
            "Bacharelado) e mestrado ou doutorado em Biologia Vegetal ou Botânica."
        )
        assert fields["graduation_requirement"] == "graduação em Ciências Biológicas (Licenciatura ou Bacharelado)"
        assert fields["postgraduate_requirement"] == "mestrado ou doutorado em Biologia Vegetal ou Botânica"

    def test_medicine_case_keeps_graduation_clean(self):
        fields = extract_requirement_fields(
            "O certame exige graduação em Medicina, com residência médica de dois anos "
            "e título de especialista em Clínica Médica ou áreas afins, além de mestrado."
        )
        assert fields["graduation_requirement"] == "graduação em Medicina"
        assert "residência médica" in fields["postgraduate_requirement"]
        assert "Medicina," not in fields["postgraduate_requirement"]

    def test_consecutive_levels_stay_together(self):
        parts = split_academic_requirement("Mestrado e Doutorado em Música ou áreas correlatas")
        assert parts["postgraduate"] == ["Mestrado e Doutorado em Música ou áreas correlatas"]

    def test_no_field_exceeds_the_segment_bound(self):
        text = "Licenciatura em Artes " + "e formação superior em Letras " * 60
        for values in split_academic_requirement(text).values():
            for value in values:
                assert len(value) <= 300


class TestReportExpansion:
    def _vacancy(self, blumenau):
        return {
            "id": "abc", "title": blumenau["title"], "institution": "Prefeitura de Blumenau",
            "state": "SC", "status": "NEW", "formal_eligibility": "UNKNOWN",
            "thematic_score": 0, "source_url": "https://www.pciconcursos.com.br/noticias/blumenau",
            "graduation_requirement_raw": "licenciatura (conforme quadro dos editais)",
            "pci_opportunities": blumenau["pci_opportunities"],
        }

    def test_one_row_per_cargo(self, blumenau):
        rows = _expanded_rows([self._vacancy(blumenau)])
        assert len(rows) == len(blumenau["pci_opportunities"])
        assert all(row["_is_subvacancy"] for row in rows)

    def test_row_carries_its_own_course(self, blumenau):
        rows = _expanded_rows([self._vacancy(blumenau)])
        assert {row["course"] for row in rows} >= {"Alemão", "Computação", "Geografia"}

    def test_contest_level_requirement_is_not_reused_per_cargo(self, blumenau):
        rows = _expanded_rows([self._vacancy(blumenau)])
        assert all(row["graduation_requirement_raw"] is None for row in rows)

    def test_cargo_hint_lands_in_the_right_column(self, blumenau):
        rows = _expanded_rows([self._vacancy(blumenau)])
        by_course = {row["course"]: row for row in rows}
        graduation, post = _structured_requirements(by_course["Ciências"])
        assert "Licenciatura Curta" in graduation
        assert post == "Não informado"
        _, post_computacao = _structured_requirements(by_course["Computação"])
        assert "Pós ou cursos de aperfeiçoamento" in post_computacao

    def test_cargo_without_a_course_still_gets_a_row(self, blumenau):
        vacancy = {
            "id": "y", "title": "Concurso",
            "pci_opportunities": [
                {"cargo": "Professor 20h (5 vagas)", "course": None,
                 "requirement_hint": None, "vacancies_count": 5, "reserve_only": False},
            ],
        }
        rows = _expanded_rows([vacancy])
        assert len(rows) == 1
        assert rows[0]["title"] == "Professor 20h (5 vagas)"
        assert rows[0]["course"] is None

    def test_display_bounds_a_pile_of_requirement_runs(self):
        assert _joined_for_display([]) == "Não informado"
        long_parts = ["A" * 280, "B" * 280, "C" * 280]
        joined = _joined_for_display(long_parts)
        assert len(joined) <= 400
        assert "no edital" in joined

    def test_notice_without_cargos_still_renders_one_row(self):
        rows = _expanded_rows([{"id": "x", "title": "Vaga única", "pci_opportunities": []}])
        assert len(rows) == 1
        assert rows[0]["_is_subvacancy"] is False

    def test_course_filter_is_rendered(self, blumenau, tmp_path):
        output = tmp_path / "index.html"
        generate_report([self._vacancy(blumenau)], output)
        document = output.read_text(encoding="utf-8")
        assert '<select id="course">' in document
        assert 'data-course="Computação"' in document


class TestCargoRecoveryFromProse:
    """Recovering the cargo list when only the flattened text is stored.

    The <li> reader only runs when a notice is fetched again, so 115 vacancies
    stored before it existed showed "abre vagas" and expanded into nothing —
    UFSCar's 66 cargos among them. Each cargo in the prose ends with its own
    count or reserve marker, which makes the list recoverable at render time.
    """

    UFSCAR = (
        "A Universidade Federal de São Carlos (UFSCar) publicou um edital. "
        "Segundo o edital, as oportunidades são para os cargos de: "
        "Professor de Organizações (1 vaga) "
        "Professor de Engenharia de Produção - Tecnologia e Trabalho (1 vaga) "
        "Professor de Química (3 vagas) "
        "A jornada é de 40 horas semanais."
    )

    def test_recovers_each_cargo_with_its_count(self):
        found = extract_pci_opportunities_from_text(self.UFSCAR)
        assert [item["course"] for item in found] == [
            "Organizações", "Engenharia de Produção - Tecnologia e Trabalho", "Química",
        ]
        assert [item["vacancies_count"] for item in found] == [1, 1, 3]

    def test_trailing_prose_is_not_a_cargo(self):
        cargos = " ".join(i["cargo"] for i in extract_pci_opportunities_from_text(self.UFSCAR))
        assert "jornada" not in cargos.lower()

    def test_edital_heading_does_not_become_part_of_the_cargo(self):
        text = ("as oportunidades são para os cargos de: EDITAL Nº 002/2026 "
                "Professor de Humanas (5 vagas)")
        found = extract_pci_opportunities_from_text(text)
        assert len(found) == 1
        assert found[0]["course"] == "Humanas"
        assert "EDITAL" not in found[0]["cargo"]

    def test_consecutive_cargos_without_their_own_count_are_split(self):
        # Only the last carries the marker; the earlier ones must not be
        # swallowed into a single 160-character "course".
        text = ("as oportunidades são para os cargos de: "
                "Professor do Ensino Fundamental - Ciências "
                "Professor do Ensino Fundamental - Geografia (2 vagas)")
        courses = [i["course"] for i in extract_pci_opportunities_from_text(text)]
        assert courses == [
            "Ensino Fundamental - Ciências", "Ensino Fundamental - Geografia",
        ]

    def test_non_teaching_cargos_are_left_out(self):
        text = ("as oportunidades são para os cargos de: Motorista (Cadastro de Reserva) "
                "Enfermeiro Padrão (1 vaga) Professor PEB II - Matemática (Cadastro de Reserva)")
        found = extract_pci_opportunities_from_text(text)
        assert len(found) == 1
        assert found[0]["course"] == "PEB II - Matemática"
        assert found[0]["reserve_only"] is True

    def test_career_wrapper_is_not_the_course(self):
        assert parse_cargo_item(
            "Professor da Carreira do Magistério Superior - Terapia Ocupacional (1 vaga)"
        )["course"] == "Terapia Ocupacional"

    def test_a_notice_without_the_marker_yields_nothing(self):
        assert extract_pci_opportunities_from_text(
            "A universidade abre uma vaga para Professor Adjunto."
        ) == []


class TestRequirementCleanup:
    """Cutting the edital's prose off the end of a requirement.

    Reading real editais raised coverage but brought their surrounding text
    along: pay, hours and application deadlines were being displayed as if they
    were part of the qualification.
    """

    def test_pay_and_hours_are_not_part_of_the_requirement(self):
        fields = extract_requirement_fields(
            "Requisito: Pós-graduação em Educação Especial e Inclusiva "
            "A carga horária poderá variar entre 20 e 40 horas-aula."
        )
        assert fields["postgraduate_requirement"] == "Pós-graduação em Educação Especial e Inclusiva"

    def test_application_dates_are_not_part_of_the_requirement(self):
        fields = extract_requirement_fields(
            "Requisito: graduação em Medicina. As inscrições vão de 1 a 20 de setembro."
        )
        assert fields["graduation_requirement"] == "graduação em Medicina"

    def test_a_value_cut_mid_word_loses_the_fragment(self):
        # "Graduação e/ou d" was reaching the page; the fragment is noise.
        fields = extract_requirement_fields("Exige-se Graduação e/ou d")
        assert not str(fields["graduation_requirement"] or "").endswith(" d")

    def test_a_complete_requirement_is_left_alone(self):
        fields = extract_requirement_fields("Mestrado em Matemática ou áreas afins")
        assert fields["postgraduate_requirement"] == "Mestrado em Matemática ou áreas afins"


class TestOfficialRequirementInheritance:
    """A cargo row showing what the edital requires of the whole selection.

    Suppressing it made sense while the contest-level value was the PCI cargo
    table in disguise. Once it comes from the edital, hiding it leaves the row
    emptier than the source — so it is shown, marked as general.
    """

    def _vacancy(self, source):
        return {
            "id": "z", "title": "Concurso", "institution": "Prefeitura de Exemplo",
            "requirements_source": source,
            "graduation_requirement_raw": "licenciatura plena em Pedagogia",
            "pci_opportunities": [
                {"cargo": "Professor de Artes (2 vagas)", "course": "Artes",
                 "requirement_hint": None, "vacancies_count": 2, "reserve_only": False},
            ],
        }

    def test_official_requirement_reaches_the_cargo_row(self):
        row = _expanded_rows([self._vacancy("OFFICIAL_PDF")])[0]
        graduation, _ = _structured_requirements(row)
        assert "Pedagogia" in graduation
        assert row["_contest_requirement_is_official"] is True

    def test_pci_summary_requirement_still_does_not(self):
        row = _expanded_rows([self._vacancy("PCI_SUMMARY")])[0]
        assert _structured_requirements(row)[0] == "Não informado"
        assert row["_contest_requirement_is_official"] is False


class TestAreaListNotices:
    """Notices that list teaching areas instead of cargos.

    UFRN writes "as oportunidades são para as seguintes áreas: Demografia
    (Cadastro de reserva)" — a different opening phrase, and items with no role
    word at all. Both had to hold for the notice to expand, so it produced
    nothing while announcing thirty areas.
    """

    UFRN = (
        "A UFRN divulgou um processo seletivo. Segundo o edital, as oportunidades são "
        "para as seguintes áreas: Magistério Superior Direito Processual e Propedêutica "
        "(Cadastro de reserva) Demografia (Cadastro de reserva) Matemática Aplicada "
        "(Cadastro de reserva) A jornada é de 40 horas."
    )

    def test_an_area_list_expands_without_a_role_word(self):
        courses = [i["course"] for i in extract_pci_opportunities_from_text(self.UFRN)]
        assert courses == ["Direito Processual e Propedêutica", "Demografia", "Matemática Aplicada"]

    def test_the_section_heading_is_not_glued_to_the_first_area(self):
        first = extract_pci_opportunities_from_text(self.UFRN)[0]
        assert not first["course"].startswith("Magistério Superior")

    def test_a_cargo_list_still_requires_the_role_word(self):
        # Municipal notices mix trades into the same list; without the role word
        # Merendeira and Motorista would be filed as teaching vacancies.
        text = ("as oportunidades são para os cargos de: Merendeira (1 vaga) "
                "Motorista (2 vagas) Professor de Artes (1 vaga)")
        courses = [i["course"] for i in extract_pci_opportunities_from_text(text)]
        assert courses == ["Artes"]

    def test_a_list_without_any_vacancy_marker_splits_on_the_role_word(self):
        # Catanduva writes them back to back with nothing in between.
        text = ("as oportunidades são para os cargos de: Professor Berçarista "
                "Professor Recreacionista Professor II - Arte A jornada é de 30 horas.")
        courses = [i["course"] for i in extract_pci_opportunities_from_text(text)]
        assert courses == ["Berçarista", "Recreacionista", "II - Arte"]


class TestRequirementCondensing:
    """Reducing a requirement to the degree and its field.

    What a reader scanning a list needs is "Mestrado em Educação ou áreas
    afins", not the paragraph it was lifted from.
    """

    def test_the_field_survives_and_the_prose_does_not(self):
        assert condense_requirement(
            "Doutorado em Sociologia ou Ciência Política ou Antropologia ou Educação, "
            "há 2 (dois) anos, conforme o disposto no edital"
        ) == "Doutorado em Sociologia ou Ciência Política ou Antropologia ou Educação"

    def test_chained_degrees_share_their_field(self):
        # Splitting these would leave "Mestrado" with no area at all.
        assert condense_requirement("Mestrado e Doutorado em Música ou áreas correlatas") == (
            "Mestrado e Doutorado em Música ou áreas correlatas"
        )

    def test_a_second_degree_keeps_its_own_field(self):
        assert condense_requirement(
            "graduação em Medicina / Doutorado em Ciências da Saúde"
        ) == "Graduação em Medicina · Doutorado em Ciências da Saúde"

    def test_a_site_menu_is_not_a_requirement(self):
        assert condense_requirement(
            "Pós-Graduação Pesquisa Extensão Vestibular Unidades Portal da Universidade"
        ) is None

    def test_a_salary_table_is_not_a_requirement(self):
        assert condense_requirement(
            "Completo R$ 2.000,00 30h 1 R$ 110,00 12 Assistente Social Curso Superior"
        ) is None

    def test_the_degree_word_is_dropped_where_the_column_already_says_it(self):
        assert condense_requirement("Graduação em Administração", keep_degree=False) == "Administração"

    def test_a_degree_modifier_is_not_lost(self):
        assert condense_requirement("Licenciatura Curta", keep_degree=False) == "Licenciatura Curta"

    def test_a_vague_restatement_yields_to_the_specific_one(self):
        assert condense_requirement(
            "doutorado na área / Doutorado em Ciências Ambientais"
        ) == "Doutorado em Ciências Ambientais"
