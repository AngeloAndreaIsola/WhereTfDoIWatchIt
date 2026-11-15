"""
Tests for Google Interests import service and view endpoint.

The import flow has three layers:
1. parse_interests_page() / parse_collections_page() — pure HTML parsing
2. fetch_page() / fetch_list_pages() — URL routing, auth-wall detection,
   Google's own pageNumber pagination
3. google_interests_import view — end-to-end POST handler (mocked HTTP calls)
"""
from __future__ import annotations

import json
from unittest.mock import patch

from django.test import TestCase
from rest_framework import status

from tracker.models import Movie, WatchlistItem


def _empty_result():
    return type("R", (), {
        "resolved": [], "ambiguous": [], "skipped": [], "watchlist_count": 0,
    })()


# Card markup captured from a live Google TV / Interests saved list.  The old
# parser looked for <h3 class="...title..."> elements, which Google no longer
# renders — every card is an anchor with class="ir4l5" and an aria-label.
LIVE_CARD_HTML = """
<html><body>
<div class="C4h8Ec" jsname="Qm9wMc">
  <a href="https://www.google.com/search?q=Taxi+Driver&amp;stick=H4sIAAAAAAAA" aria-hidden="true" tabindex="-1" target="_blank">
    <img src="https://encrypted-tbn1.gstatic.com/images?q=tbn:ANd9Gc" alt="" class="YiUotd">
  </a>
</div>
<div class="C4h8Ec" jsname="Qm9wMc">
  <a href="https://www.google.com/search?q=Taxi+Driver&amp;stick=H4sIAAAAAAAA" class="ir4l5" aria-label="Taxi Driver" target="_blank">
    <div class="VeoBtc"><span jsname="r4nke">Taxi Driver</span></div>
  </a>
</div>
<div class="C4h8Ec" jsname="Qm9wMc">
  <a href="https://www.google.com/search?q=Deadpool+%26+Wolverine&amp;stick=H4sIB" class="ir4l5" aria-label="Deadpool &amp; Wolverine" target="_blank">
    <div class="VeoBtc"><span jsname="r4nke">Deadpool &amp; Wolverine</span></div>
  </a>
</div>
<div class="C4h8Ec" jsname="Qm9wMc">
  <a href="https://www.google.com/search?q=L%27aereo+pi%C3%B9+pazzo+del+mondo&amp;stick=H4sIC" class="ir4l5" aria-label="L&#39;aereo pi&#249; pazzo del mondo" target="_blank">
    <div class="VeoBtc"><span jsname="r4nke">L'aereo pi&#249; pazzo del mondo</span></div>
  </a>
</div>
<div class="C4h8Ec" jsname="Qm9wMc">
  <a href="https://www.google.com/search?q=Some+Movie%0A%0A1996&amp;stick=H4sID" class="ir4l5" aria-label="Some Movie" target="_blank">
    <div class="VeoBtc"><span jsname="r4nke">Some Movie</span></div>
  </a>
</div>
<div class="C4h8Ec" jsname="Qm9wMc">
  <a href="https://www.google.com/search?q=Blade+Runner+2049&amp;stick=H4sIE" class="ir4l5" aria-label="Blade Runner 2049" target="_blank">
    <div class="VeoBtc"><span jsname="r4nke">Blade Runner 2049</span></div>
  </a>
</div>
<div class="C4h8Ec" jsname="Qm9wMc">
  <a href="https://www.google.com/search?q&amp;stick=H4sIF" class="ir4l5" aria-label="https://www.google.com/search?q&amp;stick=H4sIF" target="_blank">
    <div class="VeoBtc"><span jsname="r4nke"></span></div>
  </a>
</div>
</body></html>
"""


class UrlKindTests(TestCase):
    """url_kind() must classify by path — the full base URL never matches it."""

    def test_interests_url_detected(self):
        from tracker.services.google_interests import url_kind

        url = (
            "https://www.google.com/interests/saved/list/"
            "rC-mUhfHjioaxQDGr7h4ejG_2jmJOg?q=Lista%20di%20titoli"
        )
        self.assertEqual(url_kind(url), "interests")

    def test_interests_url_with_page_number_detected(self):
        from tracker.services.google_interests import url_kind

        url = (
            "https://www.google.com/interests/saved/list/"
            "VCKbqG1z408FuigOgZNM0qVzoM5P4g?q=Cerca&pageNumber=1"
        )
        self.assertEqual(url_kind(url), "interests")

    def test_collections_url_detected(self):
        from tracker.services.google_interests import url_kind

        self.assertEqual(
            url_kind("https://www.google.com/collections/s/list/abc/def"),
            "collections",
        )

    def test_unknown_url_detected(self):
        from tracker.services.google_interests import url_kind

        self.assertEqual(url_kind("https://example.com/list"), "unknown")


