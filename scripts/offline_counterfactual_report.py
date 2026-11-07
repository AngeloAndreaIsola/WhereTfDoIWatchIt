#!/usr/bin/env python3
"""
B2.4 Offline Counterfactual Fixtures and Reporting Infrastructure

A deterministic, network-free Python script that computes gate OFF vs gate ON
metered eligibility per title, with:
  - per-title evidence explaining allow/skip
  - NL/IT/JP language breakdown
  - quota implications (Streaming Availability free tier: 100 req/day)
  - SQL query counts and timing for 100/200 title batches

COMPLETELY offline: no Django, no PostgreSQL, no network calls.
Runs anywhere with Python 3.10+.

Gate Logic (from source_policy.py):
  OFF  → every applicable title-level source may run (metered included)
  ON   → metered aggregator (streaming_availability) runs only for titles
         that no cheaper source has resolved

Counterfactual Projection:
  For each "available" title, determine if it would remain available with gate ON.
  Titles resolved ONLY by metered sources become "unknown" (not "unavailable")
  if the metered source stops answering. An unanswered question is not evidence
  of absence.

Usage:
    python scripts/offline_counterfactual_report.py --count 100
    python scripts/offline_counterfactual_report.py --count 200 --format markdown
    python scripts/offline_counterfactual_report.py --region it --language it
"""

from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

# ── Constants ────────────────────────────────────────────────────────────────

# Source roles (mirrors source_policy.SOURCE_ROLES)
METERED_SLUGS: frozenset[str] = frozenset({"streaming_availability"})
BROAD_AGG_SLUGS: frozenset[str] = frozenset({"justwatch", "watchmode"})
COLLECTOR_SLUGS: frozenset[str] = frozenset({
    "mediaset_infinity", "youtube", "prime_video", "bbc_iplayer",
    "cbc_gem", "tubi", "pluto_tv", "rai_play", "pbs", "abc_iview",
})
ALL_FREE_SLUGS: frozenset[str] = BROAD_AGG_SLUGS | COLLECTOR_SLUGS
ALL_SLUGS: frozenset[str] = ALL_FREE_SLUGS | METERED_SLUGS

SA_DAILY_QUOTA = 100  # Streaming Availability free tier

# Deterministic seed – same fixtures every run
SEED = 42

# Regions and languages for breakdown
REGIONS = ("NL", "IT", "JP")
LANGS = ("nl", "it", "ja")  # ISO 639-1 for Dutch, Italian, Japanese


# ── Data models ──────────────────────────────────────────────────────────────

@dataclass
class FixtureTitle:
    """One title in the deterministic fixture set."""
    movie_id: int
    tmdb_id: int
    title: str
    year: int | None
    state: str          # "available" | "unavailable" | "unknown"
    resolved_by: set[str]
    offers_by_region: dict[str, int]   # {"NL": 2, "IT": 1, "JP": 0}
    langs_by_region: dict[str, list[str]]  # {"NL": ["nl","en"], "IT": ["it","en"]}


@dataclass
class PerTitleDecision:
    """Gate OFF vs gate ON for one title."""
    movie_id: int
    title: str
    state: str
    gate_off_allowed: list[str]
    gate_off_skipped: dict[str, str]
    gate_on_allowed: list[str]
    gate_on_skipped: dict[str, str]
    still_available_on: bool
    losing_answer: bool          # True → title becomes "unknown" under gate ON
    reason_off: str
    reason_on: str
    note: str


@dataclass
class BatchReport:
    """Aggregate report for a batch of titles."""
    batch_number: int
    total_titles: int
    gate_off_summary: dict[str, int]
    gate_on_summary: dict[str, int]
    titles_still_resolved: int
    titles_losing_answer: int
    quota: dict[str, Any]
    sql_metrics: dict[str, Any]
    nl_it_jp_breakdown: dict[str, Any]
    per_title: list[PerTitleDecision]


# ── Deterministic fixture generator ──────────────────────────────────────────

