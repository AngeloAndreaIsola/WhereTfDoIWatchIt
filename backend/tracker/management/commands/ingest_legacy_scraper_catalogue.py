"""Backfill: project every legacy scraper row onto the canonical catalogue.

Phase A moves the unified catalogue read path from
``tracker.ScrapedContentMapping`` to the canonical availability tables
(``ProviderContent`` / ``ContentMapping``). The scraper write paths bridge
every new row as they go (see ``ContentMatcher._upsert_mapping`` and the
scraper endpoints), but rows that existed *before* that switch were never
projected — this command ingests them once per deployment.

Idempotent: re-running upserts the same canonical identities, so it is safe
to run after a legacy CSV import or whenever a row was missed.

Local-only: no network, no TMDb, no offers are written — a legacy scrape is
a catalogue observation, never a commercial offer, so watchability and
ranking semantics stay untouched.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from availability.services.legacy_ingest import ingest_all


class Command(BaseCommand):
    help = (
        "Ingest legacy ScrapedContentMapping rows into the canonical "
        "availability catalogue (ProviderContent/ContentMapping), so the "
        "unified endpoint serves them without reading the legacy table."
    )

    def handle(self, *args, **options):
        count = ingest_all()
        self.stdout.write(
            self.style.SUCCESS(f"Ingested {count} legacy scraper row(s).")
        )