class ParseCardMarkupTests(TestCase):
    """Parsing against the card markup Google actually serves today."""

    def _rows(self):
        from tracker.services.google_interests import parse_interests_page

        return parse_interests_page(LIVE_CARD_HTML, "https://www.google.com/interests/saved/list/x")

    def test_card_titles_extracted(self):
        titles = [r.title for r in self._rows()]
        self.assertIn("Taxi Driver", titles)
        # The aria-hidden image link must not produce a duplicate row.
        self.assertEqual(titles.count("Taxi Driver"), 1)

    def test_html_entities_unescaped(self):
        """Unescaped entities used to be sent to TMDb as literal text."""
        by_title = {r.title: r for r in self._rows()}
        self.assertIn("Deadpool & Wolverine", by_title)
        self.assertIn("L'aereo più pazzo del mondo", by_title)
        self.assertNotIn("Deadpool &amp; Wolverine", by_title)

    def test_year_from_q_parameter(self):
        by_title = {r.title: r for r in self._rows()}
        self.assertEqual(by_title["Some Movie"].year, 1996)

    def test_title_year_never_mistaken_for_release_year(self):
        by_title = {r.title: r for r in self._rows()}
        self.assertIsNone(by_title["Blade Runner 2049"].year)

    def test_placeholder_card_dropped(self):
        """Cards Google could not resolve carry their own URL as the label."""
        titles = [r.title for r in self._rows()]
        self.assertFalse(any(t.startswith("http") for t in titles))

    def test_interests_source_label(self):
        for row in self._rows():
            self.assertEqual(row.source, "google_interests")


class FetchPageTests(TestCase):
    """URL routing, cookie handling and sign-in wall detection."""

    def test_interests_url_without_cookies_raises(self):
        from tracker.services.google_interests import GoogleAuthRequiredError, fetch_page

        with self.assertRaises(GoogleAuthRequiredError):
            fetch_page(
                "https://www.google.com/interests/saved/list/abc?q=x",
                cookies=None,
            )

    def test_interests_url_routes_to_authenticated_fetch(self):
        from tracker.services import google_interests as gi

        with patch.object(gi, "fetch_interests_page", return_value="<html></html>") as m:
            html = gi.fetch_page(
                "https://www.google.com/interests/saved/list/abc?q=x",
                cookies={"NID": "secret"},
            )
        self.assertEqual(html, "<html></html>")
        m.assert_called_once()
        self.assertEqual(m.call_args.kwargs["cookies"], {"NID": "secret"})

    def test_collections_url_routes_to_public_fetch(self):
        from tracker.services import google_interests as gi

        with patch.object(gi, "fetch_collections_page", return_value="<html></html>") as m:
            gi.fetch_page("https://www.google.com/collections/s/list/abc/def", cookies=None)
        m.assert_called_once()

    def test_sign_in_redirect_raises_auth_error(self):
        """A 200 sign-in page used to parse to zero rows with no error at all."""
        from tracker.services import google_interests as gi

        resp = type("Resp", (), {
            "url": "https://accounts.google.com/v3/signin/identifier",
            "text": "<!doctype html><base href='https://accounts.google.com/v3/signin/'>",
            "raise_for_status": lambda self: None,
        })()
        session = type("Sess", (), {"get": lambda self, *a, **k: resp})()

        with patch.object(gi, "_get_session", return_value=session):
            with self.assertRaises(gi.GoogleAuthRequiredError):
                gi.fetch_interests_page(
                    "https://www.google.com/interests/saved/list/abc",
                    cookies={"NID": "stale"},
                )

    def test_help_page_bounce_raises_auth_error(self):
        """Bad cookies end on a support article, not on a sign-in page.

        Google routes the request through accounts.google.com/ServiceLogin and
        lands on support.google.com/accounts/answer/32050, which used to be
        parsed as a (nearly empty) list page and reported as no matches.
        """
        from tracker.services import google_interests as gi

        resp = type("Resp", (), {
            "url": "https://support.google.com/accounts/answer/32050?hl=en",
            "text": "<!doctype html><html><title>Clear cache &amp; cookies - "
                    "Computer - Google Account Help</title></html>",
            "raise_for_status": lambda self: None,
        })()
        session = type("Sess", (), {"get": lambda self, *a, **k: resp})()

        with patch.object(gi, "_get_session", return_value=session):
            with self.assertRaises(gi.GoogleAuthRequiredError):
                gi.fetch_interests_page(
                    "https://www.google.com/interests/saved/list/abc",
                    cookies={"NID": "wrong-domain"},
                )

    def test_redirect_to_foreign_host_raises_auth_error(self):
        from tracker.services import google_interests as gi

        resp = type("Resp", (), {
            "url": "https://example.org/somewhere/list",
            "text": "<html><body>not the list</body></html>",
            "raise_for_status": lambda self: None,
        })()
        session = type("Sess", (), {"get": lambda self, *a, **k: resp})()

        with patch.object(gi, "_get_session", return_value=session):
            with self.assertRaises(gi.GoogleAuthRequiredError):
                gi.fetch_interests_page(
                    "https://www.google.com/interests/saved/list/abc",
                    cookies={"NID": "x"},
                )

    def test_valid_list_page_is_returned(self):
        from tracker.services import google_interests as gi

        resp = type("Resp", (), {
            "url": "https://www.google.com/interests/saved/list/abc?q=x",
            "text": "<html><body>list</body></html>",
            "raise_for_status": lambda self: None,
        })()
        session = type("Sess", (), {"get": lambda self, *a, **k: resp})()

        with patch.object(gi, "_get_session", return_value=session):
            html = gi.fetch_interests_page(
                "https://www.google.com/interests/saved/list/abc",
                cookies={"NID": "valid"},
            )
        self.assertIn("list", html)

    def test_cookies_are_not_kept_on_the_shared_session(self):
        import requests

        from tracker.services import google_interests as gi

        resp = type("Resp", (), {
            "url": "https://www.google.com/interests/saved/list/abc",
            "text": "<html><body>list</body></html>",
            "raise_for_status": lambda self: None,
        })()
        session = requests.Session()

        with patch.object(gi, "_get_session", return_value=session):
            with patch.object(session, "get", return_value=resp) as get:
                gi.fetch_interests_page(
                    "https://www.google.com/interests/saved/list/abc",
                    cookies={"NID": "secret"},
                )

        # Per-request cookies only: the shared session must not retain them.
        self.assertEqual(len(session.cookies), 0)
        self.assertEqual(get.call_args.kwargs["cookies"], {"NID": "secret"})


