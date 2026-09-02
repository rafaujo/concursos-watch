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
from src.requirements import extract_requirement_fields, split_academic_requirement

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
