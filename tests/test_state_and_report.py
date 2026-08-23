import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.monitoring import compute_status, detect_changes
from src.report import generate_report
from src.storage import RepositoryState


def test_status_and_change_detection():
    today = date(2026, 8, 23)
    assert compute_status({"registration_end": "2026-08-24"}, today) == "CLOSING_SOON"
    assert compute_status({"registration_end": "2026-08-22"}, today) == "CLOSED"
    assert detect_changes(
        {"registration_end": "2026-08-24", "content_hash": "a"},
        {"registration_end": "2026-09-01", "content_hash": "b"},
    ) == ["prazo alterado"]


def test_atomic_json_state_roundtrip(tmp_path):
    store = RepositoryState(tmp_path / "data")
    values = ([{"id": "abc"}], {"url": {"processed": True}}, [{"run_at": "now"}])
    store.save(*values)
    assert store.load() == values
    for path in (store.vacancies_path, store.seen_path, store.history_path):
        json.loads(path.read_text(encoding="utf-8"))


def test_report_is_responsive_and_escapes_content(tmp_path):
    output = tmp_path / "docs" / "index.html"
    vacancy = {
        "source_url": "https://example.test/vaga", "institution": "UFPR <Campus>",
        "state": "PR", "title": "Professor de Gestão", "area": "Gestão Ambiental",
        "formal_eligibility": "YES", "formal_reason": "Compatível", "thematic_score": 90,
        "thematic_reason": "Alta aderência", "geographic_priority": 2, "status": "NEW",
        "first_seen": "2026-08-23", "registration_end": "2026-09-15",
        "visual_category": "🔥 Forte oportunidade",
    }
    generate_report([vacancy], output, datetime(2026, 8, 23, 8, 17, tzinfo=ZoneInfo("America/Sao_Paulo")))
    page = output.read_text(encoding="utf-8")
    assert '<meta name="viewport"' in page
    assert "UFPR &lt;Campus&gt;" in page
    assert "Somente abertas" in page
    assert "Triagem, não decisão jurídica" in page
