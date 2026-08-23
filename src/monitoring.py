"""State transition helpers kept separate from network orchestration."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import config


TRACKED_CHANGE_FIELDS = (
    "title", "salary_text", "registration_start", "registration_end",
    "graduation_requirement_raw", "masters_requirement_raw", "doctorate_requirement_raw",
)


def detect_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    labels = {
        "title": "título alterado", "salary_text": "remuneração alterada",
        "registration_start": "início das inscrições alterado",
        "registration_end": "prazo alterado", "graduation_requirement_raw": "requisito de graduação alterado",
        "masters_requirement_raw": "requisito de mestrado alterado", "doctorate_requirement_raw": "requisito de doutorado alterado",
    }
    changes = [labels[field] for field in TRACKED_CHANGE_FIELDS if old.get(field) != new.get(field)]
    if old.get("content_hash") and old.get("content_hash") != new.get("content_hash") and not changes:
        changes.append("conteúdo alterado")
    return changes


def compute_status(vacancy: dict[str, Any], today: date, is_new: bool = False, is_updated: bool = False) -> str:
    end_value = vacancy.get("registration_end")
    try:
        end = date.fromisoformat(end_value) if end_value else None
    except ValueError:
        end = None
    if end and end < today:
        return "CLOSED"
    if is_updated:
        return "UPDATED"
    if is_new:
        return "NEW"
    if end and 0 <= (end - today).days <= config.CLOSING_SOON_DAYS:
        return "CLOSING_SOON"
    return "OPEN" if end else "UNCERTAIN"


def should_recheck(seen: dict[str, Any], today: date) -> bool:
    if seen.get("status") == "CLOSED":
        return False
    last_checked = seen.get("last_checked")
    if not last_checked:
        return True
    try:
        age = (today - date.fromisoformat(last_checked[:10])).days
    except ValueError:
        return True
    end_value = seen.get("registration_end")
    try:
        end = date.fromisoformat(end_value) if end_value else None
    except ValueError:
        end = None
    if end and 0 <= (end - today).days <= config.RECHECK_CLOSING_WITHIN_DAYS:
        return age >= 1
    return age >= config.RECHECK_OPEN_AFTER_DAYS


def iso_now(now: datetime) -> str:
    return now.isoformat(timespec="seconds")
