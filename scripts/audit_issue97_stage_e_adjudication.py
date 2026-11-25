"""Offline Stage E adjudication smoke check for issue #97.

Pure Python: no Django, no database, no transport, no live provider calls and
no raw evidence. Reads only the sanitized candidate matrix and applies the
adjudication engine to all 15 frozen rows.
"""

import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from availability.services.issue97_stage_e_adjudication import (  # noqa: E402
    ABSTENTION,
    INDEPENDENT,
    LAST_KNOWN,
    UNDEFINED,
    UNRESOLVED,
    YES,
    Candidate,
    Identity,
    Observation,
    Price,
    Signals,
    adjudicate_pair,
    app_display_retrieval,
    metrics,
)


MATRIX = ROOT / "docs/audits/issue97/stage-e-candidate-matrix.csv"
SLOT = datetime(2030, 1, 1, 15, tzinfo=timezone.utc)
REFERENCE = datetime(2026, 11, 23, 15, tzinfo=timezone.utc)


def signals_for(rule, offer_class):
    return Signals(
        typed_title_native_id=True,
        verified_nl_context=True,
        title_specific_positive=True,
        class_specific_action=True,
        zero_payment=offer_class in ("free", "free_with_ads"),
        ad_funded=offer_class == "free_with_ads",
    )


def build(row):
    candidate = Candidate(
        row["candidate_id"], row["batch"], row["media_type"], int(row["tmdb_id"]),
        int(row["year"]), row["market"], row["provider_slug"], row["offer_class"],
        row["evidence_rule"],
    )
    manifest = Identity(
        candidate.media_type, candidate.tmdb_id, candidate.year,
        row["title"], f"native-{candidate.tmdb_id}",
    )
    index = Observation(
        "justwatch", manifest, "NL", "request_scoped", candidate.provider_slug,
        f"jw-{candidate.tmdb_id}", candidate.offer_class,
        datetime(2026, 11, 24, 12, tzinfo=timezone.utc),
        datetime(2026, 11, 24, 12, 0, 5, tzinfo=timezone.utc),
        timedelta(seconds=5), "fresh", claim=YES,
        prices=(Price(1299, "EUR", "gross", "sd"),),
    )
    first_party = Observation(
        candidate.provider_slug, manifest, "NL", "explicit", candidate.provider_slug,
        f"native-{candidate.tmdb_id}", candidate.offer_class,
        REFERENCE + timedelta(minutes=13), REFERENCE + timedelta(minutes=13, seconds=5),
        timedelta(seconds=5), "fresh", claim=YES,
        signals=signals_for(candidate.evidence_rule, candidate.offer_class),
        prices=(Price(1299, "EUR", "gross", "sd"),),
    )
    return candidate, index, first_party, manifest


def main():
    with MATRIX.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 15, f"expected 15 frozen rows, got {len(rows)}"

    results = [adjudicate_pair(*build(row), SLOT) for row in rows]
    assert sum(item.batch == "A" for item in results) == 9
    assert sum(item.batch == "B" for item in results) == 6
    for result in results:
        assert not result.eligible, result.candidate_id
        assert result.outcome in (ABSTENTION, UNRESOLVED), (result.candidate_id, result.outcome)
        assert result.independence == INDEPENDENT, result.candidate_id
        assert result.entitlement == result.playback == "untested"

    summary = metrics(tuple(results))
    assert all(value == UNDEFINED for value in summary.rates.values())

    display = app_display_retrieval(tuple(item.candidate_id for item in results), ())
    assert display.rate == 0.0 and len(display.retrieval_misses) == 15
    last_known = Observation(**{**build(rows[0])[2].__dict__, "freshness": LAST_KNOWN})
    assert last_known.freshness == LAST_KNOWN

    print("Offline adjudication PASS: 15 rows (A=9, B=6), no binary outcomes, independence confirmed")
    print("Fresh adjudicated pairs: 0. Accuracy rates: UNDEFINED (all six denominators zero)")
    print("Synthetic display control: empty display -> 15 would be logged as retrieval misses on a completed read")
    print("Entitlement and playback: untested. No provider calls, no database writes.")


if __name__ == "__main__":
    main()
