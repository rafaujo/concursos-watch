"""Versioned JSON state with validated, atomic writes."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class JSONStoreError(RuntimeError):
    pass


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise JSONStoreError(f"Não foi possível ler JSON válido em {path}: {exc}") from exc


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


class RepositoryState:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.vacancies_path = data_dir / "vacancies.json"
        self.seen_path = data_dir / "seen.json"
        self.history_path = data_dir / "run_history.json"
        self.official_documents_path = data_dir / "official_documents.json"

    def load(self) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        vacancies = load_json(self.vacancies_path, [])
        seen = load_json(self.seen_path, {})
        history = load_json(self.history_path, [])
        official_documents = load_json(self.official_documents_path, {})
        if (
            not isinstance(vacancies, list)
            or not isinstance(seen, dict)
            or not isinstance(history, list)
            or not isinstance(official_documents, dict)
        ):
            raise JSONStoreError("Estrutura inesperada nos arquivos de estado")
        return vacancies, seen, history, official_documents

    def save(
        self,
        vacancies: list[dict[str, Any]],
        seen: dict[str, Any],
        history: list[dict[str, Any]],
        official_documents: dict[str, Any],
    ) -> None:
        atomic_write_json(self.vacancies_path, vacancies)
        atomic_write_json(self.seen_path, seen)
        atomic_write_json(self.history_path, history[-365:])
        atomic_write_json(self.official_documents_path, official_documents)
