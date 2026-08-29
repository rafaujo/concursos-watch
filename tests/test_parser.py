from pathlib import Path

from src.parser import extract_requirement_sentences, normalize_text, parse_brazilian_dates, parse_pci_detail
from src.pci import parse_listing


FIXTURES = Path(__file__).parent / "fixtures"


def test_normalize_text_preserves_matching_semantics():
    assert normalize_text("  Ciências   Ambientais  ") == "ciencias ambientais"


def test_brazilian_date_formats_and_ranges():
    text = "23 de agosto de 2026; 23/08/2026; 23-08-2026; até 15 de setembro de 2026"
    assert [item.isoformat() for item in parse_brazilian_dates(text)] == ["2026-08-23", "2026-09-15"]
    range_text = "de 10 de agosto a 2 de setembro de 2026"
    assert [item.isoformat() for item in parse_brazilian_dates(range_text)] == ["2026-08-10", "2026-09-02"]


def test_professor_doutor_job_title_is_not_a_doctorate_requirement():
    requirements = extract_requirement_sentences(
        "Concurso para Professor Doutor no Departamento de Administração. "
        "A titulação exigida deve ser consultada no edital."
    )
    assert requirements["doctorate_requirement"] is None


def test_current_pci_listing_shape():
    records = parse_listing((FIXTURES / "listing.html").read_text(encoding="utf-8"))
    assert len(records) == 1
    vacancy = records[0]
    assert vacancy["institution"] == "UFPR - Universidade Federal do Paraná"
    assert vacancy["state"] == "PR"
    assert vacancy["position"] == "Professor Adjunto"
    assert vacancy["registration_end"] == "2026-09-15"
    assert "?" not in vacancy["source_url"]


def test_current_pci_detail_shape_and_raw_requirements():
    listing = parse_listing((FIXTURES / "listing.html").read_text(encoding="utf-8"))[0]
    vacancy = parse_pci_detail(
        (FIXTURES / "detail.html").read_text(encoding="utf-8"), listing["source_url"], listing
    )
    assert vacancy["publication_date"] == "2026-08-23"
    assert vacancy["registration_start"] == "2026-08-10"
    assert vacancy["registration_end"] == "2026-09-15"
    assert vacancy["workload"] == "40 horas semanais"
    assert vacancy["area"] == "Gestão Socioambiental e Desenvolvimento Sustentável"
    assert "Graduação em Administração" in vacancy["graduation_requirement_raw"]
    assert "Doutorado em Ciências Ambientais" in vacancy["doctorate_requirement_raw"]
    assert vacancy["official_url"] == "https://concursos.ufpr.br/edital-1"
    assert vacancy["pci_documents"] == [{
        "label": "EDITAL Nº 36/2026", "url": None, "pci_link_id": "1703758",
        "pci_news_code": "notice-code", "access": "HUMAN_VERIFICATION_REQUIRED",
    }]
