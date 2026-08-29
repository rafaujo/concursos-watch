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
from src.classifier import RuleBasedAnalyzer, visual_category
from src.monitoring import compute_status, detect_changes, iso_now, should_recheck
from src.official import OfficialDocumentReader, should_check_official
from src.parser import extract_requirement_sentences, normalize_text
from src.pci import PCIConcursosSource, is_potential_listing
from src.report import generate_report
from src.storage import RepositoryState


LOGGER = logging.getLogger("concursos_watch")
TZ = ZoneInfo("America/Sao_Paulo")


def _restore_pci_requirements(vacancy: dict[str, Any]) -> None:
    """Keep parent cards free from requirements combined across official sub-vacancies."""
    pci_requirements = extract_requirement_sentences(str(vacancy.get("raw_text") or ""))
    mapping = {
        "graduation_requirement_raw": "graduation_requirement",
        "postgraduate_requirement_raw": "postgraduate_requirement",
        "masters_requirement_raw": "masters_requirement",
        "doctorate_requirement_raw": "doctorate_requirement",
    }
    for field, parsed_field in mapping.items():
        pci_field = f"pci_{field}"
        if vacancy.get("raw_text"):
            vacancy[pci_field] = pci_requirements.get(parsed_field)
        vacancy[field] = vacancy.get(pci_field)
        vacancy[field.removesuffix("_raw")] = vacancy[field]


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


