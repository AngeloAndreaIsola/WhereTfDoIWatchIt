import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from uuid import NAMESPACE_URL, uuid5
from unittest import skipUnless
from unittest.mock import Mock, patch

import requests
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase

from availability.domain.models import DiscoveredContent, ProviderOffer, ProviderOfferWithdrawal
from availability.models import (
    AvailabilityExperimentAttemptOutcome,
    AvailabilityExperimentScope,
    Country,
    Source,
)
from availability.services.experiment_runner import (
    AvailabilityExperiment,
    AvailabilityExperimentAttempt,
    ExperimentBlocked,
    ReservedTransport,
    ScopedSource,
    _reserve_attempt,
    _incomplete_scope_ids,
    _quota_evidence,
    _mediaset_http_post,
    issue_authorization,
    manifest_digest,
    read_authorization,
    read_only_preflight,
    execute_manifest,
    validate_manifest,
)
from availability.sources.base import ContentRequest, SourceFetchError, SourceFetchResult
from availability.sources.justwatch_client import SEARCH_TITLES_QUERY, JustWatchClient, JustWatchTransportError
from availability.sources.mediaset_infinity import MediasetInfinityClient
from tracker.models import Movie, RefreshRequest


MANIFEST_PATH = Path(__file__).resolve().parents[3] / "docs/experiments/stage2e_manifest.example.json"


def load_manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def jw_scope():
    return next(s for s in load_manifest()["scopes"] if s["source_slug"] == "justwatch" and s["tmdb_id"] == 27205)


def jw_payload(title="Inception", country="NL", operation="GetSearchTitles"):
    title_filter = {
        "includeTitlesWithoutUrl": True,
        "packages": None,
        "objectTypes": ["MOVIE"],
        "releaseYear": {"min": None, "max": None},
        "searchQuery": title,
    }
    return {
        "operationName": operation,
        "variables": {
            "filter": {"bestOnly": True}, "formatOfferIcon": "PNG",
            "formatPoster": "JPG", "profile": "S718", "backdropProfile": "S1920",
            "country": country, "language": "en", "first": 20, "offset": None,
            "popularTitlesFilter": title_filter, "searchTitlesFilter": title_filter,
        },
        "query": SEARCH_TITLES_QUERY,
    }


class ManifestValidationTests(SimpleTestCase):
    def test_fixed_manifest_has_exact_three_scopes_and_eight_application_attempts(self):
        manifest = load_manifest()
        self.assertTrue(validate_manifest(manifest))
        self.assertEqual(sum(s["maximum_attempts"] for s in manifest["scopes"]), 8)
        self.assertEqual(manifest["maximum_attempts"], 11)

    def test_unlisted_title_country_and_source_are_denied(self):
        for key, value in (("tmdb_id", 999), ("country", "US"), ("source_slug", "watchmode")):
            manifest = load_manifest()
            manifest["scopes"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ExperimentBlocked):
                validate_manifest(manifest)
        for source_slug in ("streaming_availability", "watchmode", "legacy_scraper"):
            manifest = load_manifest()
            manifest["scopes"][0]["source_slug"] = source_slug
            with self.subTest(source_slug=source_slug), self.assertRaises(ExperimentBlocked):
                validate_manifest(manifest)

    def test_manifest_extension_or_budget_change_is_denied(self):
        manifest = load_manifest()
        manifest["maximum_attempts"] = 12
        with self.assertRaises(ExperimentBlocked):
            validate_manifest(manifest)
        manifest = load_manifest()
        manifest["scopes"][0]["unexpected"] = True
        with self.assertRaises(ExperimentBlocked):
            validate_manifest(manifest)

    @patch("availability.services.experiment_runner.database_identity", return_value="db-hash")
    def test_authorization_is_digest_environment_and_expiry_bound(self, _identity):
        import os
        os.environ["AVAILABILITY_EXPERIMENT_AUTH_KEY"] = "x" * 40
        manifest = load_manifest()
        expiry = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        first = issue_authorization(manifest, expires_at=expiry)
        second = issue_authorization(manifest, expires_at=expiry)
        first_payload = read_authorization(first, manifest)
        second_payload = read_authorization(second, manifest)
        self.assertEqual(first_payload["authorization_id"], second_payload["authorization_id"])
        self.assertEqual(first_payload["manifest_digest"], manifest_digest(manifest))
        changed = load_manifest()
        changed["scopes"][0]["scope_id"] = "changed"
        with self.assertRaises(ExperimentBlocked):
            read_authorization(first, changed)

    @patch("availability.services.experiment_runner.database_identity", return_value="db-hash")
    def test_authorization_rejects_missing_signing_key(self, _identity):
        import os
        old = os.environ.pop("AVAILABILITY_EXPERIMENT_AUTH_KEY", None)
        try:
            expiry = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
            with self.assertRaises(ExperimentBlocked):
                issue_authorization(load_manifest(), expires_at=expiry)
        finally:
            if old is not None:
                os.environ["AVAILABILITY_EXPERIMENT_AUTH_KEY"] = old

    @patch("availability.services.experiment_runner.database_identity", return_value="db-hash")
    def test_expired_authorization_cannot_be_issued(self, _identity):
        import os
        os.environ["AVAILABILITY_EXPERIMENT_AUTH_KEY"] = "x" * 40
        expiry = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        with self.assertRaises(ExperimentBlocked):
            issue_authorization(load_manifest(), expires_at=expiry)


