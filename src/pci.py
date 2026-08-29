"""Conservative PCI Concursos source implementation."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import date
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config
from .interfaces import VacancySource
from .parser import clean_text, normalize_text, parse_brazilian_dates, parse_pci_detail


LOGGER = logging.getLogger(__name__)


def normalize_url(url: str) -> str:
    absolute = urljoin(config.PCI_LISTING_URL, url.strip())
    parts = urlsplit(absolute)
    path = re.sub(r"/+", "/", parts.path).rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def stable_id(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()[:20]


def content_hash(value: str) -> str:
    return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": config.USER_AGENT,
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
    })
    retry = Retry(
        total=config.REQUEST_RETRIES,
        connect=config.REQUEST_RETRIES,
        read=config.REQUEST_RETRIES,
        status=config.REQUEST_RETRIES,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(("GET",)),
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def parse_listing(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    records: dict[str, dict[str, Any]] = {}
    for container in soup.select("div.ca"):
        anchor = container.select_one('a[href*="/noticias/"]')
        if not anchor:
            continue
        url = normalize_url(anchor.get("href", ""))
        if not url or "/noticias/" not in url:
            continue
        card = container.parent
        institution = clean_text(anchor.get_text(" ", strip=True))
        title = clean_text(anchor.get("title") or institution)
        state_node = container.select_one(".cc")
        detail_node = container.select_one(".cd")
        deadline_node = container.select_one(".ce")
        state = clean_text(state_node.get_text(" ", strip=True)) if state_node else ""
        detail_lines = list(detail_node.stripped_strings) if detail_node else []
        position = detail_lines[1] if len(detail_lines) > 1 else "Professor"
        salary_text = detail_lines[0] if detail_lines else None
        deadline_text = clean_text(deadline_node.get_text(" ", strip=True)) if deadline_node else ""
        deadlines = parse_brazilian_dates(deadline_text)
        vacancy_match = re.search(r"(\d+)\s+vagas?", salary_text or "", re.I)
        listing_fingerprint = content_hash("|".join((title, institution, state, " ".join(detail_lines), deadline_text)))
        records[url] = {
            "id": stable_id(url),
            "source": config.SOURCE_NAME,
            "source_url": url,
            "title": title,
            "institution": institution,
            "state": state,
            "position": position,
            "salary_text": salary_text,
            "registration_end": deadlines[-1].isoformat() if deadlines else None,
            "vacancies_count": int(vacancy_match.group(1)) if vacancy_match else None,
            "listing_fingerprint": listing_fingerprint,
        }
    if not records:
        raise RuntimeError(
            "PCI parser found 0 vacancy links. This likely indicates a site layout change."
        )
    return list(records.values())


def is_potential_listing(vacancy: Mapping[str, Any]) -> bool:
    """Accept every valid card from PCI's dedicated professor listing.

    Course/profile filtering belongs to the published UI, not discovery. This
    intentionally includes basic-education and municipal teaching notices.
    """
    return bool(vacancy.get("source_url") and vacancy.get("title"))


class PCIConcursosSource(VacancySource):
    def __init__(self, session: requests.Session | None = None, delay: float | None = None):
        self.session = session or build_session()
        self.delay = config.REQUEST_DELAY_SECONDS if delay is None else delay
        self._last_request_at = 0.0

    def _get(self, url: str) -> str:
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at and elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        LOGGER.debug("GET %s", url)
        response = self.session.get(url, timeout=config.REQUEST_TIMEOUT_SECONDS)
        self._last_request_at = time.monotonic()
        response.raise_for_status()
        if not response.text.strip():
            raise RuntimeError(f"PCI returned an empty response for {url}")
        return response.text

    def discover(self) -> list[dict[str, Any]]:
        LOGGER.info("Consultando listagem de professores do PCI")
        return parse_listing(self._get(config.PCI_LISTING_URL))

    def fetch(self, vacancy: Mapping[str, Any]) -> dict[str, Any]:
        html = self._get(str(vacancy["source_url"]))
        parsed = parse_pci_detail(html, str(vacancy["source_url"]), dict(vacancy))
        parsed["content_hash"] = content_hash(parsed.get("raw_text", ""))
        return parsed
