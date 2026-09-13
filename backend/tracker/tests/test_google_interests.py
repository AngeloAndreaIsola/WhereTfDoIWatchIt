"""
Tests for Google Interests import service and view endpoint.

The import flow has two layers:
1. parse_interests_page() / parse_collections_page() — pure HTML parsing
2. google_interests_import view — end-to-end POST handler (mocked HTTP calls)
"""
from __future__ import annotations

import json
from unittest.mock import patch

from django.test import TestCase
from rest_framework import status

from tracker.models import Movie, WatchlistItem


class ParseInterestsPageTests(TestCase):
    """Unit tests for parse_interests_page()."""

    def test_basic_h3_titles_extracted(self):
        from tracker.services.google_interests import parse_interests_page

        html = """
        <html><body>
        <h3><a href="/something">The Matrix</a></h3>
        <h3><a href="/other">Inception (2010)</a></h3>
        </body></html>
        """
        rows = parse_interests_page(html, "https://example.com/list")
        titles = {r.title for r in rows}
        self.assertIn("The Matrix", titles)
        self.assertIn("Inception (2010)", titles)

        matrix = next(r for r in rows if r.title == "The Matrix")
        inception = next(r for r in rows if r.title == "Inception (2010)")
        self.assertIsNone(matrix.year)
        self.assertEqual(inception.year, 2010)

    def test_empty_html_returns_nothing(self):
        from tracker.services.google_interests import parse_interests_page

        rows = parse_interests_page("<html><body></body></html>", "https://example.com/list")
        self.assertEqual(rows, [])

    def test_fallback_scan_on_missing_class(self):
        from tracker.services.google_interests import parse_interests_page

        html = """
        <html><body>
        <div>
          <h3><a href="/tt123">Interstellar</a></h3>
        </div>
        </body></html>
        """
        rows = parse_interests_page(html, "https://example.com/list")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "Interstellar")


class CollectionsPageParsingTests(TestCase):
    """Unit tests for parse_collections_page()."""

    def test_basic_collection_items_extracted(self):
        from tracker.services.google_interests import parse_collections_page

        # Real Google HTML: href ends with &amp;stick=..., aria-label holds the title
        html = """
        <html><body>
        <a href="https://www.google.com/search?q=The+Matrix%0A%0A1999&amp;stick=abc" class="ir4l5" aria-label="The Matrix" target="_blank">
          <div class="VeoBtc"><span jsname="r4nke">The Matrix</span></div>
        </a>
        <a href="https://www.google.com/search?q=Inception%0A%0A2010&amp;stick=def" class="ir4l5" aria-label="Inception" target="_blank">
          <div class="VeoBtc"><span jsname="r4nke">Inception</span></div>
        </a>
        <a href="https://www.google.com/search?q=Interstellar&amp;stick=ghi" class="ir4l5" aria-label="Interstellar" target="_blank">
          <div class="VeoBtc"><span jsname="r4nke">Interstellar</span></div>
        </a>
        </body></html>
        """
        rows = parse_collections_page(html, "https://www.google.com/collections/s/list/abc/def")
        self.assertEqual(len(rows), 3)
        by_title = {r.title: r for r in rows}
        self.assertIn("The Matrix", by_title)
        self.assertIn("Inception", by_title)
        self.assertIn("Interstellar", by_title)
        # Years embedded in the q= param (URL-encoded newlines) are extracted
        self.assertEqual(by_title["The Matrix"].year, 1999)
        self.assertEqual(by_title["Inception"].year, 2010)
        self.assertIsNone(by_title["Interstellar"].year)

    def test_realistic_google_html_structure(self):
        """Test against HTML that matches the structure found on real Google pages."""
        from tracker.services.google_interests import parse_collections_page

        html = """
        <html><body>
        <div class="C4h8Ec" jsname="Qm9wMc">
          <a href="https://www.google.com/search?q=Comrades:+Almost+a+Love+Story&amp;stick=H4sIAAAAAAAAAONgVuLUz9U3SC4qKMkDAHQRKYENAAAA"
             class="ir4l5" aria-label="Comrades: Almost a Love Story" target="_blank">
            <div class="VeoBtc"><span jsname="r4nke">Comrades: Almost a Love Story</span></div>
          </a>
          <div class="f5syy"><span jslog="...">...</span></div>
        </div>
        <div class="C4h8Ec" jsname="Qm9wMc">
          <a href="https://www.google.com/search?q=The+Shivering+Truth&amp;stick=H4sIAAAAAAAAAONgVuLVT9c3NEyvNDIpKTfNAgAQLy32EQAAAA"
             class="ir4l5" aria-label="The Shivering Truth" target="_blank">
            <div class="VeoBtc"><span jsname="r4nke">The Shivering Truth</span></div>
          </a>
          <div class="f5syy"><span jslog="...">...</span></div>
        </div>
        </body></html>
        """
        rows = parse_collections_page(html, "https://www.google.com/collections/s/list/abc/def")
        self.assertEqual(len(rows), 2)
        titles = {r.title for r in rows}
        self.assertIn("Comrades: Almost a Love Story", titles)
        self.assertIn("The Shivering Truth", titles)
        # Real Google pages don't embed years in the static HTML for most items,
        # so both should have year=None.
        for r in rows:
            self.assertIsNone(r.year)

    def test_year_from_aria_label(self):
        """When the year IS in the aria-label, extract it."""
        from tracker.services.google_interests import parse_collections_page

        html = """
        <html><body>
        <a href="https://www.google.com/search?q=Some+Movie&amp;stick=abc" class="ir4l5" aria-label="Some Movie 1996" target="_blank">
          <div class="VeoBtc"><span jsname="r4nke">Some Movie 1996</span></div>
        </a>
        </body></html>
        """
        rows = parse_collections_page(html, "https://www.google.com/collections/s/list/abc/def")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "Some Movie 1996")
        self.assertEqual(rows[0].year, 1996)

    def test_junk_entries_skipped(self):
        from tracker.services.google_interests import parse_collections_page

        html = """
        <html><body>
        <a href="https://www.google.com/search?q=&amp;stick=abc" class="ir4l5" aria-label="" target="_blank"></a>
        <a href="https://www.google.com/search?q=The+Matrix&amp;stick=def" class="ir4l5" aria-label="The Matrix" target="_blank">
          <div class="VeoBtc"><span jsname="r4nke">The Matrix</span></div>
        </a>
        </body></html>
        """
        rows = parse_collections_page(html, "https://www.google.com/collections/s/list/abc/def")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "The Matrix")

    def test_url_encoded_plus_signs_decoded(self):
        from tracker.services.google_interests import parse_collections_page

        html = """
        <html><body>
        <a href="https://www.google.com/search?q=Fantastic+Misterou+Film&amp;stick=abc" class="ir4l5" aria-label="Fantastic Mistero Film" target="_blank">
          <div class="VeoBtc"><span jsname="r4nke">Fantastic Mistero Film</span></div>
        </a>
        </body></html>
        """
        rows = parse_collections_page(html, "https://www.google.com/collections/s/list/abc/def")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "Fantastic Mistero Film")