class PaginationTests(TestCase):
    """Google paginates long lists; the importer must follow every page."""

    def test_page_number_replaced_and_query_preserved(self):
        from tracker.services.google_interests import _with_page_number

        url = "https://www.google.com/interests/saved/list/abc?q=Lista&pageNumber=1"
        out = _with_page_number(url, 3)
        self.assertIn("pageNumber=3", out)
        self.assertIn("q=Lista", out)
        self.assertIn("/interests/saved/list/abc", out)

    def test_counter_parsed(self):
        from tracker.services.google_interests import _page_counter

        self.assertEqual(_page_counter("<span class='Azx0Fe'>1-200 of 256</span>"), (1, 200, 256))
        self.assertIsNone(_page_counter("<html></html>"))

    def test_fetch_list_pages_follows_remaining_pages(self):
        from tracker.services import google_interests as gi

        page1 = "<body><span>1-200 of 256</span></body>"
        page2 = "<body><span>201-256 of 256</span></body>"
        urls = []

        def fake_fetch(url, cookies=None, timeout=20):
            urls.append(url)
            return page1 if len(urls) == 1 else page2

        with patch.object(gi, "fetch_page", side_effect=fake_fetch):
            pages = gi.fetch_list_pages("https://www.google.com/interests/saved/list/abc?q=x")

        self.assertEqual(len(pages), 2)
        self.assertIn("pageNumber=2", urls[1])

    def test_fetch_list_pages_stops_when_list_is_complete(self):
        from tracker.services import google_interests as gi

        with patch.object(gi, "fetch_page", return_value="<span>1-35 of 35</span>") as m:
            pages = gi.fetch_list_pages("https://www.google.com/interests/saved/list/abc?q=x")
        self.assertEqual(len(pages), 1)
        self.assertEqual(m.call_count, 1)

    def test_fetch_list_pages_stops_on_repeated_page(self):
        """Google clamps out-of-range pageNumber values to the last page."""
        from tracker.services import google_interests as gi

        body = "<span>1-100 of 200</span>"
        with patch.object(gi, "fetch_page", side_effect=[body, body, body]) as m:
            pages = gi.fetch_list_pages("https://www.google.com/interests/saved/list/abc?q=x")
        self.assertEqual(len(pages), 1)
        self.assertEqual(m.call_count, 2)


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