class GuardedTransportTests(SimpleTestCase):
    def setUp(self):
        self.authorization = {
            "authorization_id": "10101010-1010-4010-8010-101010101010",
            "manifest_digest": manifest_digest(load_manifest()),
            "database_identity": "database",
            "maximum_attempts": 11,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        }
        self.scope = jw_scope()
        self.transport = ReservedTransport(
            authorization=self.authorization, scope=self.scope, source_slug="justwatch"
        )

    @patch("availability.services.experiment_runner._record_outcome")
    @patch("availability.services.experiment_runner._reserve_attempt", return_value=Mock())
    @patch("availability.services.experiment_runner.requests.Session.post")
    def test_exact_allowlisted_post_is_counted_and_redirects_are_disabled(self, post, reserve, _outcome):
        response = Mock(status_code=200)
        post.return_value = response
        self.assertIs(self.transport(self.scope["endpoint"], json=jw_payload()), response)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(reserve.call_count, 1)
        self.assertIs(post.call_args.kwargs["allow_redirects"], False)
        self.assertEqual(reserve.call_args.args[1]["scope_id"], self.scope["scope_id"])

    @patch("availability.services.experiment_runner.requests.Session.post")
    def test_unexpected_host_title_country_and_operation_are_denied_before_network(self, post):
        cases = [
            ("https://evil.example/graphql", jw_payload()),
            (self.scope["endpoint"], jw_payload(title="The Matrix")),
            (self.scope["endpoint"], jw_payload(country="IT")),
            (self.scope["endpoint"], jw_payload(operation="GetProviders")),
        ]
        for endpoint, payload in cases:
            with self.subTest(endpoint=endpoint, payload=payload), self.assertRaises(ExperimentBlocked):
                self.transport(endpoint, json=payload)
        self.assertEqual(post.call_count, 0)

    @patch("availability.services.experiment_runner.requests.Session.post")
    def test_unexpected_http_method_is_denied(self, post):
        with self.assertRaises(TypeError):
            self.transport(self.scope["endpoint"], json=jw_payload(), method="GET")
        self.assertEqual(post.call_count, 0)

    @patch("availability.services.experiment_runner.requests.Session.post")
    def test_transport_cannot_rebind_authorized_scope_to_an_unlisted_title(self, post):
        forged_scope = dict(self.scope, tmdb_id=603, title="The Matrix", country="US")
        with self.assertRaises(ExperimentBlocked):
            ReservedTransport(
                authorization=self.authorization, scope=forged_scope, source_slug="justwatch"
            )
        self.assertEqual(post.call_count, 0)

    def test_transport_session_ignores_proxy_environment_and_has_no_retry_adapter(self):
        adapter = self.transport.session.get_adapter("https://apis.justwatch.com")
        self.assertFalse(self.transport.session.trust_env)
        self.assertEqual(adapter.max_retries.total, 0)

    @patch("availability.services.experiment_runner._record_outcome")
    @patch("availability.services.experiment_runner._reserve_attempt", return_value=Mock())
    @patch("availability.services.experiment_runner.requests.Session.post")
    def test_graphql_document_and_media_filter_are_exact(self, post, _reserve, _outcome):
        post.return_value = Mock(status_code=200)
        bad = jw_payload()
        bad["variables"]["searchTitlesFilter"]["objectTypes"] = ["SHOW"]
        with self.assertRaises(ExperimentBlocked):
            self.transport(self.scope["endpoint"], json=bad)
        bad = jw_payload()
        bad["query"] += " # changed"
        with self.assertRaises(ExperimentBlocked):
            self.transport(self.scope["endpoint"], json=bad)
        self.assertEqual(post.call_count, 0)

    @patch("availability.services.experiment_runner._record_outcome")
    @patch("availability.services.experiment_runner._reserve_attempt", return_value=Mock())
    @patch("availability.services.experiment_runner.requests.Session.post")
    def test_mediaset_scope_without_reviewed_guid_is_denied_before_network(self, post, reserve, _outcome):
        scope = next(s for s in load_manifest()["scopes"] if s["source_slug"] == "mediaset_infinity")
        with self.assertRaisesRegex(ExperimentBlocked, "independently verified provider GUID"):
            ReservedTransport(
                authorization=self.authorization, scope=scope, source_slug="mediaset_infinity"
            )
        self.assertEqual(post.call_count, 0)
        self.assertEqual(reserve.call_count, 0)

    @patch("availability.services.experiment_runner.validate_scope")
    @patch("availability.services.experiment_runner._record_outcome")
    @patch("availability.services.experiment_runner._reserve_attempt", return_value=Mock())
    @patch("availability.services.experiment_runner.requests.Session.post")
    def test_mediaset_wired_callback_pins_guid_headers_timeout_and_http_200(
        self, post, reserve, _outcome, _validate_scope
    ):
        scope = dict(next(s for s in load_manifest()["scopes"] if s["source_slug"] == "mediaset_infinity"))
        scope["verified_provider_id"] = "fixture-guid"
        guard = ReservedTransport(
            authorization=self.authorization, scope=scope, source_slug="mediaset_infinity"
        )
        response = Mock(status_code=200)
        response.json.return_value = {"data": {"getSearchPage": {"areaContainersConnection": {
            "areaContainers": [{"areas": [{"sections": [{"collections": [{"itemsConnection": {
                "items": [{"__typename": "VideoItem", "guid": "fixture-guid", "cardTitle": "Dune", "editorialType": "Movie"}]
            }}]}]}]}]
        }}}}
        post.return_value = response
        client = MediasetInfinityClient(http_post=_mediaset_http_post(guard))
        client.search("Dune")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(reserve.call_count, 1)
        self.assertEqual(post.call_args.kwargs["headers"]["x-m-platform"], "WEB")
        self.assertEqual(post.call_args.kwargs["headers"]["x-m-app-version"], "1.5.2")
        self.assertEqual(post.call_args.kwargs["timeout"], 30)
        self.assertFalse(post.call_args.kwargs["allow_redirects"])

        post.return_value = Mock(status_code=201)
        with self.assertRaises(SourceFetchError):
            guard2 = ReservedTransport(
                authorization=self.authorization, scope=scope, source_slug="mediaset_infinity"
            )
            MediasetInfinityClient(http_post=_mediaset_http_post(guard2)).search("Dune")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(reserve.call_count, 2)

    @patch("availability.services.experiment_runner._record_outcome")
    @patch("availability.services.experiment_runner._reserve_attempt", return_value=Mock())
    @patch("availability.services.experiment_runner.requests.Session.post")
    def test_redirect_is_one_spent_attempt_and_never_followed(self, post, reserve, _outcome):
        post.return_value = Mock(status_code=302, headers={"Location": "https://evil.example/"})
        with self.assertRaises(ExperimentBlocked):
            self.transport(self.scope["endpoint"], json=jw_payload())
        self.assertEqual(post.call_count, 1)
        self.assertEqual(reserve.call_count, 1)
        self.assertFalse(post.call_args.kwargs["allow_redirects"])

    @patch("availability.services.experiment_runner._record_outcome")
    @patch("availability.services.experiment_runner._reserve_attempt", return_value=Mock())
    @patch("availability.services.experiment_runner.requests.Session.post")
    def test_justwatch_retry_exhaustion_counts_every_outbound_attempt(self, post, reserve, outcome):
        post.return_value = Mock(status_code=503)
        client = JustWatchClient(http_post=self.transport, retries=3, backoff=0, sleeper=lambda _delay: None)
        with self.assertRaises(JustWatchTransportError):
            client.search_titles("Inception", country="NL", object_types=["MOVIE"])
        self.assertEqual(post.call_count, 3)
        self.assertEqual(reserve.call_count, 3)
        self.assertEqual(outcome.call_count, 3)

    @patch("availability.services.experiment_runner._record_outcome")
    @patch("availability.services.experiment_runner._reserve_attempt", return_value=Mock())
    @patch("availability.services.experiment_runner.requests.Session.post", side_effect=requests.ConnectionError("offline"))
    def test_connection_interruption_is_ambiguous_and_spent(self, post, reserve, outcome):
        with self.assertRaises(ExperimentBlocked):
            self.transport(self.scope["endpoint"], json=jw_payload())
        self.assertEqual(post.call_count, 1)
        self.assertEqual(reserve.call_count, 1)
        outcome.assert_not_called()

    @patch("availability.services.experiment_runner._record_outcome", side_effect=RuntimeError("ledger persistence failed"))
    @patch("availability.services.experiment_runner._reserve_attempt", return_value=Mock())
    @patch("availability.services.experiment_runner.requests.Session.post")
    def test_outcome_persistence_failure_keeps_attempt_spent(self, post, reserve, _outcome):
        post.return_value = Mock(status_code=200)
        with self.assertRaisesRegex(RuntimeError, "ledger persistence failed"):
            self.transport(self.scope["endpoint"], json=jw_payload())
        self.assertEqual(post.call_count, 1)
        self.assertEqual(reserve.call_count, 1)

    def test_scope_proxy_denies_unlisted_identity_without_source_call(self):
        source = Mock(slug="justwatch", capabilities=Mock())
        scoped = ScopedSource(source, self.scope)
        with self.assertRaises(ExperimentBlocked):
            scoped.fetch(ContentRequest(tmdb_id=603, content_type="movie", title="The Matrix"), country="NL")
        source.fetch.assert_not_called()

    def test_scope_proxy_drops_provider_catalogue_and_withdrawal_side_effects(self):
        source = Mock(slug="justwatch", capabilities=Mock(), name="JustWatch", source_type="aggregator")
        malicious = SourceFetchResult(
            contents=[DiscoveredContent(provider_id="netflix", provider_content_id="other", content_type="movie", title="Other")],
            offer_withdrawals=[ProviderOfferWithdrawal(provider_id="netflix", provider_content_id="other", country="US", content_type="movie")],
            offers=[
                ProviderOffer(provider_id="netflix", provider_content_id="inception", country="NL",
                              content_type="movie", tmdb_id=27205, season_number=None,
                              episode_number=None, offer_type="subscription"),
                ProviderOffer(provider_id="netflix", provider_content_id="inception-rent", country="NL",
                              content_type="movie", tmdb_id=27205, season_number=None,
                              episode_number=None, offer_type="rent"),
            ],
        )
        source.fetch.return_value = malicious
        scoped = ScopedSource(source, self.scope)
        result = scoped.fetch(ContentRequest(tmdb_id=27205, content_type="movie", title="Inception"), country="NL")
        self.assertEqual(result.contents, [])
        self.assertEqual(result.offer_withdrawals, [])
        self.assertEqual([offer.offer_type for offer in result.offers], ["subscription"])
        self.assertEqual(len(malicious.offers), 2)
        self.assertEqual(len(malicious.contents), 1)


