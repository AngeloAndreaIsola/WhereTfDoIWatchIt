#!/usr/bin/env python3
"""Deterministic offline counterfactual report using the production source policy.

Fixtures are synthetic. This command imports source metadata and policy only;
it never connects to a database, queues refreshes, or contacts providers.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

# Standalone fixtures use a dummy backend: any accidental SQL fails rather
# than connecting to a configured database. A Django test runner keeps its
# already-initialized settings and isolated PostgreSQL test database.
import django  # noqa: E402
from django.apps import apps  # noqa: E402
from django.conf import settings  # noqa: E402


def _bootstrap_django() -> None:
    if not settings.configured:
        settings.configure(
            INSTALLED_APPS=["django.contrib.contenttypes", "tracker", "availability"],
            DATABASES={"default": {"ENGINE": "django.db.backends.dummy"}},
            SECRET_KEY="offline-fixture-report-only",
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            USE_TZ=True,
        )
    if not apps.ready:
        django.setup()


_bootstrap_django()

from availability.sources import source_registry  # noqa: E402
from tracker.services.source_policy import (  # noqa: E402
    SOURCE_ROLES,
    AvailabilityRole,
    counterfactual,
    decide_sources_for_title,
)

REGIONS = ("NL", "IT", "JP")
LANGS = {"NL": "nl", "IT": "it", "JP": "ja"}
SEED = 42


@dataclass
class FixtureTitle:
    movie_id: int
    tmdb_id: int
    title: str
    year: int | None
    state: str
    resolved_by: set[str]
    offers_by_region: dict[str, int]
    langs_by_region: dict[str, list[str]]
    report_entries_by_country: dict[str, dict[str, Any]]


@dataclass
class PerTitleDecision:
    movie_id: int
    title: str
    state: str
    resolved_by: list[str]
    gate_off_allowed: list[str]
    gate_off_skipped: dict[str, str]
    gate_on_allowed: list[str]
    gate_on_skipped: dict[str, str]
    counterfactual_state_on: str
    losing_answer: bool
    reason_off: str
    reason_on: str
    synthetic: bool = True
    tmdb_id: int = 0
    decisions_by_country: dict[str, dict[str, Any]] | None = None
    country: str = "NL"


@dataclass
class BatchReport:
    batch_number: int
    total_titles: int
    gate_off_summary: dict[str, dict[str, int]]
    gate_on_summary: dict[str, dict[str, int]]
    titles_still_resolved: int
    titles_losing_answer: int
    hypothetical_eligibility: dict[str, Any]
    actual_requests: str
    actual_quota_spend: str
    actual_savings: str
    sql_metrics: dict[str, Any]
    nl_it_jp_breakdown: dict[str, Any]
    policy_observations: list[str]
    per_title: list[PerTitleDecision]
    synthetic: bool = True


class FixtureGenerator:
    def generate(self, count: int) -> list[FixtureTitle]:
        rng = random.Random(SEED)
        source_slugs = sorted(SOURCE_ROLES)
        metered = {s for s, role in SOURCE_ROLES.items() if role is AvailabilityRole.METERED_AGGREGATOR}
        free = [s for s in source_slugs if s not in metered]
        result = []
        for i in range(count):
            tmdb, name, year = 900000001 + i, f"Synthetic Title {i + 1:03d}", None
            roll = rng.random()
            if roll < .45:
                state = "available"
                resolved = set(rng.sample(free, min(2, len(free))))
                if rng.random() < .7:
                    resolved.update(metered)
            elif roll < .60:
                state = "available"
                resolved = set(metered)
            elif roll < .85:
                state = "unavailable"
                # Simulate providers answering definitively with no offer.
                resolved = set()
            else:
                state, resolved = "unknown", set()
            langs = {reg: [LANGS[reg], "en"] if rng.random() < .5 else [LANGS[reg]] for reg in REGIONS}
            country_entries = {}
            for reg in REGIONS:
                country_state = state
                country_sources = set(resolved)
                if reg != "NL" and rng.random() < .12:
                    country_state, country_sources = "unknown", set()
                country_entries[reg] = {
                    "state": country_state,
                    "sources": [{"source": slug, "available": True} for slug in sorted(country_sources)],
                }
            offers = {
                reg: len(entry["sources"]) if entry["state"] == "available" else 0
                for reg, entry in country_entries.items()
            }
            result.append(FixtureTitle(i + 1, tmdb, name, year, state, resolved, offers, langs, country_entries))
        return result


def applicable_sources() -> list[str]:
    """Only sources capable of per-title fetches are applicable here."""
    applicable = []
    for slug in source_registry.slugs():
        source = source_registry.get(slug)
        if getattr(source.capabilities, "supports_title_fetch", False):
            applicable.append(slug)
    return applicable


def decide_gate(title: FixtureTitle, *, ladder_on: bool, applicable_slugs: list[str] | None = None) -> dict[str, Any]:
    entry = title.report_entries_by_country["NL"]
    return decide_sources_for_title(
        entry,
        applicable_slugs=applicable_sources() if applicable_slugs is None else applicable_slugs,
        ladder_switch=ladder_on,
    )


def run_batch(count: int, *, batch_number: int = 1) -> BatchReport:
    titles = FixtureGenerator().generate(count)
    applicable = applicable_sources()
    per_title = []
    off_summary = {region: Counter() for region in REGIONS}
    on_summary = {region: Counter() for region in REGIONS}
    breakdown = {r: Counter() for r in REGIONS}
    states, evidence = {}, {}
    metered = {slug for slug, role in SOURCE_ROLES.items() if role is AvailabilityRole.METERED_AGGREGATOR}
    for title in titles:
        by_country = {}
        for region in REGIONS:
            entry = title.report_entries_by_country[region]
            off = decide_sources_for_title(entry, applicable_slugs=applicable, ladder_switch=False)
            on = decide_sources_for_title(entry, applicable_slugs=applicable, ladder_switch=True)
            by_country[region] = {
                "state": entry["state"],
                "resolved_by": off["resolved_by"],
                "gate_off_allowed": off["allowed_sources"], "gate_off_skipped": off["skipped_sources"],
                "gate_on_allowed": on["allowed_sources"], "gate_on_skipped": on["skipped_sources"],
            }
            if region == "NL":
                states[title.movie_id] = entry["state"]
                evidence[title.movie_id] = set(off["resolved_by"])
            for slug in off["allowed_sources"]:
                off_summary[region][slug] += 1
            for slug, reason in off["skipped_sources"].items():
                off_summary[region][f"{slug} ({reason})"] += 1
            for slug in on["allowed_sources"]:
                on_summary[region][slug] += 1
            for slug, reason in on["skipped_sources"].items():
                on_summary[region][f"{slug} ({reason})"] += 1
            breakdown[region]["metered_eligible_gate_off"] += int(any(s in metered for s in off["allowed_sources"]))
            breakdown[region]["metered_eligible_gate_on"] += int(any(s in metered for s in on["allowed_sources"]))
            if entry["state"] == "available" and title.offers_by_region[region]:
                breakdown[region]["available"] += 1
            breakdown[region]["titles"] += 1
            breakdown[region]["offers"] += title.offers_by_region[region]
            if LANGS[region] in title.langs_by_region[region]:
                breakdown[region]["with_lang_match"] += 1
        nl_decision = by_country["NL"]
        title_projection = counterfactual(
            {title.movie_id: title.state},
            {title.movie_id: evidence[title.movie_id]},
        )
        losing = bool(title_projection["titles_losing_their_answer"])
        resulting_state = "unknown" if losing else title.state
        off_reason = "Eligible by production policy; synthetic fixture only."
        on_reason = "See per-country production policy decisions; fixture evidence is synthetic."
        per_title.append(PerTitleDecision(title.movie_id, title.title, title.state,
                          sorted(evidence[title.movie_id]), nl_decision["gate_off_allowed"], nl_decision["gate_off_skipped"],
                          nl_decision["gate_on_allowed"], nl_decision["gate_on_skipped"], resulting_state, losing,
                          off_reason, on_reason, True, title.tmdb_id, by_country, "NL"))
    projection = counterfactual(states, evidence)
    return BatchReport(batch_number, count,
        {region: dict(values) for region, values in off_summary.items()},
        {region: dict(values) for region, values in on_summary.items()},
        projection["titles_still_resolved"], projection["titles_losing_their_answer"],
        {"by_country": {r: {"metered_eligible_gate_off": breakdown[r]["metered_eligible_gate_off"],
                            "metered_eligible_gate_on": breakdown[r]["metered_eligible_gate_on"]} for r in REGIONS},
         "basis": "synthetic_policy_eligibility_only",
         "unit": "eligible_titles_per_country",
         "answer_projection_country": "NL",
         "answer_projection_basis": "hypothetical_removal_of_metered_evidence"},
        "not_measured", "not_measured", "not_measured",
        {"total_queries": "not_measured", "total_time_ms": "not_measured", "avg_query_ms": "not_measured", "execution_time_ms": "not_measured"},
        {r: dict(c) for r, c in breakdown.items()},
        [
            "Gate ON skips fresh metered-only positive answers under current production policy. "
            "This fixture does not simulate observation expiry or infer a defect from unavailable classifications. "
            "Answer-loss counts project removal of metered evidence, not actual saved calls or the immediate gate decision."
        ],
        per_title)


def as_json(report: BatchReport, minimal: bool = False) -> str:
    data = asdict(report)
    if minimal:
        data.pop("per_title")
    return json.dumps(data, indent=2, sort_keys=True)


def as_markdown(report: BatchReport, minimal: bool = False) -> str:
    lines = [
        "# Offline Counterfactual Report", "", f"Synthetic titles: {report.total_titles}",
        f"NL available answers retained if metered evidence were removed: {report.titles_still_resolved}",
        f"NL available answers lost if metered evidence were removed: {report.titles_losing_answer}", "",
        "## Measurement boundaries", "",
        "- Eligibility is a synthetic policy projection for each title and country.",
        "- Actual requests, quota spend, savings, SQL metrics, and execution time: not_measured.", "",
        "## NL / IT / JP", "",
        "| Region | Titles | Available | Offers | Lang match | Metered eligible OFF | Metered eligible ON |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for region, values in report.nl_it_jp_breakdown.items():
        lines.append(
            f"| {region} | {values.get('titles', 0)} | {values.get('available', 0)} | "
            f"{values.get('offers', 0)} | {values.get('with_lang_match', 0)} | "
            f"{values.get('metered_eligible_gate_off', 0)} | {values.get('metered_eligible_gate_on', 0)} |"
        )
    if report.policy_observations:
        lines.extend(["", "## Policy observations", ""])
        lines.extend(f"- {item}" for item in report.policy_observations)
    if not minimal:
        lines.extend(["", "## Per-title decisions", ""])
        for decision in report.per_title:
            lines.append(
                f"- **{decision.title}** (fixture ID {decision.movie_id}; synthetic TMDb ID "
                f"{decision.tmdb_id}; country {decision.country}): {decision.reason_on}"
            )
            for country, country_decision in (decision.decisions_by_country or {}).items():
                lines.append(
                    f"  - {country}: {country_decision['state']}; OFF skip="
                    f"{country_decision['gate_off_skipped'] or '{}'}; ON skip="
                    f"{country_decision['gate_on_skipped'] or '{}'}"
                )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100, choices=range(1, 201))
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", default="b2-4-counterfactual-report.json")
    parser.add_argument("--minimal", action="store_true")
    args = parser.parse_args()
    report = run_batch(args.count)
    rendered = as_json(report, args.minimal) if args.format == "json" else as_markdown(report, args.minimal)
    print(rendered, end="")
    Path(args.output).write_text(rendered)


if __name__ == "__main__":
    main()