class ImportMatchingTests(TestCase):
    """TMDb matching: Google TV lists mix films and series."""

    def test_search_covers_movies_and_series(self):
        from tracker.services.google_interests import GoogleInterestRow, import_interests

        calls = []

        class FakeClient:
            def search(self, query, year=None, content_type=None):
                calls.append((query, year, content_type))
                if query == "Masters of the Air":
                    return [type("M", (), {
                        "tmdb_id": 46518, "title": "Masters of the Air", "year": 2024,
                        "poster_path": "/x.jpg", "overview": "", "content_type": "series",
                    })()]
                return []

        result = import_interests(
            [GoogleInterestRow(title="Masters of the Air", source="google_interests")],
            client=FakeClient(),
        )

        self.assertEqual(calls[0][2], "all")
        self.assertEqual(len(result.resolved), 1)
        self.assertEqual(result.watchlist_count, 1)

    def test_duplicate_rows_searched_once(self):
        from tracker.services.google_interests import GoogleInterestRow, import_interests

        calls = []

        class FakeClient:
            def search(self, query, year=None, content_type=None):
                calls.append(query)
                return []

        rows = [
            GoogleInterestRow(title="Taxi Driver", source="google_interests"),
            GoogleInterestRow(title="Taxi Driver", source="google_interests"),
        ]
        import_interests(rows, client=FakeClient())
        self.assertEqual(calls, ["Taxi Driver"])

    def test_no_match_lands_in_skipped(self):
        from tracker.services.google_interests import GoogleInterestRow, import_interests

        class FakeClient:
            def search(self, query, year=None, content_type=None):
                return []

        result = import_interests(
            [GoogleInterestRow(title="SCP: Overlord", source="google_interests")],
            client=FakeClient(),
        )
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(result.skipped[0]["reason"], "no_match")


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
            with patch.object(gi_mod, "import_interests", return_value=_empty_result()):
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
            with patch.object(gi_mod, "import_interests", return_value=_empty_result()):
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
            with patch.object(gi_mod, "import_interests", return_value=_empty_result()):
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

    def test_missing_cookies_reported_as_skipped_not_silent(self):
        """An Interests URL without cookies must surface an error, not 0/0/0."""
        resp = self.client.post(
            self.url,
            data=json.dumps({
                "urls": [
                    "https://www.google.com/interests/saved/list/abc?q=Lista",
                    "https://www.google.com/interests/saved/list/def?q=Cerca&pageNumber=1",
                ],
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["skipped"]), 2)
        for item in resp.data["skipped"]:
            self.assertIn("fetch_failed", item["reason"])
            self.assertIn("cookies", item["reason"])

    def test_parsed_rows_are_imported(self):
        """Cards from a real Google page flow through parse + import."""
        from tracker.services import google_interests as gi_mod

        captured = {}

        def fake_import(rows, client=None, user_id=1):
            captured["rows"] = list(rows)
            return _empty_result()

        with patch.object(gi_mod, "fetch_page", return_value=LIVE_CARD_HTML):
            with patch.object(gi_mod, "import_interests", side_effect=fake_import):
                resp = self.client.post(
                    self.url,
                    data=json.dumps({
                        "cookies": {"NID": "x"},
                        "urls": ["https://www.google.com/interests/saved/list/abc?q=x"],
                    }),
                    content_type="application/json",
                )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        titles = [r.title for r in captured["rows"]]
        self.assertIn("Taxi Driver", titles)
        self.assertIn("Deadpool & Wolverine", titles)

    def test_empty_page_yields_skipped_when_nothing_parsed(self):
        """A page that returns HTML but contains no list items is noted."""
        from tracker.services import google_interests as gi_mod

        with patch.object(gi_mod, "fetch_page", return_value="<html></html>"):
            with patch.object(gi_mod, "import_interests", return_value=_empty_result()):
                resp = self.client.post(
                    self.url,
                    data=json.dumps({
                        "urls": ["https://google.com/collections/s/list/abc/def"],
                    }),
                    content_type="application/json",
                )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["watchlist_count"], 0)
        self.assertEqual(len(resp.data["skipped"]), 1)
        self.assertIn("no_items_found", resp.data["skipped"][0]["reason"])
        self.assertEqual(
            resp.data["skipped"][0]["url"],
            "https://google.com/collections/s/list/abc/def",
        )