def _apply_official_result(
    vacancy: dict[str, Any], result: dict[str, Any], analyzer: RuleBasedAnalyzer,
    now: datetime,
) -> bool:
    """Attach audit metadata and reclassify only from scoped official evidence."""
    previous_eligibility = vacancy.get("formal_eligibility")
    vacancy["official_check_status"] = result.get("status")
    vacancy["official_checked_at"] = result.get("checked_at")
    vacancy["official_check_reason"] = result.get("reason")
    vacancy["official_documents"] = result.get("documents", [])
    vacancy["official_document_url"] = result.get("document_url")
    vacancy["official_document_type"] = result.get("document_type")
    vacancy["official_content_hash"] = result.get("content_hash")
    vacancy["official_evidence_confidence"] = result.get("confidence")
    vacancy["official_requirement_evidence"] = result.get("evidence", [])
    vacancy["official_errors"] = result.get("errors", [])
    vacancy["official_pci_protected_documents"] = result.get("pci_protected_documents", [])

    if result.get("opportunities"):
        _restore_pci_requirements(vacancy)
        analyzed_opportunities = []
        for opportunity in result["opportunities"]:
            child = {
                **vacancy,
                **opportunity,
                "title": f"{vacancy.get('position') or 'Professor'} - {opportunity['area']}",
                "description": opportunity["area"],
                "raw_text": opportunity.get("requirement_text") or "",
                "official_evidence_text": opportunity.get("requirement_text") or "",
            }
            child.update(analyzer.analyze(child, config.PROFILE))
            analyzed_opportunities.append({
                **opportunity,
                "formal_eligibility": child["formal_eligibility"],
                "formal_reason": child["formal_reason"],
                "thematic_score": child["thematic_score"],
                "thematic_reason": child["thematic_reason"],
                "visual_category": child["visual_category"],
                "official_document_url": result.get("document_url"),
            })
        vacancy["official_opportunities"] = analyzed_opportunities
        relevant = [item for item in analyzed_opportunities if item["thematic_score"] > 0]
        if relevant:
            best_score = max(item["thematic_score"] for item in relevant)
            eligibility = next(
                status for status in ("YES", "UNCERTAIN", "UNKNOWN", "NO")
                if any(item["formal_eligibility"] == status for item in relevant)
            )
            counts = {
                status: sum(item["formal_eligibility"] == status for item in relevant)
                for status in ("YES", "UNCERTAIN", "UNKNOWN", "NO")
            }
            vacancy["formal_eligibility"] = eligibility
            vacancy["formal_reason"] = (
                f"Edital multiárea lido: {len(relevant)} sub-vaga(s) tematicamente relevante(s) "
                f"(YES {counts['YES']}, UNCERTAIN {counts['UNCERTAIN']}, "
                f"UNKNOWN {counts['UNKNOWN']}, NO {counts['NO']}). Consulte o bloco específico."
            )
            vacancy["thematic_score"] = best_score
            areas = ", ".join(item["area"] for item in sorted(
                relevant, key=lambda item: -item["thematic_score"]
            )[:4])
            vacancy["thematic_reason"] = f"Maior aderência entre as sub-vagas oficiais. Áreas: {areas}."
            vacancy["visual_category"] = visual_category(eligibility, best_score)
            vacancy["requirements_source"] = "OFFICIAL_PDF_MULTI"
            vacancy["official_evidence_text"] = " ".join(
                f"{item['area']} {item.get('requirement_text') or ''}" for item in relevant
            )
            changed = eligibility != previous_eligibility
            if changed:
                vacancy["updated_at"] = now.date().isoformat()
                change = (
                    "elegibilidade revista pelas sub-vagas do edital: "
                    f"{previous_eligibility} → {eligibility}"
                )
                vacancy["changes"] = list(dict.fromkeys([*(vacancy.get("changes") or []), change]))
                history = list(vacancy.get("change_history") or [])
                history.append({"date": iso_now(now), "changes": [change]})
                vacancy["change_history"] = history[-20:]
                vacancy["status"] = compute_status(vacancy, now.date(), is_updated=True)
            return changed

    if not result.get("applicable"):
        _restore_pci_requirements(vacancy)
        vacancy["requirements_source"] = "PCI_SUMMARY"
        vacancy.pop("official_evidence_text", None)
        vacancy.update(analyzer.analyze(vacancy, config.PROFILE))
        restored = vacancy.get("formal_eligibility") != previous_eligibility
        if restored:
            vacancy["updated_at"] = now.date().isoformat()
            change = f"elegibilidade restaurada após descartar documento não correspondente: {previous_eligibility} → {vacancy['formal_eligibility']}"
            vacancy["changes"] = list(dict.fromkeys([*(vacancy.get("changes") or []), change]))
            vacancy["status"] = compute_status(vacancy, now.date(), is_updated=True)
        return restored
    requirements = result.get("requirements") or {}
    _restore_pci_requirements(vacancy)
    for field, value in requirements.items():
        vacancy[field] = value
    vacancy["requirements_source"] = f"OFFICIAL_{result.get('document_type', 'DOCUMENT')}"
    vacancy["official_evidence_text"] = " ".join(
        str(item.get("text") or "") for item in result.get("evidence", [])
    )
    vacancy.update(analyzer.analyze(vacancy, config.PROFILE))
    changed = vacancy.get("formal_eligibility") != previous_eligibility
    if changed:
        vacancy["updated_at"] = now.date().isoformat()
        change = f"elegibilidade revista pelo edital: {previous_eligibility} → {vacancy['formal_eligibility']}"
        vacancy["changes"] = list(dict.fromkeys([*(vacancy.get("changes") or []), change]))
        history = list(vacancy.get("change_history") or [])
        history.append({"date": iso_now(now), "changes": [change]})
        vacancy["change_history"] = history[-20:]
        vacancy["status"] = compute_status(vacancy, now.date(), is_updated=True)
    return changed


