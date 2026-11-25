"""Stage D verification without creating a receipt, authorization or reservation."""

import hashlib
import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection, transaction
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from availability.services.experiment_runner import search_payload_for_scope
from availability.services.issue97_stage_d_policy import load_policy


class StageDDocumentTests(SimpleTestCase):
    def test_every_admitted_scope_builds_the_pinned_nl_search(self):
        policy = load_policy()
        self.assertEqual(policy["source"], "justwatch")
        self.assertEqual(policy["method"], "POST")
        self.assertEqual(policy["endpoint"], "https://apis.justwatch.com/graphql")
        for scope in policy["scopes"]:
            with self.subTest(scope=scope["scope_id"]):
                payload = search_payload_for_scope(scope)
                self.assertEqual(payload["operationName"], "GetSearchTitles")
                digest = hashlib.sha256(payload["query"].encode()).hexdigest()
                self.assertEqual(digest, policy["document_sha256"])
                variables = payload["variables"]
                self.assertEqual(variables["country"], "NL")
                self.assertEqual(variables["first"], 20)
                self.assertIsNone(variables["offset"])
                self.assertEqual(variables["searchTitlesFilter"]["searchQuery"], scope["title"])
                kind = {"movie": "MOVIE", "series": "SHOW"}[scope["media_type"]]
                self.assertEqual(variables["searchTitlesFilter"]["objectTypes"], [kind])


class StageDPreflightTests(TransactionTestCase):
    def test_default_command_blocks_without_receipt_in_read_only_transaction(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL enforces the read-only transaction")
        output = StringIO()
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            with CaptureQueriesContext(connection) as queries, patch("requests.Session.send") as send:
                call_command("issue97_stage_d", stdout=output)
        send.assert_not_called()
        self.assertTrue(queries.captured_queries)
        for query in queries.captured_queries:
            self.assertTrue(query["sql"].lstrip().upper().startswith("SELECT "))
        report = json.loads(output.getvalue())
        self.assertEqual(report["batch"], 1)
        self.assertEqual(report["readiness"], "blocked")
        self.assertIn("current runtime source receipt must cover NL search only", report["blockers"])
        self.assertNotIn("authorization_scope", report)
        self.assertEqual(report["accounting"]["http_reservations_spent"], 0)
        self.assertEqual(report["accounting"]["http_client_handoffs"], 0)
