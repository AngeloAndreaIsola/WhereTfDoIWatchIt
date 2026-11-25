from unittest.mock import Mock, patch

import requests
from django.test import TransactionTestCase

from availability.models import JustWatchRequestBudget, Source
from availability.services.source_permissions import PERMISSION_KEY
from availability.sources.base import SourceFetchStopped, SourceQuotaExceeded
from availability.sources.justwatch_client import JustWatchClient
from tracker.tests.availability_factories import reviewed_test_sources


class ReviewedJustWatchTransportTests(TransactionTestCase):
    def setUp(self):
        reviewed_test_sources(slugs=["justwatch"])
        self.send = self.enterContext(patch("requests.Session.post"))
        self.send.return_value = Mock(
            status_code=200, headers={"Content-Type": "application/json"},
            json=Mock(return_value={"data": {"popularTitles": {"edges": []}}}),
        )
        self.client_api = JustWatchClient()

    def search(self, country="NL"):
        return self.client_api.search_titles("Film", country=country)

    def test_enabled_is_not_authorization_and_reviewed_country_is_exact(self):
        with self.assertRaises(SourceFetchStopped):
            self.search("FR")
        Source.objects.filter(slug="justwatch").update(configuration={})
        with self.assertRaises(SourceFetchStopped):
            self.search()
        self.send.assert_not_called()
        self.assertFalse(JustWatchRequestBudget.objects.exists())

    def test_budget_is_reserved_before_send_and_reused_across_clients(self):
        source = Source.objects.get(slug="justwatch")
        source.configuration[PERMISSION_KEY]["maximum_requests"] = 1
        source.save(update_fields=["configuration"])
        response = self.send.return_value

        def assert_reserved(*args, **kwargs):
            self.assertTrue(JustWatchRequestBudget.objects.get().in_progress)
            self.assertEqual(JustWatchRequestBudget.objects.get().attempts, 1)
            self.assertFalse(kwargs["allow_redirects"])
            return response

        self.send.side_effect = assert_reserved
        self.search()
        with self.assertRaises(SourceQuotaExceeded):
            JustWatchClient().search_titles("Film", country="NL")
        self.send.assert_called_once()

    def test_403_and_429_stop_without_retries(self):
        for status, error in ((403, SourceFetchStopped), (429, SourceQuotaExceeded)):
            with self.subTest(status=status):
                self.send.return_value.status_code = status
                with self.assertRaises(error):
                    self.search()
                with self.assertRaises(error):
                    self.search()
                self.send.assert_called_once()
                self.assertEqual(JustWatchRequestBudget.objects.get().attempts, 1)
                JustWatchRequestBudget.objects.all().delete()
                self.send.reset_mock()

    def test_timeout_or_interruption_requires_reconciliation(self):
        for interruption, error in (
            (requests.Timeout("fixture timeout"), SourceFetchStopped),
            (KeyboardInterrupt(), KeyboardInterrupt),
        ):
            with self.subTest(error=error):
                self.send.side_effect = interruption
                with self.assertRaises(error):
                    self.search()
                with self.assertRaises(SourceFetchStopped):
                    self.search()
                self.send.assert_called_once()
                self.assertTrue(JustWatchRequestBudget.objects.get().in_progress)
                JustWatchRequestBudget.objects.all().delete()
                self.send.reset_mock()
