#!/usr/bin/env python3
"""Daily Concursos Watch orchestrator."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import config
from src.classifier import RuleBasedAnalyzer
from src.monitoring import compute_status, detect_changes, iso_now, should_recheck
from src.pci import PCIConcursosSource, is_potential_listing
from src.report import generate_report
from src.storage import RepositoryState


LOGGER = logging.getLogger("concursos_watch")
TZ = ZoneInfo("America/Sao_Paulo")


def _merge_vacancy(
    listing: dict[str, Any], detail: dict[str, Any], previous: dict[str, Any] | None,
    now: datetime, analyzer: RuleBasedAnalyzer,
) -> tuple[dict[str, Any], list[str]]:
    previous = previous or {}
    merged = {**previous, **listing, **detail}
    merged["first_seen"] = previous.get("first_seen") or now.date().isoformat()
    merged["last_seen"] = now.date().isoformat()
    merged["last_checked"] = iso_now(now)
    merged["changes"] = detect_changes(previous, merged) if previous else []
    is_updated = bool(previous and merged["changes"])
    if is_updated:
        merged["updated_at"] = now.date().isoformat()
        history = list(previous.get("change_history") or [])
        history.append({"date": iso_now(now), "changes": merged["changes"]})
        merged["change_history"] = history[-20:]
    merged.update(analyzer.analyze(merged, config.PROFILE))
    merged["status"] = compute_status(merged, now.date(), is_new=not bool(previous), is_updated=is_updated)
    return merged, merged["changes"]


def run(max_fetch: int | None = None, delay: float | None = None) -> int:
    now = datetime.now(TZ)
    state = RepositoryState(config.DATA_DIR)
    vacancies, seen, run_history = state.load()
    by_url = {vacancy["source_url"]: vacancy for vacancy in vacancies}
    source = PCIConcursosSource(delay=delay)
    analyzer = RuleBasedAnalyzer()

    print("=" * 60)
    print("CONCURSOS WATCH")
    print("=" * 60)
    print(f"Run time: {now.strftime('%Y-%m-%d %H:%M %Z')}")
    discovered = source.discover()
    known_at_start = sum(item["source_url"] in seen for item in discovered)
    new_at_start = len(discovered) - known_at_start
    changed_listing = 0
    queue: list[dict[str, Any]] = []

    for listing in discovered:
        url = listing["source_url"]
        record = seen.get(url)
        potential = is_potential_listing(listing)
        if record is None:
            record = {
                "id": listing["id"], "url": url, "first_seen": now.date().isoformat(),
                "processed": False, "potential": potential,
            }
            seen[url] = record
        record["last_seen"] = now.date().isoformat()
        record["title"] = listing["title"]
        record["registration_end"] = listing.get("registration_end")
        fingerprint_changed = bool(
            record.get("listing_fingerprint")
            and record.get("listing_fingerprint") != listing["listing_fingerprint"]
        )
        if fingerprint_changed:
            changed_listing += 1
        record["listing_fingerprint"] = listing["listing_fingerprint"]
        record["potential"] = potential
        needs_fetch = potential and (
            not record.get("processed") or fingerprint_changed or should_recheck(record, now.date())
        )
        if needs_fetch:
            queue.append(listing)

    print(f"PCI listings discovered : {len(discovered)}")
    print(f"Already known           : {known_at_start}")
    print(f"New listings            : {new_at_start}")
    print(f"Changed listings        : {changed_listing}")
    if max_fetch is not None:
        queue = queue[:max_fetch]
        print(f"Local fetch limit       : {max_fetch}")

    processed = failures = relevant_new = uncertain = rejected = 0
    for index, listing in enumerate(queue, start=1):
        url = listing["source_url"]
        print(f"\nProcessing vacancy {index}/{len(queue)}: {listing['institution']}")
        try:
            detail = source.fetch(listing)
            previous = by_url.get(url)
            vacancy, changes = _merge_vacancy(listing, detail, previous, now, analyzer)
            by_url[url] = vacancy
            seen[url].update({
                "processed": True, "last_checked": iso_now(now), "content_hash": vacancy.get("content_hash"),
                "status": vacancy["status"], "registration_end": vacancy.get("registration_end"),
            })
            processed += 1
            if not previous and vacancy["formal_eligibility"] != "NO" and vacancy["thematic_score"] > 0:
                relevant_new += 1
            uncertain += vacancy["formal_eligibility"] in ("UNCERTAIN", "UNKNOWN")
            rejected += vacancy["formal_eligibility"] == "NO"
            print(f"Area: {vacancy.get('area')}")
            print(f"Formal eligibility: {vacancy['formal_eligibility']}")
            print(f"Thematic score: {vacancy['thematic_score']}")
            if changes:
                print(f"Changes: {', '.join(changes)}")
        except Exception as exc:  # one unavailable advert must not abort the whole run
            failures += 1
            seen[url]["last_error"] = f"{type(exc).__name__}: {exc}"
            seen[url]["last_error_at"] = iso_now(now)
            LOGGER.warning("Falha ao processar %s: %s", url, exc, exc_info=True)

    # Statuses continue ageing even when details are intentionally not fetched.
    for vacancy in by_url.values():
        updated_today = vacancy.get("updated_at") == now.date().isoformat()
        is_new_today = vacancy.get("first_seen") == now.date().isoformat() and vacancy.get("status") == "NEW"
        vacancy["status"] = compute_status(vacancy, now.date(), is_new=is_new_today, is_updated=updated_today)
        if vacancy["source_url"] in seen:
            seen[vacancy["source_url"]]["status"] = vacancy["status"]

    vacancy_list = sorted(by_url.values(), key=lambda item: item.get("first_seen", ""), reverse=True)
    run_record = {
        "run_at": iso_now(now), "discovered": len(discovered), "known": known_at_start,
        "new": new_at_start, "changed_listings": changed_listing, "processed": processed,
        "failures": failures, "relevant_new": relevant_new,
    }
    run_history.append(run_record)
    state.save(vacancy_list, seen, run_history)
    generate_report(vacancy_list, config.DOCS_DIR / "index.html", now)

    print(f"\nRelevant new vacancies: {relevant_new}")
    print(f"Uncertain: {uncertain}")
    print(f"Rejected: {rejected}")
    print(f"Individual failures: {failures}")
    print("\nPage generated: docs/index.html")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Atualiza o radar Concursos Watch")
    parser.add_argument("--max-fetch", type=int, help="Limite de páginas individuais (diagnóstico local)")
    parser.add_argument("--delay", type=float, help="Intervalo entre requisições em segundos")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return run(max_fetch=args.max_fetch, delay=args.delay)
    except Exception as exc:
        LOGGER.error("Execução abortada: %s", exc, exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    sys.exit(main())