def run(
    max_fetch: int | None = None,
    delay: float | None = None,
    max_official: int | None = None,
    skip_official: bool = False,
    force_official: bool = False,
    official_match: str | None = None,
) -> int:
    now = datetime.now(TZ)
    state = RepositoryState(config.DATA_DIR)
    vacancies, seen, run_history, official_cache = state.load()
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

    official_processed = official_read = official_reclassified = official_failures = 0
    if config.OFFICIAL_CHECK_ENABLED and not skip_official:
        reader = OfficialDocumentReader(source.session)
        official_candidates = [
            vacancy for vacancy in by_url.values()
            if vacancy.get("status") != "CLOSED"
            and vacancy.get("source_url")
            and (
                force_official
                or should_check_official(official_cache.get(vacancy["source_url"]), now.date())
            )
        ]
        official_candidates.sort(key=lambda vacancy: (
            -int(vacancy.get("thematic_score") or 0),
            int(vacancy.get("geographic_priority") or 4),
            vacancy.get("registration_end") or "9999-12-31",
        ))
        if official_match:
            needle = normalize_text(official_match)
            official_candidates = [
                vacancy for vacancy in official_candidates
                if needle in normalize_text(
                    f"{vacancy.get('institution', '')} {vacancy.get('title', '')}"
                )
            ]
        limit = config.OFFICIAL_MAX_VACANCIES_PER_RUN if max_official is None else max_official
        if limit is not None:
            official_candidates = official_candidates[:limit]
        print(f"\nOfficial documents queue: {len(official_candidates)}")
        for index, vacancy in enumerate(official_candidates, start=1):
            print(f"Reading official source {index}/{len(official_candidates)}: {vacancy['institution']}")
            try:
                result = reader.read(vacancy, now)
                official_cache[vacancy["source_url"]] = result
                official_processed += 1
                official_read += result.get("status") in ("READ", "READ_MULTI")
                official_failures += result.get("status") in ("ERROR", "BLOCKED")
                official_reclassified += _apply_official_result(vacancy, result, analyzer, now)
                print(
                    f"Official status: {result.get('status')} | "
                    f"Evidence: {result.get('confidence', 'NONE')} | "
                    f"Eligibility: {vacancy.get('formal_eligibility')}"
                )
            except Exception as exc:
                official_failures += 1
                LOGGER.warning("Falha inesperada na etapa oficial de %s: %s", vacancy["source_url"], exc, exc_info=True)

    vacancy_list = sorted(by_url.values(), key=lambda item: item.get("first_seen", ""), reverse=True)
    run_record = {
        "run_at": iso_now(now), "discovered": len(discovered), "known": known_at_start,
        "new": new_at_start, "changed_listings": changed_listing, "processed": processed,
        "failures": failures, "relevant_new": relevant_new,
        "official_processed": official_processed, "official_read": official_read,
        "official_reclassified": official_reclassified, "official_failures": official_failures,
    }
    run_history.append(run_record)
    state.save(vacancy_list, seen, run_history, official_cache)
    generate_report(vacancy_list, config.DOCS_DIR / "index.html", now)

    print(f"\nRelevant new vacancies: {relevant_new}")
    print(f"Uncertain: {uncertain}")
    print(f"Rejected: {rejected}")
    print(f"Individual failures: {failures}")
    print(f"Official sources processed: {official_processed}")
    print(f"Official documents read: {official_read}")
    print(f"Reclassified from official evidence: {official_reclassified}")
    print(f"Official read failures/blocks: {official_failures}")
    print("\nPage generated: docs/index.html")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Atualiza o radar Concursos Watch")
    parser.add_argument("--max-fetch", type=int, help="Limite de páginas individuais (diagnóstico local)")
    parser.add_argument("--delay", type=float, help="Intervalo entre requisições em segundos")
    parser.add_argument("--max-official", type=int, help="Limite de vagas na etapa de edital oficial")
    parser.add_argument("--skip-official", action="store_true", help="Não consultar fontes oficiais nesta execução")
    parser.add_argument("--force-official", action="store_true", help="Ignorar cache e revisar fontes oficiais")
    parser.add_argument("--official-match", help="Filtrar diagnóstico oficial por instituição ou título")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return run(
            max_fetch=args.max_fetch,
            delay=args.delay,
            max_official=args.max_official,
            skip_official=args.skip_official,
            force_official=args.force_official,
            official_match=args.official_match,
        )
    except Exception as exc:
        LOGGER.error("Execução abortada: %s", exc, exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    sys.exit(main())