class FixtureGenerator:
    """Build deterministic fixtures that mirror real data shapes."""

    def __init__(self, *, region: str | None = None, language: str | None = None, seed: int = SEED):
        self.rng = random.Random(seed)
        self.region = region.upper() if region else None
        self.language = language.lower() if language else None

    # 30 well-known titles across NL/IT/JP
    _TITLES = [
        (550, "Fight Club", 1999),
        (13,  "Forrest Gump", 1994),
        (680, "Pulp Fiction", 1994),
        (155, "The Dark Knight", 2008),
        (278, "The Shawshank Redemption", 1994),
        (238, "The Godfather", 1972),
        (603, "The Matrix", 1999),
        (27205, "Inception", 2010),
        (157336, "Interstellar", 2014),
        (496243, "Parasite", 2019),
        (129,  "Spirited Away", 2001),
        (438631, "Dune", 2021),
        (324857, "Blade Runner 2049", 2017),
        (872585, "Oppenheimer", 2023),
        (598,  "Rear Window", 1954),
        (76341, "Mad Max: Fury Road", 2015),
        (11216, "Cinema Paradiso", 1988),
        (124,  "La Dolce Vita", 1960),
        (614,  "Seven Samurai", 1954),
        (10227, "Life is Beautiful", 1997),
        (447365, "Past Lives", 2023),
        (5971, "Spirited Away", 2001),
        (372058, "Your Name", 2016),
        (533514, "Weathering with You", 2019),
        (460465, "Tenet", 2020),
        (545611, "Licorice Pizza", 2021),
        (705644, "Close", 2022),
        (823924, "Aftersun", 2022),
        (934641, "The Zone of Interest", 2023),
        (507356, "The Holdovers", 2023),
    ]

    def generate(self, count: int) -> list[FixtureTitle]:
        """Generate *count* deterministic titles."""
        titles: list[FixtureTitle] = []
        rng = self.rng

        for i in range(count):
            tmdb_id, name, year = self._TITLES[i % len(self._TITLES)]
            # Avoid duplicate tmdb_ids in the fixture
            tmdb_id = tmdb_id + (i // len(self._TITLES)) * 100000

            # ── Deterministic state distribution ──
            # 45 % available (free + metered), 15 % available (metered-only),
            # 25 % unavailable, 15 % unknown
            r = rng.random()
            if r < 0.45:
                state = "available"
                metered_only = False
            elif r < 0.60:
                state = "available"
                metered_only = True
            elif r < 0.85:
                state = "unavailable"
                metered_only = False
            else:
                state = "unknown"
                metered_only = False

            # ── Resolved-by ──
            resolved_by: set[str] = set()
            if state in ("available", "unavailable"):
                if metered_only:
                    resolved_by.add("streaming_availability")
                else:
                    # Free sources that resolved
                    n_free = rng.randint(1, 3)
                    resolved_by.update(rng.sample(sorted(ALL_FREE_SLUGS), k=n_free))
                    # Metered also resolved ~70 % of the time for available titles
                    if state == "available" and rng.random() < 0.7:
                        resolved_by.add("streaming_availability")

            # ── Region / language breakdown ──
            offers_by_region: dict[str, int] = {}
            langs_by_region: dict[str, list[str]] = {}
            for reg in REGIONS:
                n_offers = rng.randint(0, 3) if state == "available" else 0
                offers_by_region[reg] = n_offers
                # Languages present in that region
                if reg == "NL":
                    langs_by_region[reg] = rng.sample(["nl", "en"], k=rng.randint(1, 2))
                elif reg == "IT":
                    langs_by_region[reg] = rng.sample(["it", "en"], k=rng.randint(1, 2))
                elif reg == "JP":
                    langs_by_region[reg] = rng.sample(["ja", "en"], k=rng.randint(1, 2))

            # Apply region filter
            if self.region:
                offers_by_region = {k: v for k, v in offers_by_region.items() if k == self.region}
                langs_by_region = {k: v for k, v in langs_by_region.items() if k == self.region}

            # Apply language filter
            if self.language:
                langs_by_region = {
                    k: [l for l in v if l == self.language]
                    for k, v in langs_by_region.items()
                }

            titles.append(FixtureTitle(
                movie_id=i + 1,
                tmdb_id=tmdb_id,
                title=name,
                year=year,
                state=state,
                resolved_by=resolved_by,
                offers_by_region=offers_by_region,
                langs_by_region=langs_by_region,
            ))

        return titles


# ── Gate decision logic (mirrors source_policy.decide_sources_for_title) ─────

def decide_gate(title: FixtureTitle, *, ladder_on: bool) -> dict[str, Any]:
    """
    Return {"allowed_sources": [...], "skipped_sources": {slug: reason}}.

    Mirrors source_policy.decide_sources_for_title exactly.
    """
    metered = [s for s in METERED_SLUGS]
    free = [s for s in ALL_FREE_SLUGS]

    skipped: dict[str, str] = {}

    if not ladder_on:
        # Gate OFF → metered always allowed
        allowed_metered = metered
    elif title.state == "unknown":
        # Gate ON, title unknown → metered is the last hope
        allowed_metered = metered
    else:
        # Gate ON, title resolved (available or unavailable) → skip metered
        allowed_metered = []
        for s in metered:
            skipped[s] = "not_needed"

    return {
        "allowed_sources": free + allowed_metered,
        "skipped_sources": skipped,
    }


def counterfactual_for_title(title: FixtureTitle) -> dict[str, Any]:
    """
    What switching gate ON would cost for this single title.

    Mirrors source_policy.counterfactual per-title logic.
    """
    metered = METERED_SLUGS
    produced = title.resolved_by

    if title.state != "available":
        # Already unavailable/unknown → gate cannot make it worse
        return {"still_resolved": True, "losing": False}
    if produced & metered and not (produced - metered):
        # Only metered resolved this → loses answer
        return {"still_resolved": False, "losing": True}
    # Free sources already cover it
    return {"still_resolved": True, "losing": False}


# ── Batch runner ─────────────────────────────────────────────────────────────

def run_batch(
    count: int,
    *,
    region: str | None = None,
    language: str | None = None,
    batch_number: int = 1,
) -> BatchReport:
    """Run counterfactual analysis on *count* deterministic titles."""
    t0 = time.perf_counter()

    gen = FixtureGenerator(region=region, language=language)
    titles = gen.generate(count)

    per_title: list[PerTitleDecision] = []
    gate_off_summary = Counter()
    gate_on_summary = Counter()
    nl_it_jp = {"NL": Counter(), "IT": Counter(), "JP": Counter()}

    for t in titles:
        off = decide_gate(t, ladder_on=False)
        on  = decide_gate(t, ladder_on=True)
        cf  = counterfactual_for_title(t)

        # Accumulate source counts
        for s in off["allowed_sources"]:
            gate_off_summary[s] += 1
        for s, r in off["skipped_sources"].items():
            gate_off_summary[f"{s} ({r})"] += 1
        for s in on["allowed_sources"]:
            gate_on_summary[s] += 1
        for s, r in on["skipped_sources"].items():
            gate_on_summary[f"{s} ({r})"] += 1

        # NL/IT/JP breakdown
        for reg in REGIONS:
            nl_it_jp[reg]["offers"] += t.offers_by_region.get(reg, 0)
            nl_it_jp[reg]["titles"] += 1
            if t.state == "available" and t.offers_by_region.get(reg, 0) > 0:
                nl_it_jp[reg]["available"] += 1
            langs = t.langs_by_region.get(reg, [])
            if langs:
                nl_it_jp[reg]["with_lang_match"] += 1

        reason_off = "All applicable sources allowed (gate OFF)"
        if not cf["losing"]:
            reason_on = "Title resolved by free source(s); metered skipped (not_needed)"
        elif t.state == "unknown":
            reason_on = "Title unknown; metered allowed as last resort"
        else:
            reason_on = "Title available only via metered source; loses answer with gate ON"

        per_title.append(PerTitleDecision(
            movie_id=t.movie_id,
            title=t.title,
            state=t.state,
            gate_off_allowed=off["allowed_sources"],
            gate_off_skipped=off["skipped_sources"],
            gate_on_allowed=on["allowed_sources"],
            gate_on_skipped=on["skipped_sources"],
            still_available_on=cf["still_resolved"],
            losing_answer=cf["losing"],
            reason_off=reason_off,
            reason_on=reason_on,
            note=(
                "Counterfactual only — no source was consulted and no quota was spent. "
                "A title whose only available offer came from a metered source would "
                "fall back to 'unknown', not to 'unavailable': an unanswered question "
                "is not evidence of absence."
            ),
        ))

    still_resolved = sum(1 for d in per_title if d.still_available_on)
    losing = sum(1 for d in per_title if d.losing_answer)

    # Quota implications
    metered_off = sum(1 for d in per_title if any(s in METERED_SLUGS for s in d.gate_off_allowed))
    metered_on  = sum(1 for d in per_title if any(s in METERED_SLUGS for s in d.gate_on_allowed))
    savings = metered_off - metered_on
    quota = {
        "metered_calls_gate_off": metered_off,
        "metered_calls_gate_on": metered_on,
        "savings": savings,
        "savings_pct": round(savings / max(metered_off, 1) * 100, 1),
        "daily_quota": SA_DAILY_QUOTA,
        "remaining_with_savings": SA_DAILY_QUOTA - metered_on,
    }

    # SQL metrics: not measured (offline, no DB connection).
    sql = {
        "total_queries": "not_measured",
        "total_time_ms": "not_measured",
        "avg_query_ms": "not_measured",
        "queries_per_title": "not_measured",
        "note": "SQL metrics not_measured: offline report does not connect to a database. Run against an isolated DB to obtain real measurements.",
    }

    elapsed = time.perf_counter() - t0

    # Convert Counter → dict for JSON
    nl_it_jp_dict = {
        reg: {k: v for k, v in ctr.items()}
        for reg, ctr in nl_it_jp.items()
    }

    return BatchReport(
        batch_number=batch_number,
        total_titles=count,
        gate_off_summary=dict(gate_off_summary),
        gate_on_summary=dict(gate_on_summary),
        titles_still_resolved=still_resolved,
        titles_losing_answer=losing,
        quota=quota,
        sql_metrics={**sql, "wall_seconds": round(elapsed, 3)},
        nl_it_jp_breakdown=nl_it_jp_dict,
        per_title=per_title,
    )


# ── Output formatters ────────────────────────────────────────────────────────

def _json(report: BatchReport, *, minimal: bool = False) -> str:
    data = {
        "batch": {"number": report.batch_number, "total_titles": report.total_titles},
        "gate_off_summary": report.gate_off_summary,
        "gate_on_summary": report.gate_on_summary,
        "titles_still_resolved": report.titles_still_resolved,
        "titles_losing_answer": report.titles_losing_answer,
        "quota": report.quota,
        "sql_metrics": report.sql_metrics,
        "nl_it_jp_breakdown": report.nl_it_jp_breakdown,
    }
    if not minimal:
        data["per_title"] = [
            {
                "movie_id": d.movie_id,
                "title": d.title,
                "state": d.state,
                "gate_off_allowed": d.gate_off_allowed,
                "gate_on_allowed": d.gate_on_allowed,
                "still_available_on": d.still_available_on,
                "losing_answer": d.losing_answer,
                "reason_on": d.reason_on,
            }
            for d in report.per_title
        ]
    return json.dumps(data, indent=2)


def _markdown(report: BatchReport, *, minimal: bool = False) -> str:
    lines: list[str] = []
    w = lines.append

    w("# B2.4 Offline Counterfactual Report\n")
    w(f"**Batch** {report.batch_number} | **Titles** {report.total_titles}\n")
    w(f"- Still resolved (gate ON): {report.titles_still_resolved}\n")
    w(f"- Losing answer (gate ON):  {report.titles_losing_answer}\n\n")

    # Quota
    q = report.quota
    w("## Quota Implications\n")
    w(f"| | Gate OFF | Gate ON |\n")
    w(f"|---|---|---|\n")
    w(f"| Metered calls | {q['metered_calls_gate_off']} | {q['metered_calls_gate_on']} |\n")
    w(f"| Savings | — | **{q['savings']}** ({q['savings_pct']}%) |\n")
    w(f"| Daily quota remaining | {q['daily_quota'] - q['metered_calls_gate_off']} | {q['remaining_with_savings']} |\n\n")

    # SQL
    s = report.sql_metrics
    w("## SQL Performance\n")
    w(f"- Queries: {s['total_queries']} ({s['queries_per_title']}/title)\n")
    w(f"- Time: {s['total_time_ms']}ms (avg {s['avg_query_ms']}ms/query)\n")
    w(f"- Wall: {s['wall_seconds']}s\n\n")

    # NL/IT/JP
    w("## NL / IT / JP Breakdown\n")
    w("| Region | Titles | Available | Offers | Lang match |\n")
    w("|--------|--------|-----------|--------|------------|\n")
    for reg in REGIONS:
        b = report.nl_it_jp_breakdown.get(reg, {})
        w(f"| {reg} | {b.get('titles',0)} | {b.get('available',0)} | {b.get('offers',0)} | {b.get('with_lang_match',0)} |\n")
    w("\n")

    # Gate summary
    w("## Source Summary\n")
    w("### Gate OFF\n")
    for src, cnt in sorted(report.gate_off_summary.items()):
        w(f"- {src}: {cnt}\n")
    w("\n### Gate ON\n")
    for src, cnt in sorted(report.gate_on_summary.items()):
        w(f"- {src}: {cnt}\n")
    w("\n")

    if not minimal:
        w("## Per-Title Decisions\n")
        for i, d in enumerate(report.per_title, 1):
            w(f"### {i}. {d.title} (ID {d.movie_id})\n")
            w(f"- State: **{d.state}**\n")
            w(f"- Gate OFF allowed: {', '.join(d.gate_off_allowed) or '∅'}\n")
            w(f"- Gate ON allowed:  {', '.join(d.gate_on_allowed) or '∅'}\n")
            if d.losing_answer:
                w(f"- ⚠️ **Loses answer with gate ON**\n")
            w(f"- Reason: {d.reason_on}\n\n")

    return "\n".join(lines)


def _pretty(report: BatchReport, *, minimal: bool = False) -> str:
    """Human-readable terminal output."""
    lines: list[str] = []
    p = lines.append

    p("=" * 78)
    p("B2.4 OFFLINE COUNTERFACTUAL REPORT")
    p("=" * 78)
    p(f"Batch {report.batch_number} | {report.total_titles} titles")
    p(f"  Still resolved (gate ON): {report.titles_still_resolved}")
    p(f"  Losing answer  (gate ON): {report.titles_losing_answer}")
    p("")

    q = report.quota
    p("QUOTA (SA free tier: 100 req/day)")
    p("-" * 40)
    p(f"  Gate OFF metered calls: {q['metered_calls_gate_off']}")
    p(f"  Gate ON  metered calls: {q['metered_calls_gate_on']}")
    p(f"  Savings: {q['savings']} ({q['savings_pct']}%)")
    p(f"  Remaining: {q['remaining_with_savings']} req/day")
    p("")

    s = report.sql_metrics
    p("SQL (simulated)")
    p("-" * 40)
    p(f"  Queries: {s['total_queries']} ({s['queries_per_title']}/title)")
    p(f"  Time:    {s['total_time_ms']}ms (avg {s['avg_query_ms']}ms)")
    p(f"  Wall:    {s['wall_seconds']}s")
    p("")

    p("NL / IT / JP BREAKDOWN")
    p("-" * 40)
    for reg in REGIONS:
        b = report.nl_it_jp_breakdown.get(reg, {})
        p(f"  {reg}: {b.get('available',0)} available / {b.get('offers',0)} offers / {b.get('with_lang_match',0)} lang-matched")
    p("")

    p("GATE OFF sources")
    p("-" * 40)
    for src, cnt in sorted(report.gate_off_summary.items()):
        p(f"  {src}: {cnt}")
    p("")
    p("GATE ON sources")
    p("-" * 40)
    for src, cnt in sorted(report.gate_on_summary.items()):
        p(f"  {src}: {cnt}")

    if not minimal:
        p("")
        p("PER-TITLE (first 20)")
        p("-" * 40)
        for d in report.per_title[:20]:
            flag = " ⚠ LOSES" if d.losing_answer else ""
            p(f"  [{d.state:>11}] {d.title}{flag}")
            p(f"    OFF: {', '.join(d.gate_off_allowed[:5])}…")
            p(f"    ON:  {', '.join(d.gate_on_allowed[:5])}…")
            p(f"    {d.reason_on}")
        if len(report.per_title) > 20:
            p(f"  … {len(report.per_title) - 20} more")

    p("")
    p("=" * 78)
    p("DONE")
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=100, help="Titles to analyse (1–200)")
    ap.add_argument("--batch-size", type=int, default=100, help="Sub-batch size (for 200-title runs)")
    ap.add_argument("--region", choices=["nl", "it", "jp"], help="Region filter")
    ap.add_argument("--language", choices=["nl", "it", "ja"], help="Language filter")
    ap.add_argument("--format", choices=["json", "markdown", "pretty"], default="pretty", help="Output format")
    ap.add_argument("--output", default="b2-4-counterfactual-report.json", help="Output file")
    ap.add_argument("--minimal", action="store_true", help="Summary only, no per-title rows")
    args = ap.parse_args()

    count = min(args.count, 200)

    # If count > batch_size, run in sub-batches and merge
    if count > args.batch_size:
        reports: list[BatchReport] = []
        for start in range(0, count, args.batch_size):
            n = min(args.batch_size, count - start)
            reports.append(run_batch(n, region=args.region, language=args.language, batch_number=len(reports) + 1))
        # Merge
        merged_off = Counter()
        merged_on = Counter()
        merged_nl_it_jp: dict[str, Counter] = {r: Counter() for r in REGIONS}
        total_still = total_losing = total_off = total_on = 0
        all_per: list[PerTitleDecision] = []
        for r in reports:
            merged_off.update(r.gate_off_summary)
            merged_on.update(r.gate_on_summary)
            for reg in REGIONS:
                merged_nl_it_jp[reg].update(r.nl_it_jp_breakdown.get(reg, {}))
            total_still += r.titles_still_resolved
            total_losing += r.titles_losing_answer
            total_off += r.quota["metered_calls_gate_off"]
            total_on += r.quota["metered_calls_gate_on"]
            all_per.extend(r.per_title)
        savings = total_off - total_on
        report = BatchReport(
            batch_number=1,
            total_titles=count,
            gate_off_summary=dict(merged_off),
            gate_on_summary=dict(merged_on),
            titles_still_resolved=total_still,
            titles_losing_answer=total_losing,
            quota={
                "metered_calls_gate_off": total_off,
                "metered_calls_gate_on": total_on,
                "savings": savings,
                "savings_pct": round(savings / max(total_off, 1) * 100, 1),
                "daily_quota": SA_DAILY_QUOTA,
                "remaining_with_savings": SA_DAILY_QUOTA - total_on,
            },
            sql_metrics=reports[0].sql_metrics,  # representative
            nl_it_jp_breakdown={reg: dict(c) for reg, c in merged_nl_it_jp.items()},
            per_title=all_per,
        )
    else:
        report = run_batch(count, region=args.region, language=args.language)

    # Format output
    if args.format == "json":
        text = _json(report, minimal=args.minimal)
    elif args.format == "markdown":
        text = _markdown(report, minimal=args.minimal)
    else:
        text = _pretty(report, minimal=args.minimal)

    # Print to terminal
    print(text)

    # Write file
    with open(args.output, "w") as f:
        f.write(text)
    print(f"\nOutput → {args.output}")


if __name__ == "__main__":
    main()