class GoogleInterestsImportViewTests(TestCase):
    """End-to-end tests for POST /api/importers/google-interests/."""

    url = "/api/importers/google-interests/"

    def test_missing_urls_returns_400(self):
        resp = self.client.post(
            self.url,
            data=json.dumps({"cookies": {"CONSENT": "nr"}}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("urls", resp.data["error"].lower())

    def test_empty_cookies_accepted_for_public_urls(self):
        """Public collections URLs should work without cookies."""
        from tracker.services import google_interests as gi_mod

        with patch.object(gi_mod, "fetch_page", return_value="<html></html>"):
            with patch.object(gi_mod, "parse_and_import", return_value=type("R", (), {
                "resolved": [], "ambiguous": [], "skipped": [], "watchlist_count": 0,
            })()):
                resp = self.client.post(
                    self.url,
                    data=json.dumps({
                        "urls": ["https://www.google.com/collections/s/list/abc/def"],
                    }),
                    content_type="application/json",
                )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_cookies_optional_key_not_required(self):
        """Omitting the cookies key entirely should be accepted."""
        from tracker.services import google_interests as gi_mod

        with patch.object(gi_mod, "fetch_page", return_value="<html></html>"):
            with patch.object(gi_mod, "parse_and_import", return_value=type("R", (), {
                "resolved": [], "ambiguous": [], "skipped": [], "watchlist_count": 0,
            })()):
                resp = self.client.post(
                    self.url,
                    data=json.dumps({"urls": ["https://www.google.com/collections/s/list/abc/def"]}),
                    content_type="application/json",
                )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_non_dict_cookies_returns_400(self):
        resp = self.client.post(
            self.url,
            data=json.dumps({"cookies": ["not", "an", "object"], "urls": ["https://example.com"]}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cookies", resp.data["error"].lower())

    def test_fetch_failure_is_captured_in_skipped(self):
        """When Google returns an error, the URL is reported as skipped."""
        from tracker.services import google_interests as gi_mod

        with patch.object(gi_mod, "fetch_page", side_effect=Exception("connection refused")):
            with patch.object(gi_mod, "parse_and_import", return_value=type("R", (), {
                "resolved": [], "ambiguous": [], "skipped": [], "watchlist_count": 0,
            })()):
                resp = self.client.post(
                    self.url,
                    data=json.dumps({
                        "cookies": {"CONSENT": "nr", "NID": "abc"},
                        "urls": ["https://google.com/interests/saved/list/abc"],
                    }),
                    content_type="application/json",
                )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["watchlist_count"], 0)
        self.assertEqual(len(resp.data["resolved"]), 0)
        self.assertTrue(any("fetch_failed" in item.get("reason", "") for item in resp.data["skipped"]))

    def test_empty_page_yields_skipped_when_nothing_parsed(self):
        """A page that returns HTML but contains no list items is noted."""
        from tracker.services import google_interests as gi_mod

        with patch.object(gi_mod, "fetch_page", return_value="<html></html>"):
            with patch.object(gi_mod, "parse_and_import", return_value=type("R", (), {
                "resolved": [], "ambiguous": [], "skipped": [], "watchlist_count": 0,
            })()):
                resp = self.client.post(
                    self.url,
                    data=json.dumps({
                        "urls": ["https://google.com/collections/s/list/abc/def"],
                    }),
                    content_type="application/json",
                )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["watchlist_count"], 0)
