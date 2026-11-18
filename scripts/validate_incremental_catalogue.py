#!/usr/bin/env python3
"""Fixture-driven workflow rehearsal on an explicitly named disposable clone.

Restore a fresh live pg_dump into a separate PostgreSQL container, migrate that
clone, then run this script with its exact database/host. All simulations roll
back. HTTP is intercepted; this script never contacts a provider or TMDb.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    backend = Path(__file__).resolve().parents[1] / "backend"
    sys.path.insert(0, str(backend if backend.exists() else Path.cwd()))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    from django.apps import apps
    from django.db import connection, connections, transaction
    from django.utils import timezone
    from availability.models import ContentMapping, Offer
    from availability.tests.test_candidate_gateway import FakeResponse
    from tracker.models import (
        CanonicalBootstrapEvidence, CatalogueDiscoveryState, CatalogueReconciliationRecord,
        CatalogueReconciliationRun, DiscoveredCatalogueRecord, Movie, Provider, ScrapedContentMapping,
    )
    from tracker.scrapers.adapters import AnimeWorldScraper
    from tracker.scrapers.base import ScrapedItem
    from tracker.services.bulk_refresh import try_source_lock, release_source_lock
    from tracker.services.catalogue_discovery import _persist_unclassified_observation, run_discovery
    from tracker.services.incremental_catalogue_reconciliation import (
        _commit_record, eligible_records, run_incremental_reconciliation,
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        actual = cursor.fetchone()[0]
    if (connection.vendor != "postgresql" or not args.database.startswith("incremental_")
            or args.database != actual or connection.settings_dict["HOST"] != args.host):
        parser.error("Require the exact expected disposable incremental_* PostgreSQL database and host")

    def snapshot():
        result = {}
        for model in apps.get_models():
            if model._meta.app_label not in {"tracker", "availability"}:
                continue
            rows = list(model.objects.order_by("pk").values())
            result[model._meta.label] = {"count": len(rows), "sha256": hashlib.sha256(
                json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()}
        return result

    report = {"database": actual, "http": "fixture interception only", "scenarios": {}}
    before = snapshot()
    assert DiscoveredCatalogueRecord.objects.count() == 5095
    assert ContentMapping.objects.count() == 4769
    assert ScrapedContentMapping.objects.count() == 3300
    assert Offer.objects.count() == 0
    assert CatalogueReconciliationRecord.objects.filter(status="BASELINED").count() == 326
    assert not list(eligible_records("hydrahd")) and not list(eligible_records("animeworld"))
    historical = CatalogueDiscoveryState.objects.get(provider__slug="hydrahd").cursor["modes"]["historical"]
    report["historical_checkpoint"] = historical["parts"]
    report["baseline"] = {"discoveries": 5095, "associations": 4769, "unresolved_deferred": 326,
                          "legacy_mappings": 3300, "offers": 0, "eligible": 0}
    hydra = Provider.objects.get(slug="hydrahd")
    anime = Provider.objects.get(slug="animeworld")
    module = "tracker.services.incremental_catalogue_reconciliation"

    def job(slug="hydrahd", **kwargs):
        return run_incremental_reconciliation(slug, apply=True, interval=0, **kwargs)

    def response(title, pk=1999999001):
        return [FakeResponse(200, {"results": [{"id": pk, "title": title, "release_date": "2020-01-01"}]}),
                FakeResponse(200, {"id": pk, "title": title, "release_date": "2020-01-01"})]

    with patch("requests.Session.get", side_effect=AssertionError("Unexpected HTTP outside scenario fixture")), transaction.atomic():
        title = "Incremental rehearsal new film"
        item = ScrapedItem(title=title, provider_slug="hydrahd", year=2020, content_type="movie",
            source_url="https://hydrahd.gd/movie/incremental-rehearsal", raw_payload={"id": "incremental-rehearsal", "year": 2020})
        def page(scraper, slug, part, number, query=None):
            scraper.session.get("https://fixture.invalid/listing")
            return [item], True, 1, "rehearsal_terminal"
        with patch("tracker.services.catalogue_discovery._parts", return_value=["movies"]), patch(
                "tracker.services.catalogue_discovery._page", side_effect=page), patch("requests.Session.get", return_value=FakeResponse()):
            discovery = run_discovery("hydrahd", mode="incremental", request_budget=1, delay=0)
        record = DiscoveredCatalogueRecord.objects.get(provider=hydra, title=title)
        assert list(eligible_records("hydrahd"))[0][0].pk == record.pk
        assert record.classification_state == "PENDING"
        with patch("requests.Session.get", side_effect=response(title)) as http:
            matched = job(allow_network=True, request_budget=2)
        assert http.call_count == 2 and matched["records"][0]["status"] == "ASSOCIATED"
        assert job()["selected"] == 0
        assert ContentMapping.objects.count() == 4770
        assert CatalogueDiscoveryState.objects.get(provider=hydra).cursor["modes"]["historical"] == historical
        report["scenarios"]["new_hydra_and_replay"] = {"discovery_requests": discovery["requests_made"],
            "tmdb_requests": matched["tmdb_budget"]["attempted"], "associations_added": 1, "replay_selected": 0}

        xml = '''<rss xmlns:anime="https://www.animeworld.ac/"><channel><item><guid>incremental-rss-rehearsal</guid>
        <title>Episode update</title><anime:name>Incremental rehearsal anime</anime:name>
        <anime:link>https://www.animeworld.ac/play/incremental-rehearsal</anime:link><anime:type>anime</anime:type>
        </item></channel></rss>'''
        rss_item = AnimeWorldScraper().parse_rss(xml)[0]
        for _ in range(2):
            _persist_unclassified_observation(provider=anime, item=rss_item, partition="rss-animes")
        rss = DiscoveredCatalogueRecord.objects.get(provider=anime, provider_native_id="incremental-rss-rehearsal")
        missing = job("animeworld")
        assert missing["records"][0]["status"] == "UNMATCHED" and missing["tmdb_budget"]["attempted"] == 0
        assert job("animeworld")["selected"] == 0
        rss.refresh_from_db()
        assert rss.classified_content_type == "series"
        report["scenarios"]["rss_missing_year"] = {"guid_stable": True, "classified_as": "series", "state": "UNMATCHED", "tmdb_requests": 0}

        rss.original_metadata["year"] = 2020
        rss.save()
        # Deliberately share a numeric TMDb ID with the new movie. TV and movie
        # details, caches and canonical identities must remain separate.
        shared_id = 1999999001
        tv_responses = [FakeResponse(200, {"results": [{"id": shared_id, "name": rss.title,
                            "first_air_date": "2020-01-01"}]}),
                        FakeResponse(200, {"id": shared_id, "name": rss.title,
                            "first_air_date": "2020-01-01"})]
        with patch("requests.Session.get", side_effect=tv_responses) as http:
            rss_verified = job("animeworld", allow_network=True, request_budget=2)
        assert rss_verified["records"][0]["status"] == "ASSOCIATED" and http.call_count == 2
        assert ContentMapping.objects.get(provider=anime, provider_content_id=rss.provider_native_id).content_type == "series"
        assert set(Movie.objects.filter(tmdb_id=shared_id).values_list("content_type", flat=True)) == {"movie", "series"}
        assert job("animeworld")["selected"] == 0
        report["scenarios"]["rss_verified_and_media_identity_separation"] = {
            "state": "ASSOCIATED", "tmdb_requests": 2, "replay_selected": 0,
            "shared_numeric_id_distinct_movie_and_series": True}

        mapping = list(ContentMapping.objects.filter(provider_content_id=record.provider_native_id).values())
        proof = list(CanonicalBootstrapEvidence.objects.filter(discovered_record=record).values())
        record.refresh_from_db()
        record.title = "Incremental rehearsal conflicting title"
        record.save()
        with patch("requests.Session.get", side_effect=response(record.title, 1999999002)):
            changed = job(allow_network=True, request_budget=2)
        assert changed["records"][0]["status"] == "CONFLICTING"
        assert list(ContentMapping.objects.filter(provider_content_id=record.provider_native_id).values()) == mapping
        assert list(CanonicalBootstrapEvidence.objects.filter(discovered_record=record).values()) == proof
        assert job()["selected"] == 0
        report["scenarios"]["metadata_change"] = {"state": "CONFLICTING", "old_mapping_preserved": True, "old_proof_preserved": True}

        pending = []
        for suffix in ("a", "b"):
            pending.append(DiscoveredCatalogueRecord.objects.create(provider=hydra, title=f"Recovery {suffix}",
                provider_native_id=f"recovery-{suffix}", source_url=f"https://hydrahd.gd/movie/recovery-{suffix}",
                original_metadata={"catalogue_partition": "movies"}))
        def interrupt(rec, *args, **kwargs):
            if rec.pk == pending[1].pk:
                raise KeyboardInterrupt()
            return _commit_record(rec, *args, **kwargs)
        with patch(f"{module}._commit_record", side_effect=interrupt):
            try:
                job(record_ids=[r.pk for r in pending])
            except KeyboardInterrupt:
                pass
            else:
                raise AssertionError("Interruption fixture did not run")
        state = CatalogueReconciliationRun.objects.get(provider=hydra)
        assert state.status == "INTERRUPTED" and state.last_completed_record_id == pending[0].pk
        state.lease_owner, state.lease_expires_at = "terminated-worker", timezone.now() - timedelta(seconds=1)
        state.save()
        resumed = job(record_ids=[r.pk for r in pending])
        assert resumed["recovered_expired_lease"] and [r["record_id"] for r in resumed["records"]] == [pending[1].pk]
        report["scenarios"]["interruption_and_expired_lease"] = {"first_commit_retained": True, "only_remaining_record_resumed": True}

        key = "unofficial-canonical:hydrahd"
        assert try_source_lock(key)
        try:
            def competing():
                try:
                    return job()
                finally:
                    connections.close_all()
            with ThreadPoolExecutor(max_workers=1) as pool:
                concurrent = pool.submit(competing).result(timeout=15)
            assert concurrent["status"] == "LOCKED"
        finally:
            release_source_lock(key)
        report["scenarios"]["concurrent_jobs"] = {"second_postgres_connection": "LOCKED"}

        limited = DiscoveredCatalogueRecord.objects.create(provider=hydra, title="Budget rehearsal", provider_native_id="budget-rehearsal",
            source_url="https://hydrahd.gd/movie/budget-rehearsal", original_metadata={"catalogue_partition": "movies", "year": 2020})
        with patch("requests.Session.get", side_effect=response(limited.title, 1999999003)) as http:
            exhausted = job(record_ids=[limited.pk], allow_network=True, request_budget=1)
        assert exhausted["records"][0]["status"] == "BUDGET_EXHAUSTED" and http.call_count == 1
        with patch("requests.Session.get", return_value=response(limited.title, 1999999003)[1]) as http:
            recovered = job(record_ids=[limited.pk], review=True, allow_network=True, request_budget=1)
        assert recovered["records"][0]["status"] == "ASSOCIATED" and http.call_count == 1
        report["scenarios"]["budget_and_cache_recovery"] = {"first_requests": 1, "resume_requests": 1, "state": "ASSOCIATED"}

        from availability.models import TmdbSearchCache, TmdbDetailsCache
        ContentMapping.objects.filter(provider=hydra, provider_content_id=limited.provider_native_id).delete()
        CanonicalBootstrapEvidence.objects.filter(discovered_record=limited).update(verified_at=timezone.now() - timedelta(days=31))
        TmdbSearchCache.objects.filter(query=limited.title.casefold()).update(expires_at=timezone.now() - timedelta(seconds=1))
        TmdbDetailsCache.objects.filter(tmdb_id=1999999003).update(expires_at=timezone.now() - timedelta(seconds=1))
        expired = job(record_ids=[limited.pk])
        assert expired["records"][0]["status"] == "CACHE_MISS"
        assert not ContentMapping.objects.filter(provider=hydra, provider_content_id=limited.provider_native_id).exists()
        report["scenarios"]["expired_evidence"] = {"state": "CACHE_MISS", "stale_proof_not_applied": True}

        ambiguous = DiscoveredCatalogueRecord.objects.create(provider=hydra, title="Ambiguous rehearsal", provider_native_id="ambiguous-rehearsal",
            source_url="https://hydrahd.gd/movie/ambiguous-rehearsal", original_metadata={"catalogue_partition": "movies", "year": 2020})
        candidates = [{"id": pk, "title": ambiguous.title, "release_date": "2020-01-01"} for pk in (1999999004, 1999999005)]
        with patch("requests.Session.get", return_value=FakeResponse(200, {"results": candidates})) as http:
            unresolved = job(record_ids=[ambiguous.pk], allow_network=True, request_budget=1)
        assert unresolved["records"][0]["status"] == "AMBIGUOUS" and http.call_count == 1
        assert job(record_ids=[ambiguous.pk])["selected"] == 0
        report["scenarios"]["unresolved_candidates"] = {"state": "AMBIGUOUS", "immediate_retry_selected": 0}

        crash = DiscoveredCatalogueRecord.objects.create(provider=hydra, title="Atomic crash rehearsal", provider_native_id="atomic-crash-rehearsal",
            source_url="https://hydrahd.gd/movie/atomic-crash-rehearsal", original_metadata={"catalogue_partition": "movies", "year": 2020})
        with patch("requests.Session.get", side_effect=response(crash.title, 1999999006)), patch(
                f"{module}._apply_classification", side_effect=RuntimeError("crash inside atomic commit")):
            try:
                job(record_ids=[crash.pk], allow_network=True, request_budget=2)
            except RuntimeError:
                pass
            else:
                raise AssertionError("Atomic crash fixture did not run")
        assert not CanonicalBootstrapEvidence.objects.filter(discovered_record=crash).exists()
        assert not CatalogueReconciliationRecord.objects.filter(discovered_record=crash).exists()
        assert job(record_ids=[crash.pk])["records"][0]["status"] == "ASSOCIATED"
        report["scenarios"]["atomic_commit_failure"] = {"partial_evidence_and_outcome_rolled_back": True, "cached_resume": "ASSOCIATED"}

        limited.title = "Rate limit rehearsal"
        limited.save()
        with patch("requests.Session.get", return_value=FakeResponse(429, headers={"Retry-After": "900"})) as http:
            rate = job(record_ids=[limited.pk], allow_network=True, request_budget=2)
        assert rate["status"] == "BLOCKED" and http.call_count == 1 and job()["status"] == "COOLDOWN"
        preview = run_incremental_reconciliation("hydrahd", allow_network=True)
        assert preview["status"] == "COOLDOWN" and preview["tmdb_budget"]["attempted"] == 0
        report["scenarios"]["rate_limit"] = {"requests": 1, "persisted_cooldown": True}
        assert Offer.objects.count() == 0
        report["offers_created"] = 0
        transaction.set_rollback(True)

    after = snapshot()
    assert before == after, "Rehearsal must leave the migrated clone unchanged"
    report["rollback_preserved_all_application_tables"] = True
    report["tables_verified"] = len(before)
    report["status"] = "PASSED"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