@skipUnless(connection.vendor == "postgresql", "durable reservation integration tests require isolated PostgreSQL")
class PostgreSQLReservationTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.authorization = {
            "authorization_id": "20202020-2020-4020-8020-202020202020",
            "manifest_digest": manifest_digest(load_manifest()),
            "database_identity": "isolated-test-database",
            "maximum_attempts": 11,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        }
        AvailabilityExperiment.objects.create(
            authorization_id=self.authorization["authorization_id"],
            manifest_digest=self.authorization["manifest_digest"],
            database_identity=self.authorization["database_identity"],
            maximum_attempts=11,
        )

    def test_attempt_ordinals_are_durable_and_scope_budget_is_enforced(self):
        scope = jw_scope()
        records = [_reserve_attempt(self.authorization, scope, "GetSearchTitles", "POST", "apis.justwatch.com") for _ in range(3)]
        self.assertEqual([row.ordinal for row in records], [1, 2, 3])
        with self.assertRaises(ExperimentBlocked):
            _reserve_attempt(self.authorization, scope, "GetSearchTitles", "POST", "apis.justwatch.com")
        self.assertEqual(AvailabilityExperimentAttempt.objects.count(), 3)

    def test_concurrent_runners_cannot_allocate_more_than_scope_or_global_limit(self):
        scope = jw_scope()
        def reserve_one(_):
            try:
                return _reserve_attempt(self.authorization, scope, "GetSearchTitles", "POST", "apis.justwatch.com").ordinal
            except ExperimentBlocked:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            ordinals = list(pool.map(reserve_one, range(8)))
        saved = list(AvailabilityExperimentAttempt.objects.order_by("ordinal").values_list("ordinal", flat=True))
        self.assertEqual(sorted(x for x in ordinals if x is not None), [1, 2, 3])
        self.assertEqual(saved, [1, 2, 3])

    def test_eleven_global_ceiling_cannot_be_exceeded_across_sources_and_scopes(self):
        scopes = load_manifest()["scopes"]
        experiment = AvailabilityExperiment.objects.get(pk=self.authorization["authorization_id"])
        # Model the three bounded evidence-page reservations. No evidence
        # transport exists in this version, so these rows are fixtures only.
        for ordinal in range(1, 4):
            AvailabilityExperimentAttempt.objects.create(
                experiment=experiment, ordinal=ordinal, scope_id="provider-evidence",
                source_slug="provider_evidence", operation="provider_page", method="GET",
                destination_host="evidence.example",
            )
        for scope in scopes:
            for _ in range(scope["maximum_attempts"]):
                operation = "GetSearchTitles" if scope["source_slug"] == "justwatch" else "MediasetSearch"
                if scope["source_slug"] == "mediaset_infinity" and _ == 1:
                    operation = "MediasetMovie"
                from urllib.parse import urlsplit
                _reserve_attempt(
                    self.authorization, scope, operation, "POST",
                    urlsplit(scope["endpoint"]).hostname,
                )
        self.assertEqual(AvailabilityExperimentAttempt.objects.count(), 11)
        with self.assertRaises(ExperimentBlocked):
            _reserve_attempt(self.authorization, scopes[0], "MediasetSearch", "POST", "frontier.api-graph.mediaset.it")

    def test_ledger_rows_are_append_only(self):
        from django.db import DatabaseError, transaction
        row = _reserve_attempt(self.authorization, jw_scope(), "GetSearchTitles", "POST", "apis.justwatch.com")
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                row.delete()
        self.assertEqual(AvailabilityExperimentAttempt.objects.count(), 1)

    def test_reservation_refuses_enclosing_transaction(self):
        from django.db import transaction
        with transaction.atomic():
            with self.assertRaises(ExperimentBlocked):
                _reserve_attempt(self.authorization, jw_scope(), "GetSearchTitles", "POST", "apis.justwatch.com")
        self.assertEqual(AvailabilityExperimentAttempt.objects.count(), 0)

    def test_restart_reconciliation_blocks_both_reserved_and_responded_incomplete_scopes(self):
        scope = jw_scope()
        attempt = _reserve_attempt(self.authorization, scope, "GetSearchTitles", "POST", "apis.justwatch.com")
        self.assertEqual(_incomplete_scope_ids(self.authorization["authorization_id"]), {scope["scope_id"]})
        AvailabilityExperimentAttemptOutcome.objects.create(
            attempt=attempt, outcome="success", http_status=200,
        )
        # A response does not imply that canonical persistence and scope
        # completion committed; restart must not issue the request again.
        self.assertEqual(_incomplete_scope_ids(self.authorization["authorization_id"]), {scope["scope_id"]})
        experiment = AvailabilityExperiment.objects.get(pk=self.authorization["authorization_id"])
        AvailabilityExperimentScope.objects.create(experiment=experiment, scope_id=scope["scope_id"])
        self.assertEqual(_incomplete_scope_ids(self.authorization["authorization_id"]), set())


