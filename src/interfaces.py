"""Small extension points for future sources and analyzers."""

from abc import ABC, abstractmethod
from typing import Any, Iterable, Mapping


class VacancySource(ABC):
    @abstractmethod
    def discover(self) -> Iterable[dict[str, Any]]:
        """Return lightweight vacancy records from a source listing."""

    @abstractmethod
    def fetch(self, vacancy: Mapping[str, Any]) -> dict[str, Any]:
        """Fetch and parse one vacancy in detail."""


class VacancyAnalyzer(ABC):
    @abstractmethod
    def analyze(
        self, vacancy: Mapping[str, Any], profile: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Return classification fields for a vacancy."""
