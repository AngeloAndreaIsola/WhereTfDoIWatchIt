"""Stage D execution tests use synthetic responses and disposable PostgreSQL."""

import base64
import hashlib
import hmac
import json
import os
from datetime import timedelta
from io import StringIO
from threading import Event, Thread
from unittest.mock import patch

import requests
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from availability.models import (
    AvailabilityExperiment, AvailabilityExperimentAttempt,
    AvailabilityExperimentAttemptOutcome, AvailabilityExperimentScope,
    AvailabilityStageDAttemptEvent, AvailabilityStageDRun,
    Country, JustWatchRequestBudget, Offer, Source,
)
from availability.services import issue97_stage_d_runner as runner
from availability.services.experiment_runner import APPROVED_MANIFEST, ExperimentBlocked, database_identity
from availability.services.issue97_stage_d_policy import EVIDENCE_ENV, KEY_ENV, load_policy
from availability.services.source_permissions import PERMISSION_KEY
from availability.sources import justwatch_transport
from availability.sources.base import SourceFetchStopped
from availability.sources.justwatch_client import SEASONS_QUERY
from tracker.models import Movie, RefreshRun


class StageDRunnerTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Stage D requires PostgreSQL")
        self.policy = load_policy()
        self.database = database_identity()
        self.now = timezone.now()
        self.key = "synthetic-test-key-for-mocked-transport-only"
        self.endpoint = self.policy["endpoint"]
        self.reference = "synthetic-stage-d-grant"
        dated = {
            "checked_at": self.now.isoformat(),
            "valid_until": (self.now + timedelta(hours=1)).isoformat(),
            "reviewed_by": "synthetic-reviewer",
        }
        self.receipt = {
            "status": "authorized", "endpoint": self.endpoint,
            "evidence_reference": self.reference, "reviewed_by": dated["reviewed_by"],
            "countries": ["NL"], "operations": ["search"],
            "valid_from": (self.now - timedelta(minutes=1)).isoformat(),
            "valid_until": dated["valid_until"],
            "maximum_requests": 12, "minimum_interval_seconds": 5,
        }
        self.evidence = {
            "database_identity": self.database,
            "grant_reference": self.reference,
            "exclusive_grant_use": True,
            "access_permission": {
                **dated, "evidence_reference": self.reference,
                "status": "authorized", "country": "NL", "endpoint": self.endpoint,
                "valid_from": self.receipt["valid_from"],
                "operations": ["GetSearchTitles"], "document_sha256": self.policy["document_sha256"],
                "scope_ids": [scope["scope_id"] for scope in self.policy["scopes"]],
                "maximum_requests": 12, "maximum_retries": 0,
            },
            "quota": {
                **dated, "evidence_reference": "synthetic-quota",
                "status": "available", "window_id": "synthetic-window",
                "remaining": 12, "spent_at_review": 0,
            },
            "rate_policy": {
                **dated, "evidence_reference": "synthetic-rate",
                "max_burst": 1, "maximum_in_flight": 1, "minimum_interval_seconds": 5,
            },
        }
        self.source, _ = Source.objects.update_or_create(slug="justwatch", defaults={
            "name": "JustWatch", "enabled": True, "configuration": {PERMISSION_KEY: self.receipt},
        })
        Country.objects.update_or_create(code="NL", defaults={"name": "Netherlands", "enabled": True})
        Movie.objects.bulk_create([
            Movie(tmdb_id=scope["tmdb_id"], content_type=scope["media_type"],
                  title=scope["title"], year=scope["year"])
            for scope in self.policy["scopes"]
        ])
        self.enterContext(patch.dict(os.environ, {KEY_ENV: self.key, runner.DISPOSABLE_ENV: "1"}))
        self.publish_evidence()
        self.enterContext(patch.object(runner.timezone, "now", side_effect=lambda: self.now))
        self.sleep = self.enterContext(patch.object(runner, "sleep", side_effect=self.advance))
        self.http = self.enterContext(patch("requests.Session.post", autospec=True, side_effect=self.mock_send))

    def publish_evidence(self):
        os.environ[EVIDENCE_ENV] = json.dumps(self.evidence)

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)

    def sign_fixture(self, scope, *, seconds=300, key=None):
        payload = {**scope, "expires_at": (self.now + timedelta(seconds=seconds)).isoformat()}
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
        tag = hmac.new((key or self.key).encode(), encoded, hashlib.sha256).hexdigest()
        return f"{encoded.decode()}.{tag}"

    def reviewed_scope(self, batch=1):
        report = runner.preflight(batch)
        self.assertEqual(report["blockers"], [])
        return report["authorization_scope"]

    def run_batch(self, batch):
        token = self.sign_fixture(self.reviewed_scope(batch))
        return runner.execute(batch, token, self.database)

    def mock_send(self, session, url, *, json, headers, timeout, allow_redirects):
        self.assertEqual(url, self.endpoint)
        self.assertFalse(session.trust_env)
        self.assertEqual(session.get_adapter(url).max_retries.total, 0)
        self.assertEqual(json["operationName"], "GetSearchTitles")
        self.assertEqual(json["variables"]["country"], "NL")
        self.assertEqual(json["variables"]["first"], 20)
        self.assertNotEqual(json["query"], SEASONS_QUERY)
        self.assertFalse(allow_redirects)
        self.assertEqual(timeout, 30)
        title_filter = json["variables"]["searchTitlesFilter"]
        scope = next(scope for scope in self.policy["scopes"] if scope["title"] == title_filter["searchQuery"])
        self.assertEqual(title_filter["objectTypes"], ["MOVIE" if scope["media_type"] == "movie" else "SHOW"])
        # An independent connection must see the debit and both events before transport.
        observer = connection.copy()
        try:
            with observer.cursor() as cursor:
                cursor.execute(
                    "SELECT b.attempts, b.in_progress, e.kind "
                    "FROM availability_availabilitystagedattemptevent e "
                    "JOIN availability_availabilityexperimentattempt a ON a.id = e.attempt_id "
                    "JOIN availability_availabilitystagedrun r ON r.experiment_id = a.experiment_id "
                    "JOIN availability_justwatchrequestbudget b ON b.id = r.grant_id "
                    "WHERE a.ordinal = %s AND a.scope_id = %s AND b.evidence_reference = %s "
                    "ORDER BY e.kind",
                    [self.http.call_count, scope["scope_id"], self.reference],
                )
                self.assertEqual(cursor.fetchall(), [
                    (self.http.call_count, True, "handoff"),
                    (self.http.call_count, True, "reservation"),
                ])
        finally:
            observer.close()
        self.advance(1)
        return self.response(scope)

    def response(self, scope=None, *, status=200, transform=None, headers=None):
        scope = scope or self.policy["scopes"][0]
        node = {
            "id": "synthetic-node",
            "objectType": "MOVIE" if scope["media_type"] == "movie" else "SHOW",
            "content": {"title": scope["title"], "originalReleaseYear": scope["year"],
                        "externalIds": {"tmdbId": str(scope["tmdb_id"])}, "fullPath": "/nl/synthetic"},
            "offers": [],
        }
        body = {"data": {"popularTitles": {"edges": [{"node": node}]}}}
        if transform:
            transform(body, node)
        response = requests.Response()
        response.status_code = status
        response.headers.update({"Content-Type": "application/json", **(headers or {})})
        response._content = json.dumps(body).encode()
        return response

    def test_read_only_command_reports_missing_receipt_and_creates_no_rows(self):
        self.source.configuration = {}
        self.source.save(update_fields=["configuration"])
        output = StringIO()
        call_command("issue97_stage_d", stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report["readiness"], "blocked")
        self.assertEqual(report["database_identity"], self.database)
        self.assertIn("runtime source receipt", report["blockers"][0])
        self.assertFalse(AvailabilityExperiment.objects.exists())
        self.assertFalse(JustWatchRequestBudget.objects.exists())
        self.http.assert_not_called()

    def test_exact_batch_and_pinned_policy_preserve_v1(self):
        self.assertEqual([2, 5, 5], self.policy["batch_sizes"])
        self.assertEqual([238, 1396], [scope["tmdb_id"] for scope in self.policy["scopes"][:2]])
        self.assertEqual({scope["country"] for scope in self.policy["scopes"]}, {"NL"})
        self.assertEqual({scope["maximum_attempts"] for scope in self.policy["scopes"]}, {1})
        self.assertNotIn(220289, {scope["tmdb_id"] for scope in self.policy["scopes"]})
        self.assertEqual(APPROVED_MANIFEST["maximum_attempts"], 6)
        self.assertEqual(len(APPROVED_MANIFEST["scopes"]), 2)
        self.assertEqual(self.reviewed_scope()["maximum_retries"], 0)
        with self.assertRaisesRegex(ExperimentBlocked, "exact title batch"):
            runner.preflight(4)

    def test_signed_scope_rejects_tampering_and_unauthorized_seasons(self):
        expected = self.reviewed_scope()
        for key, bad in (
            ("manifest_digest", "other"), ("database_identity", "other"),
            ("evidence_sha256", "other"), ("runtime_receipt_sha256", "other"),
            ("prior_checkpoints_sha256", "other"), ("scope_ids", ["movie-220289-nl"]),
            ("maximum_attempts", 13), ("maximum_retries", 1), ("operation", "GetTitleNode"),
        ):
            with self.subTest(key=key), self.assertRaisesRegex(ExperimentBlocked, "authorization does not match"):
                runner.execute(1, self.sign_fixture({**expected, key: bad}), self.database)
        for token in (self.sign_fixture(expected, key="unreviewed"), "invalid"):
            with self.assertRaisesRegex(ExperimentBlocked, "invalid Stage D authorization"):
                runner.execute(1, token, self.database)
        self.http.assert_not_called()
        self.assertFalse(AvailabilityExperimentAttempt.objects.exists())

    def test_runtime_guards_require_fingerprint_autocommit_and_expiry(self):
        expected = self.reviewed_scope()
        token = self.sign_fixture(expected)
        with self.assertRaisesRegex(ExperimentBlocked, "actual PostgreSQL target"):
            runner.execute(1, token, "wrong")
        with patch.dict(os.environ, {runner.DISPOSABLE_ENV: "0"}):
            with self.assertRaisesRegex(ExperimentBlocked, "disposable database"):
                runner.execute(1, token, self.database)
        with transaction.atomic(), self.assertRaisesRegex(ExperimentBlocked, "independently committed"):
            runner.execute(1, token, self.database)
        for seconds in (-1, 3601):
            with self.assertRaisesRegex(ExperimentBlocked, "expired or outlives"):
                runner.execute(1, self.sign_fixture(expected, seconds=seconds), self.database)
        self.assertFalse(AvailabilityExperimentAttempt.objects.exists())
        self.http.assert_not_called()

    def test_evidence_freshness_and_effective_quota(self):
        self.evidence["quota"]["remaining"] = 1
        self.publish_evidence()
        self.assertIn("insufficient balance", runner.preflight(1)["blockers"][0])
        self.evidence["quota"]["remaining"] = 12
        self.evidence["rate_policy"]["checked_at"] = (self.now - timedelta(hours=1)).isoformat()
        self.publish_evidence()
        self.assertIn("stale", runner.preflight(1)["blockers"][0])
        self.evidence["rate_policy"]["checked_at"] = self.now.isoformat()
        self.evidence["quota"]["spent_at_review"] = 1
        self.publish_evidence()
        self.assertIn("exceeds the shared receipt ledger", runner.preflight(1)["blockers"][0])
        self.http.assert_not_called()

    def test_reports_queue_and_active_source_lock(self):
        active = RefreshRun.objects.create()
        self.assertTrue(runner.try_source_lock("justwatch"))
        try:
            report = runner.preflight(1)
        finally:
            runner.release_source_lock("justwatch")
        self.assertTrue(report["locks"]["justwatch"])
        self.assertTrue(any("unfinished" in item for item in report["blockers"]))
        active.delete()
        self.http.assert_not_called()

    def test_twelve_committed_sends_and_no_thirteenth_reservation(self):
        # Upstream and receipt capacity remain after all 12 Stage D slots are spent.
        self.receipt["maximum_requests"] = 20
        self.source.configuration = {PERMISSION_KEY: self.receipt}
        self.source.save(update_fields=["configuration"])
        self.evidence["access_permission"]["maximum_requests"] = 20
        self.evidence["quota"]["remaining"] = 20
        self.publish_evidence()
        for batch, total in ((1, 2), (2, 7), (3, 12)):
            with self.subTest(batch=batch):
                result = self.run_batch(batch)
                self.assertEqual(result["http_reservations_spent"], total)
                self.assertEqual(result["http_client_handoffs"], total)
                self.assertEqual(result["http_transmitted_confirmed"], total)
                self.assertEqual(result["http_transmission_unknown"], 0)
        self.assertEqual(self.http.call_count, 12)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [5] * 11)
        self.assertEqual(AvailabilityExperiment.objects.count(), 1)
        self.assertEqual(AvailabilityStageDRun.objects.count(), 1)
        self.assertEqual(AvailabilityExperimentScope.objects.count(), 12)
        self.assertEqual(AvailabilityExperimentAttemptOutcome.objects.count(), 12)
        self.assertEqual(AvailabilityStageDAttemptEvent.objects.count(), 48)
        self.assertEqual(JustWatchRequestBudget.objects.get().attempts, 12)
        expected = self.reviewed_scope(3)
        token = self.sign_fixture(expected)
        with runner._run_locks(), self.assertRaisesRegex(ExperimentBlocked, "already spent its slot"):
            runner._reserve(self.policy, self.database, 3, self.policy["scopes"][-1],
                            expected["prior_checkpoints_sha256"], token)
        self.assertEqual(runner.execute(3, token, self.database)["http_reservations_spent"], 12)
        self.assertEqual(self.http.call_count, 12)
        self.assertFalse(Offer.objects.exists())

    def test_batch_order_and_resume_enforce_strictest_response_pacing(self):
        self.assertIn("previous title batch", runner.preflight(2)["blockers"][0])
        self.evidence["rate_policy"]["minimum_interval_seconds"] = 8
        self.publish_evidence()
        self.run_batch(1)
        second_batch = self.reviewed_scope(2)
        self.assertNotEqual(second_batch["prior_checkpoints_sha256"],
                            self.reviewed_scope(1)["prior_checkpoints_sha256"])
        token = self.sign_fixture(second_batch, seconds=7)
        with self.assertRaisesRegex(ExperimentBlocked, "pacing window"):
            runner.execute(2, token, self.database)
        self.assertEqual(self.http.call_count, 2)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [8])
        self.run_batch(2)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [8] * 6)

    def test_changed_evidence_during_pacing_blocks_next_reservation(self):
        token = self.sign_fixture(self.reviewed_scope())

        def rotate(seconds):
            self.advance(seconds)
            self.evidence["quota"]["window_id"] = "different-window"
            self.publish_evidence()

        self.sleep.side_effect = rotate
        with self.assertRaisesRegex(ExperimentBlocked, "authorization does not match"):
            runner.execute(1, token, self.database)
        self.assertEqual(self.http.call_count, 1)
        self.assertEqual(AvailabilityExperimentAttempt.objects.count(), 1)

    def test_uncertain_transport_is_spent_and_blocks_resumption(self):
        token = self.sign_fixture(self.reviewed_scope())
        self.http.side_effect = requests.Timeout
        with self.assertRaisesRegex(ExperimentBlocked, "send is unconfirmed"):
            runner.execute(1, token, self.database)
        report = runner.preflight(1)
        self.assertEqual(report["accounting"], {
            "http_reservations_spent": 1, "http_client_handoffs": 1,
            "http_transmitted_confirmed": 0, "http_transmission_unknown": 1,
            "http_handoff_without_confirmation": 1, "checkpointed_scope_ids": [],
        })
        self.assertTrue(JustWatchRequestBudget.objects.get().in_progress)
        with self.assertRaisesRegex(ExperimentBlocked, "unknown transmission"):
            runner.execute(1, token, self.database)
        with self.assertRaisesRegex(ExperimentBlocked, "unknown transmission"):
            runner.recover_checkpoint(self.database)
        self.assertEqual(self.http.call_count, 1)

    def test_expiry_after_reservation_distinguishes_no_handoff(self):
        token = self.sign_fixture(self.reviewed_scope(), seconds=1)
        reserve = runner._reserve

        def commit_then_expire(*args, **kwargs):
            result = reserve(*args, **kwargs)
            self.advance(2)
            return result

        with patch.object(runner, "_reserve", side_effect=commit_then_expire):
            with self.assertRaisesRegex(ExperimentBlocked, "expired"):
                runner.execute(1, token, self.database)
        account = runner.preflight(1)["accounting"]
        self.assertEqual(account["http_reservations_spent"], 1)
        self.assertEqual(account["http_client_handoffs"], 0)
        self.assertEqual(account["http_transmission_unknown"], 1)
        self.http.assert_not_called()

    def test_interrupted_handoff_stays_spent_after_reconnect(self):
        token = self.sign_fixture(self.reviewed_scope())
        self.http.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            runner.execute(1, token, self.database)
        connection.close()
        report = runner.preflight(1)
        self.assertEqual(report["accounting"]["http_reservations_spent"], 1)
        self.assertEqual(report["accounting"]["http_handoff_without_confirmation"], 1)
        self.assertTrue(JustWatchRequestBudget.objects.get().in_progress)
        self.assertIn("unknown transmission", report["blockers"][0])
        with self.assertRaisesRegex(ExperimentBlocked, "unknown transmission"):
            runner.execute(1, token, self.database)
        self.assertEqual(self.http.call_count, 1)

    def test_confirmed_response_can_be_checkpointed_offline_without_resend(self):
        token = self.sign_fixture(self.reviewed_scope())
        with patch.object(runner, "_checkpoint", side_effect=RuntimeError("interrupted checkpoint")):
            with self.assertRaisesRegex(RuntimeError, "interrupted checkpoint"):
                runner.execute(1, token, self.database)
        report = runner.preflight(1)
        self.assertEqual(report["accounting"]["http_transmitted_confirmed"], 1)
        self.assertIn("offline checkpoint recovery", report["blockers"][0])
        received = self.now
        self.advance(10)
        connection.close()
        output = StringIO()
        call_command("issue97_stage_d", recover_checkpoint=True,
                     confirm_database_identity=self.database, stdout=output)
        checkpoint = json.loads(output.getvalue())
        self.assertEqual(checkpoint["observed_at"], received.isoformat())
        self.assertEqual(AvailabilityStageDAttemptEvent.objects.get(kind="checkpoint").occurred_at, self.now)
        self.assertEqual(self.http.call_count, 1)
        result = runner.execute(1, token, self.database)
        self.assertEqual(result["http_reservations_spent"], 2)
        self.assertEqual(self.http.call_count, 2)

    def test_tampered_observation_cannot_be_recovered(self):
        token = self.sign_fixture(self.reviewed_scope())
        with patch.object(runner, "_checkpoint", side_effect=RuntimeError("interrupted checkpoint")):
            with self.assertRaises(RuntimeError):
                runner.execute(1, token, self.database)
        response = AvailabilityStageDAttemptEvent.objects.get(kind="response")
        response.evidence["observation"]["country"] = "US"
        with self.assertRaisesRegex(DatabaseError, "append-only"):
            with transaction.atomic():
                response.save(update_fields=["evidence"])
        with self.assertRaisesRegex(ExperimentBlocked, "no intact validated observation"):
            runner._checkpoint(response.attempt, response)
        self.assertFalse(AvailabilityExperimentScope.objects.exists())
        self.assertEqual(self.http.call_count, 1)

    def test_wrong_typed_response_stops_before_observation(self):
        self.assert_unmatched_title_stops(
            lambda body, node: node.update(objectType="SHOW"), "typed title is missing or ambiguous",
        )

    def test_wrong_tmdb_id_stops_before_observation(self):
        self.assert_unmatched_title_stops(
            lambda body, node: node["content"]["externalIds"].update(tmdbId="999999"),
            "typed title is missing or ambiguous",
        )

    def test_wrong_title_stops_before_observation(self):
        self.assert_unmatched_title_stops(
            lambda body, node: node["content"].update(title="Different title"), "label/year mismatch",
        )

    def test_wrong_year_stops_before_observation(self):
        self.assert_unmatched_title_stops(
            lambda body, node: node["content"].update(originalReleaseYear=1973), "label/year mismatch",
        )

    def assert_unmatched_title_stops(self, transform, expected_error):
        token = self.sign_fixture(self.reviewed_scope())
        self.http.side_effect = None
        self.http.return_value = self.response(transform=transform)
        with self.assertRaisesRegex(ExperimentBlocked, expected_error):
            runner.execute(1, token, self.database)
        response = AvailabilityStageDAttemptEvent.objects.get(kind="response")
        self.assertNotIn("observation", response.evidence)
        self.assertEqual(response.evidence["status"], 200)
        self.assertFalse(AvailabilityExperimentScope.objects.exists())
        self.assertEqual(self.http.call_count, 1)

    def test_stage_d_attempt_and_handoff_history_is_append_only(self):
        self.run_batch(1)
        attempts = AvailabilityExperimentAttempt.objects.filter(ordinal=1)
        handoffs = AvailabilityStageDAttemptEvent.objects.filter(attempt__ordinal=1, kind="handoff")
        with self.assertRaisesRegex(DatabaseError, "append-only"), transaction.atomic():
            attempts.update(scope_id="tampered")
        with self.assertRaisesRegex(DatabaseError, "append-only"), transaction.atomic():
            handoffs.delete()
        self.assertEqual(attempts.get().scope_id, self.policy["scopes"][0]["scope_id"])
        self.assertEqual(handoffs.count(), 1)
        self.assertEqual(self.http.call_count, 2)

    def test_wrong_market_stops_before_observation(self):
        token = self.sign_fixture(self.reviewed_scope())
        self.http.side_effect = None
        self.http.return_value = self.response(transform=lambda body, node: node["content"].update(fullPath="/us/title"))
        with self.assertRaisesRegex(ExperimentBlocked, "outside the NL market"):
            runner.execute(1, token, self.database)
        self.assertEqual(self.http.call_count, 1)
        self.assertFalse(AvailabilityExperimentScope.objects.exists())

    def test_observation_keeps_variants_and_mapping_gaps_separate_from_presence(self):
        token = self.sign_fixture(self.reviewed_scope())

        def include_variants(body, node):
            offer = {
                "monetizationType": "FLATRATE", "package": {"technicalName": "netflix"},
                "standardWebURL": "https://example.invalid/restricted-url",
            }
            node["offers"] = [
                {**offer, "presentationType": "HD"},
                {**offer, "presentationType": "UHD"},
                {"monetizationType": "OTHER", "package": {"technicalName": "unknown-example"}},
                {"audioLanguages": "nld"},
            ]

        self.http.side_effect = [
            self.response(transform=include_variants),
            self.response(self.policy["scopes"][1]),
        ]
        runner.execute(1, token, self.database)
        first = AvailabilityStageDAttemptEvent.objects.get(kind="response", attempt__ordinal=1)
        observation = first.evidence["observation"]
        self.assertEqual(observation["presence"], [{"provider": "netflix", "offer_class": "subscription"}])
        self.assertEqual([row["presentation"] for row in observation["offer_variants"]], ["HD", "UHD", None])
        self.assertEqual([item["ordinal"] for item in observation["unresolved"]], [3, 4])
        self.assertNotIn("example.invalid", json.dumps(first.evidence))
        self.assertFalse(Offer.objects.exists())

    def test_same_grant_renewal_resumes_without_minting_a_new_budget(self):
        expected = self.reviewed_scope()
        with self.assertRaisesRegex(ExperimentBlocked, "pacing window"):
            runner.execute(1, self.sign_fixture(expected, seconds=4), self.database)
        self.evidence["quota"].update(remaining=11, spent_at_review=1)
        self.publish_evidence()
        with self.assertRaisesRegex(ExperimentBlocked, "authorization does not match"):
            runner.execute(1, self.sign_fixture(expected), self.database)
        self.assertEqual(self.run_batch(1)["http_reservations_spent"], 2)
        self.assertEqual(AvailabilityStageDRun.objects.count(), 1)
        self.assertEqual(JustWatchRequestBudget.objects.get().attempts, 2)
        self.assertEqual(self.http.call_count, 2)

    def assert_charged_stop(self, status):
        token = self.sign_fixture(self.reviewed_scope())
        self.http.side_effect = None
        self.http.return_value = self.response(status=status, headers={"Retry-After": "901"})
        with self.assertRaisesRegex(ExperimentBlocked, f"HTTP {status}"):
            runner.execute(1, token, self.database)
        grant = JustWatchRequestBudget.objects.get()
        response = AvailabilityStageDAttemptEvent.objects.get(kind="response")
        self.assertFalse(self.http.call_args.kwargs["allow_redirects"])
        self.assertEqual(grant.attempts, 1)
        self.assertFalse(grant.in_progress)
        self.assertEqual(grant.denied, status in (401, 403, 302))
        self.assertEqual(response.evidence["status"], status)
        self.assertFalse(AvailabilityExperimentScope.objects.exists())
        with self.assertRaisesRegex(ExperimentBlocked, "confirmed response stopped"):
            runner.execute(1, token, self.database)
        self.assertEqual(self.http.call_count, 1)
        return grant, response

    def test_401_is_charged_and_stops(self):
        self.assert_charged_stop(401)

    def test_403_is_charged_and_stops(self):
        self.assert_charged_stop(403)

    def test_redirect_is_charged_and_stops(self):
        self.assert_charged_stop(302)

    def test_http_200_html_challenge_is_charged_and_stops(self):
        token = self.sign_fixture(self.reviewed_scope())
        challenge = self.response(headers={"Content-Type": "text/html"})
        challenge._content = b"<html><title>Synthetic access challenge</title></html>"
        self.http.side_effect = None
        self.http.return_value = challenge
        with self.assertRaisesRegex(ExperimentBlocked, "access challenge"):
            runner.execute(1, token, self.database)
        with self.assertRaisesRegex(ExperimentBlocked, "confirmed response stopped"):
            runner.execute(1, token, self.database)
        event = AvailabilityStageDAttemptEvent.objects.get(kind="response")
        self.assertEqual(event.evidence["status"], 200)
        self.assertNotIn("observation", event.evidence)
        self.assertNotIn("Synthetic access challenge", json.dumps(event.evidence))
        self.assertEqual(JustWatchRequestBudget.objects.get().attempts, 1)
        self.assertFalse(AvailabilityExperimentScope.objects.exists())
        self.assertEqual(self.http.call_count, 1)

    def test_429_durably_respects_retry_after(self):
        grant, response = self.assert_charged_stop(429)
        self.assertEqual(grant.blocked_until, self.now + timedelta(seconds=901))
        self.assertEqual(response.evidence["cooldown_until"], grant.blocked_until.isoformat())

    def test_503_is_charged_without_retry(self):
        self.assert_charged_stop(503)

    def test_shared_receipt_prior_spend_is_charged_to_same_budget(self):
        JustWatchRequestBudget.objects.create(
            evidence_reference=self.reference, attempts=1, last_reserved_at=self.now - timedelta(seconds=20),
        )
        self.evidence["quota"].update(remaining=11, spent_at_review=1)
        self.publish_evidence()
        self.assertIn("grant-idle timestamp", runner.preflight(1)["blockers"][0])
        self.evidence["prior_receipt_idle_since"] = self.now.isoformat()
        self.publish_evidence()
        self.http.side_effect = None
        scope = self.policy["scopes"][0]
        # The first title returns an exact response while the second stops at a denial.
        self.http.side_effect = [self.response(scope), self.response(status=403)]
        with self.assertRaisesRegex(ExperimentBlocked, "HTTP 403"):
            self.run_batch(1)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [5, 5])
        self.assertEqual(AvailabilityStageDRun.objects.get().initial_grant_attempts, 1)
        self.assertEqual(JustWatchRequestBudget.objects.get().attempts, 3)

    def test_ordinary_inflight_request_blocks_stage_across_receipt_versions(self):
        token = self.sign_fixture(self.reviewed_scope())
        JustWatchRequestBudget.objects.create(evidence_reference="older-receipt", in_progress=True, attempts=1)
        with self.assertRaisesRegex(ExperimentBlocked, "other receipt spend"):
            runner.execute(1, token, self.database)
        JustWatchRequestBudget.objects.filter(in_progress=True).update(evidence_reference=self.reference)
        with self.assertRaisesRegex(ExperimentBlocked, "attempt review"):
            runner.execute(1, token, self.database)
        self.http.assert_not_called()

    def test_stage_owns_budget_during_send_across_threads_and_receipt_versions(self):
        token = self.sign_fixture(self.reviewed_scope())
        started, finish = Event(), Event()
        errors = []

        def send(*args, **kwargs):
            started.set()
            if not finish.wait(5):
                raise AssertionError("test did not release the send")
            raise requests.Timeout()

        def stage_in_thread():
            try:
                runner.execute(1, token, self.database)
            except Exception as exc:
                errors.append(exc)
            finally:
                connection.close()

        self.http.side_effect = send
        thread = Thread(target=stage_in_thread)
        thread.start()
        try:
            self.assertTrue(started.wait(5))
            self.assertEqual(JustWatchRequestBudget.objects.get().attempts, 1)
            self.assertTrue(runner.preflight(1)["locks"]["title_drain"])
            with self.assertRaisesRegex(ExperimentBlocked, "title-drain lock"):
                runner.execute(1, token, self.database)
            for reference in (self.reference, "rotated-receipt"):
                self.source.configuration = {PERMISSION_KEY: {**self.receipt, "evidence_reference": reference}}
                self.source.save(update_fields=["configuration"])
                with self.assertRaisesRegex(SourceFetchStopped, "owns JustWatch"):
                    justwatch_transport.post(self.endpoint, operation="search", countries=["NL"],
                                             json={}, headers={}, timeout=1)
        finally:
            finish.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ExperimentBlocked)
        self.assertIn("unconfirmed", str(errors[0]))
        self.assertEqual(self.http.call_count, 1)
        self.assertEqual(JustWatchRequestBudget.objects.count(), 1)