@skipUnless(connection.vendor == "postgresql", "preflight integration tests require isolated PostgreSQL")
class PostgreSQLPreflightTests(TransactionTestCase):
    def setUp(self):
        self.manifest = load_manifest()
        Country.objects.update_or_create(code="IT", defaults={"name": "Italy", "enabled": True})
        Country.objects.update_or_create(code="NL", defaults={"name": "Netherlands", "enabled": True})
        Movie.objects.update_or_create(
            tmdb_id=438631,
            defaults={"title": "Dune", "year": 2021, "content_type": "movie"},
        )
        Movie.objects.update_or_create(
            tmdb_id=27205,
            defaults={"title": "Inception", "year": 2010, "content_type": "movie"},
        )
        Source.objects.update_or_create(
            slug="justwatch",
            defaults={"name": "JustWatch", "type": "aggregator", "enabled": True,
                      "configuration": {"expected_refresh_interval_seconds": 43200, "api_token": "never-report-me"}},
        )
        Source.objects.update_or_create(
            slug="mediaset_infinity",
            defaults={"name": "Mediaset Infinity", "type": "api", "enabled": True,
                      "configuration": {"expected_refresh_interval_seconds": 86400}},
        )
        import os
        os.environ["AVAILABILITY_EXPERIMENT_QUOTA_EVIDENCE"] = json.dumps({
            "justwatch": {"status": "unmetered_public", "checked_at": datetime.now(timezone.utc).isoformat()},
            "mediaset_infinity": {"status": "unmetered_public", "checked_at": datetime.now(timezone.utc).isoformat()},
        })

    def tearDown(self):
        import os
        os.environ.pop("AVAILABILITY_EXPERIMENT_QUOTA_EVIDENCE", None)

    def test_read_only_preflight_reports_scope_config_freshness_queue_and_lock_state(self):
        before = (Movie.objects.count(), Source.objects.count(), AvailabilityExperimentAttempt.objects.count())
        report = read_only_preflight(self.manifest)
        after = (Movie.objects.count(), Source.objects.count(), AvailabilityExperimentAttempt.objects.count())
        self.assertEqual(report["readiness"], "blocked", report)
        self.assertTrue(any("provider GUID" in blocker for blocker in report["blockers"]))
        self.assertEqual(before, after)
        self.assertIn("expected_refresh_interval_seconds", report["scopes"][1]["source_configuration"])
        self.assertEqual(report["scopes"][1]["source_configuration"]["api_token"], "[redacted]")
        self.assertEqual(report["queue_scope_overlap"], 0)
        self.assertEqual(report["expected_maximum_attempts"], 11)

    def test_preflight_blocks_ambiguous_current_experiment_reservation(self):
        from availability.services.experiment_runner import database_identity
        identity = database_identity()
        experiment_id = str(uuid5(NAMESPACE_URL, f"stage2e-availability-refresh:{identity}"))
        experiment = AvailabilityExperiment.objects.create(
            authorization_id=experiment_id,
            manifest_digest=manifest_digest(self.manifest),
            database_identity=identity,
            maximum_attempts=11,
        )
        AvailabilityExperimentAttempt.objects.create(
            experiment=experiment,
            ordinal=1,
            scope_id="inception-nl-justwatch",
            source_slug="justwatch",
            operation="GetSearchTitles",
            method="POST",
            destination_host="apis.justwatch.com",
        )
        report = read_only_preflight(self.manifest)
        self.assertEqual(report["reservations"]["current_experiment_ambiguous"], 1)
        self.assertTrue(any("ambiguous reservations" in blocker for blocker in report["blockers"]))

    def test_preflight_blocks_responded_but_incomplete_exhausted_scope(self):
        from availability.services.experiment_runner import database_identity
        identity = database_identity()
        experiment_id = str(uuid5(NAMESPACE_URL, f"stage2e-availability-refresh:{identity}"))
        experiment = AvailabilityExperiment.objects.create(
            authorization_id=experiment_id,
            manifest_digest=manifest_digest(self.manifest),
            database_identity=identity,
            maximum_attempts=11,
        )
        for ordinal in range(1, 4):
            attempt = AvailabilityExperimentAttempt.objects.create(
                experiment=experiment,
                ordinal=ordinal,
                scope_id="inception-nl-justwatch",
                source_slug="justwatch",
                operation="GetSearchTitles",
                method="POST",
                destination_host="apis.justwatch.com",
            )
            AvailabilityExperimentAttemptOutcome.objects.create(
                attempt=attempt, outcome="failure", http_status=503
            )
        report = read_only_preflight(self.manifest)
        self.assertEqual(report["reservations"]["current_experiment_ambiguous"], 0)
        self.assertIn("inception-nl-justwatch", report["reservations"]["current_experiment_incomplete_scopes"])
        self.assertTrue(any("without completion markers" in blocker for blocker in report["blockers"]))
        self.assertTrue(any("per-scope attempts" in blocker for blocker in report["blockers"]))

    def test_pending_exact_scope_and_missing_quota_block_preflight(self):
        os.environ.pop("AVAILABILITY_EXPERIMENT_QUOTA_EVIDENCE", None)
        movie = Movie.objects.get(tmdb_id=27205)
        RefreshRequest.objects.create(movie=movie, source="justwatch", country="NL")
        report = read_only_preflight(self.manifest)
        self.assertEqual(report["readiness"], "blocked")
        self.assertEqual(report["queue_scope_overlap"], 1)
        self.assertTrue(any("quota" in blocker for blocker in report["blockers"]))

    def test_zero_or_boolean_quota_is_not_accepted(self):
        os.environ["AVAILABILITY_EXPERIMENT_QUOTA_EVIDENCE"] = json.dumps({
            "justwatch": {"status": "available", "remaining": 0, "checked_at": datetime.now(timezone.utc).isoformat()},
            "mediaset_infinity": {"status": "available", "remaining": True, "checked_at": datetime.now(timezone.utc).isoformat()},
        })
        self.assertEqual(_quota_evidence(), {})

    def test_source_lock_inspection_detects_ordinary_catalogue_overlap(self):
        from tracker.services.bulk_refresh import release_source_lock, try_source_lock
        from availability.services.experiment_runner import _source_lock_states
        self.assertTrue(try_source_lock("justwatch"))
        try:
            states = _source_lock_states(self.manifest["scopes"])
            self.assertTrue(states["justwatch"]["held"])
        finally:
            release_source_lock("justwatch")

    def test_preflight_detects_shared_a7_title_drain_lock(self):
        from tracker.services.refresh_requests import title_drain_lock
        with title_drain_lock() as acquired:
            self.assertTrue(acquired)
            report = read_only_preflight(self.manifest)
        self.assertTrue(report["active_title_drain"]["held"])
        self.assertIn("shared A7 title-drain advisory lock is currently held", report["blockers"])

    def test_concurrent_execute_call_cannot_repeat_scopes_during_lock_gap(self):
        auth = {
            "authorization_id": str(uuid5(NAMESPACE_URL, "stage2e-availability-refresh:test-db")),
            "manifest_digest": manifest_digest(self.manifest),
            "database_identity": "test-db",
            "maximum_attempts": 11,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        }
        entered = Event()
        release = Event()
        calls = []
        def mock_refresh(_service, movie, *, country=None):
            calls.append((movie.tmdb_id, country))
            if len(calls) == 1:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("test synchronization timed out")
            return None
        from availability.services import experiment_runner
        with patch.object(experiment_runner, "database_identity", return_value="test-db"), \
             patch.object(experiment_runner, "read_only_preflight", return_value={"readiness": "ready", "blockers": []}), \
             patch.object(experiment_runner, "ReservedTransport", return_value=Mock(session=Mock())), \
             patch("availability.services.refresh.AvailabilityRefreshService.refresh_content", new=mock_refresh):
            with ThreadPoolExecutor(max_workers=1) as pool:
                first = pool.submit(execute_manifest, self.manifest, auth)
                self.assertTrue(entered.wait(5))
                with self.assertRaises(ExperimentBlocked):
                    execute_manifest(self.manifest, auth)
                release.set()
                result = first.result(timeout=10)
        self.assertEqual(len(calls), 3)
        self.assertEqual([row["status"] for row in result], ["completed"] * 3)
