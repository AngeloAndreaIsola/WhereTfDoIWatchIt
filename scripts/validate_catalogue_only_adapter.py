#!/usr/bin/env python3
"""Fixture-backed collector bridge rehearsal on an exact disposable clone.

No source/TMDb requests; all scenario rows roll back and clone sequences are
restored. Run inside the internal-only network with its DNS/socket guard.
"""
import argparse
from dataclasses import replace
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
    args = parser.parse_args()
    backend = Path(__file__).resolve().parents[1] / "backend"
    sys.path.insert(0, str(backend if backend.exists() else Path.cwd()))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    from django.db import connection, transaction
    from django.utils import timezone
    from availability.models import ContentMapping, Offer, Source, TmdbDetailsCache, TmdbSearchCache
    from availability.services.candidate_gateway import TMDbCandidateGateway
    from tracker.models import CatalogueDiscoveryState, CatalogueReconciliationRecord, DiscoveredCatalogueRecord
    from tracker.services.catalogue_only import CHECKPOINT_KEY, import_catalogue_batch, reconcile_catalogue_records
    from tracker.tests.catalogue_only_fixtures import PERMISSION_FIXTURE, one_page
    with connection.cursor() as c:
        c.execute("SELECT current_database()")
        database = c.fetchone()[0]
    if (connection.vendor != "postgresql" or database != args.database
            or database != "incremental_catalogue_only" or args.host != "wtdw-catalogue-only-db"
            or connection.settings_dict["HOST"] != args.host):
        parser.error("Require exact disposable incremental_catalogue_only@wtdw-catalogue-only-db")

    def tables():
        result = {}
        with connection.cursor() as c:
            c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
            for (table,) in c.fetchall():
                c.execute(f"SELECT to_jsonb(t)::text FROM {connection.ops.quote_name(table)} t ORDER BY to_jsonb(t)::text")
                values = c.fetchall()
                result[table] = dict(count=len(values), sha256=hashlib.sha256(json.dumps(values).encode()).hexdigest())
        return result

    def sequences():
        result = {}
        with connection.cursor() as c:
            c.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename")
            for (name,) in c.fetchall():
                c.execute(f"SELECT last_value,is_called FROM {connection.ops.quote_name(name)}")
                result[name] = c.fetchone()
        return result

    before, seq_before = tables(), sequences()
    assert before["tracker_discoveredcataloguerecord"]["count"] == 5095
    assert before["availability_contentmapping"]["count"] == 4769
    assert not Offer.objects.exists()
    assert CatalogueReconciliationRecord.objects.filter(status="BASELINED").count() == 326
    evidence = dict(database=database, host=args.host, external_http_requests=0, tmdb_http_requests=0,
                    evidence_kind="retained collector HTML, synthetic bounded XML and TMDb cache fixtures")

    with patch("requests.sessions.Session.request", side_effect=AssertionError("External HTTP forbidden")), \
            patch("tracker.services.catalogue_only.permission_for", return_value=PERMISSION_FIXTURE):
        with transaction.atomic():
            result, accounting = one_page(redirect=True)
            assert accounting["pages_attempted"] == 1 and accounting["http"]["attempted"] == 5
            assert [e["started"] for e in accounting["http"]["events"]] == [0,2,4,6,8]
            def imported(value=result, *, cursor=None):
                return import_catalogue_batch("tubi", "tubi", value, expected_cursor=cursor, apply=True)
            stable = tables()
            preview_import = import_catalogue_batch("tubi", "tubi", result, expected_cursor=None)
            assert preview_import["inserted"] == 1 and tables() == stable
            imported()
            record = DiscoveredCatalogueRecord.objects.get(provider__slug="tubi", provider_native_id="578912")
            assert record.title == "I, Robot" and record.original_metadata["year"] == 2004
            assert record.original_metadata["external_ids"] == {"imdb_id": "tt0343818"}
            assert "offline fixture" in record.original_metadata["permission_evidence"]
            def caches(ids):
                TmdbSearchCache.objects.update_or_create(query=TMDbCandidateGateway._normalize_query(record.title),
                    year=2004, content_type="movie", defaults={"results": [
                        dict(tmdb_id=i, title=record.title, year=2004, content_type="movie") for i in ids],
                        "is_negative": not bool(ids), "expires_at": timezone.now()+timedelta(days=1)})
                for pk in ids:
                    TmdbDetailsCache.objects.update_or_create(tmdb_id=pk, content_type="movie", defaults={
                        "details": dict(tmdb_id=pk,title=record.title,year=2004,content_type="movie"),
                        "is_negative":False,"expires_at":timezone.now()+timedelta(days=1)})
            def reconcile(apply=False):
                return reconcile_catalogue_records("tubi", record_ids=[record.pk], apply=apply)
            def preview():
                stable = tables()
                proposal = reconcile()
                assert tables() == stable and proposal["tmdb_budget"]["attempted"] == 0
                return proposal["records"][0]["status"]
            caches([1999996501])
            assert preview() == "VERIFIED"
            scenarios = {"exact_preview": "VERIFIED"}
            for name, ids, expected in (("ambiguous",[1999996501,1999996502],"AMBIGUOUS"),
                                        ("unmatched",[],"UNMATCHED")):
                with transaction.atomic():
                    caches(ids)
                    assert preview() == expected
                    assert reconcile(True)["records"][0]["status"] == expected
                    assert ContentMapping.objects.count() == 4769 and not Offer.objects.exists()
                    scenarios[name] = dict(preview=expected, apply=expected)
                    transaction.set_rollback(True)
            with transaction.atomic():
                source = Source.objects.get_or_create(slug="tubi", defaults={"name":"Tubi","type":"web"})[0]
                mapping = ContentMapping.objects.create(provider=record.provider, provider_content_id="578912",
                    source=source, content_type="movie", tmdb_id=1999996599, match_method="manual")
                assert preview() == "CONFLICTING"
                assert reconcile(True)["records"][0]["status"] == "CONFLICTING"
                mapping.refresh_from_db()
                assert mapping.tmdb_id == 1999996599
                scenarios["mapping_conflict"] = dict(preview="CONFLICTING",apply="CONFLICTING",mapping_preserved=True)
                transaction.set_rollback(True)
            stable = tables()
            with patch.object(CatalogueDiscoveryState, "save", side_effect=KeyboardInterrupt):
                try:
                    imported(replace(result,cursor="interrupted"), cursor=result.cursor)
                except KeyboardInterrupt:
                    pass
                else:
                    raise AssertionError("Expected simulated interruption")
            assert tables() == stable
            assert reconcile(True)["records"][0]["status"] == "ASSOCIATED"
            assert ContentMapping.objects.count() == 4770 and not Offer.objects.exists()
            assert imported()["unchanged"] == 1
            assert reconcile(True)["selected"] == 0
            assert DiscoveredCatalogueRecord.objects.count() == 5096
            checkpoint = CatalogueDiscoveryState.objects.get(provider=record.provider).cursor[CHECKPOINT_KEY]
            assert checkpoint["cursor"] == result.cursor
            assert CatalogueReconciliationRecord.objects.filter(status="BASELINED").count() == 326
            evidence.update(request_accounting=accounting,scenarios=scenarios,checkpoint=checkpoint,
                import_preview_read_only=True,reconciliation_previews_read_only=True,
                interrupted_import_rolled_back=True,replay_selected=0,offers_written=0,
                fixture_associations_added=1,all_original_deferred_preserved=True)
            transaction.set_rollback(True)
    with connection.cursor() as c:
        for name,(value,called) in seq_before.items():
            c.execute("SELECT setval(%s::regclass,%s,%s)",[name,value,called])
    assert tables() == before and sequences() == seq_before
    evidence.update(status="OFFLINE_ADAPTER_PASSED",tables_preserved=len(before),
        application_tables_preserved=sum(t.startswith(("tracker_","availability_")) for t in before),
        sequences_preserved=len(seq_before))
    print(json.dumps(evidence,indent=2))


if __name__ == "__main__":
    main()
