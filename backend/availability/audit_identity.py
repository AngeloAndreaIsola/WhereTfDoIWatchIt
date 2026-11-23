"""Read and compare offline TMDb identity evidence without Django or HTTP."""
from __future__ import annotations

import csv
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


MANIFEST_SOURCE = "tmdb_direct_id_details"
MANIFEST_COLUMNS = {
    "tmdb_id", "media_type", "canonical_title", "year", "source",
    "evidence_sha256", "observed_utc", "provenance",
}
# Typed aliases reviewed in docs/audits/stage2b/issue97-identity-decisions.md.
LOCALIZED_TITLE_ALIASES = {
    (619264, "movie", 2019): frozenset({"the platform", "il buco", "el hoyo"}),
    (2316, "series", 2005): frozenset({"the office us", "the office"}),
}


@dataclass(frozen=True)
class ManifestIdentity:
    canonical_title: str
    year: int
    evidence_sha256: str


def load_identity_manifest(
    path: Path, *, evidence_dir: Path | None = None,
) -> dict[tuple[int, str], ManifestIdentity]:
    entries = {}
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if MANIFEST_COLUMNS - set(reader.fieldnames or ()):
            raise ValueError("Identity manifest is missing required columns.")
        for line, record in enumerate(reader, start=2):
            try:
                tmdb_id = int(record["tmdb_id"])
                year = int(record["year"])
                observed = datetime.fromisoformat(record["observed_utc"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid identity manifest ID, year or time on line {line}.") from exc
            media_type = record["media_type"]
            title = (record["canonical_title"] or "").strip()
            digest = record["evidence_sha256"] or ""
            if (
                tmdb_id <= 0 or media_type not in {"movie", "series"} or year < 1880
                or not normalized_title(title) or observed.utcoffset() != timedelta(0)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or record["source"] != MANIFEST_SOURCE or not (record["provenance"] or "").strip()
            ):
                raise ValueError(f"Invalid identity manifest data on line {line}.")
            key = (tmdb_id, media_type)
            if key in entries:
                raise ValueError(f"Duplicate identity manifest entry on line {line}.")
            if evidence_dir is not None:
                response = evidence_dir / f"{media_type}_{tmdb_id}.json"
                try:
                    actual_digest = hashlib.sha256(response.read_bytes()).hexdigest()
                except OSError as exc:
                    raise ValueError(f"Cannot read identity manifest evidence for {response.name}.") from exc
                if actual_digest != digest:
                    raise ValueError(f"Identity manifest evidence SHA-256 mismatch for {response.name}.")
            entries[key] = ManifestIdentity(title, year, digest)
    return entries


def normalized_title(value: object) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join("".join(ch if ch.isalnum() else " " for ch in plain.casefold()).split())


def titles_match(case, expected, observed):
    aliases = LOCALIZED_TITLE_ALIASES.get((case["tmdb_id"], case["media_type"], case["year"]), ())
    return expected == observed or (expected in aliases and observed in aliases)


def manifest_identity_fields(case: dict, entry: ManifestIdentity | None) -> dict:
    review = []
    if entry:
        if case["year"] is None:
            review.append("COHORT_YEAR_MISSING")
        elif entry.year != case["year"]:
            review.append("YEAR_MISMATCH")
        if not titles_match(case, normalized_title(case["title"]), normalized_title(entry.canonical_title)):
            review.append("TITLE_ALIAS_REVIEW")
    return {
        "identity_manifest_title": entry.canonical_title if entry else None,
        "identity_manifest_year": entry.year if entry else None,
        "identity_manifest_evidence_sha256": entry.evidence_sha256 if entry else None,
        "identity_manifest_review": ",".join(review),
    }
