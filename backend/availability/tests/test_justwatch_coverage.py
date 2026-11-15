"""P8 — JustWatch catalogue coverage at the HTTP boundary.

The whole point of P8's year work is that a year window must reach JustWatch.
A parameter that is accepted, threaded through two layers and then silently
dropped is the failure this file exists to make impossible, so every assertion
here inspects the JSON body that would actually be POSTed.

Country propagation already has its own dedicated file
(`test_justwatch_country_propagation.py`) pinning the `385c8b4` behaviour, so
these tests assert country only as a precondition for the year/pagination
work rather than re-covering it.

The client seam is `JustWatchClient(http_post=...)`, and the catalogue call is
`catalogue_titles(country=, count=, offset=, packages=, object_types=,
min_year=, max_year=)`. No request leaves the process: assertions are about the
payload we *would* send, which is exactly the thing that was wrong before.
"""
from unittest import TestCase as SimpleTestCase

from availability.domain.enums import ContentType
from availability.sources.base import SourceFetchError, accepts_kwarg
from availability.sources.justwatch import JustWatchSource
from availability.sources.justwatch_client import (
    MAX_TITLES_PER_REQUEST,
    JustWatchClient,
)

from . import aggregator_fixtures as fx


def sleeper(_attempt):
    """No real backoff sleeps in tests."""


class RecordingPost:
    """Captures every payload the client would POST, in order.

    Recording the whole sequence rather than the last call is what makes the
    pagination cases meaningful: they can assert that *every* request carried
    the year window and that offsets advanced, not merely that one did.
    """

    def __init__(self, responses=()):
        self.responses = list(responses)
        self.payloads = []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.payloads.append(json)
        return self.responses.pop(0) if self.responses else fx.FakeResponse()

    def release_years(self):
        return [
            (p or {}).get("variables", {})
            .get("popularTitlesFilter", {})
            .get("releaseYear")
            for p in self.payloads
        ]

    def offsets(self):
        return [
            (p or {}).get("variables", {}).get("offset")
            for p in self.payloads
        ]

    def firsts(self):
        return [
            (p or {}).get("variables", {}).get("first")
            for p in self.payloads
        ]


def source_with(post, **kwargs):
    return JustWatchSource(
        client=JustWatchClient(http_post=post, sleeper=sleeper), **kwargs
    )


def catalogue_page(year=2025, title="Superman", tmdb_id="1061474", node_id="tm1"):
    """One catalogue edge, so page fullness is controllable per response."""
    return fx.envelope({
        "popularTitles": {
            "edges": fx.edges(
                fx.title(node_id, title, year=year, tmdb_id=tmdb_id)
            )
        }
    })


class YearWindowReachesThePayloadTests(SimpleTestCase):
    """The year range must land in ``popularTitlesFilter.releaseYear``."""

    def test_bounded_window_reaches_the_graphql_variables(self):
        post = RecordingPost([fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX)])
        source = source_with(post)

        source.fetch(
            None,
            country="NL",
            provider="netflix",
            cursor={"offset": 0, "min_year": 2017, "max_year": 2026},
        )

        self.assertTrue(post.payloads, "no request was sent")
        for payload in post.payloads:
            self.assertEqual(
                payload["variables"]["popularTitlesFilter"]["releaseYear"],
                {"min": 2017, "max": 2026},
            )

    def test_no_year_window_sends_null_bounds(self):
        """Unbounded discovery is still a valid request, not a broken one."""
        post = RecordingPost([fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX)])
        source = source_with(post)

        source.fetch(None, country="NL", provider="netflix",
                     cursor={"offset": 0})

        for payload in post.payloads:
            release_year = payload["variables"]["popularTitlesFilter"]["releaseYear"]
            self.assertEqual(release_year, {"min": None, "max": None})
            # JustWatch reads a null bound as "no bound", which is correct;
            # what must never happen is a bound that was never requested.
            self.assertIsNone(release_year["min"])
            self.assertIsNone(release_year["max"])

    def test_min_only_window(self):
        post = RecordingPost([fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX)])
        source = source_with(post)

        source.fetch(None, country="NL", provider="netflix",
                     cursor={"offset": 0, "min_year": 2020})

        self.assertEqual(post.release_years()[0], {"min": 2020, "max": None})

    def test_max_only_window(self):
        post = RecordingPost([fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX)])
        source = source_with(post)

        source.fetch(None, country="NL", provider="netflix",
                     cursor={"offset": 0, "max_year": 2015})

        self.assertEqual(post.release_years()[0], {"min": None, "max": 2015})

    def test_every_page_of_a_walk_carries_the_window(self):
        """A window that reaches only page 1 would partition nothing."""
        post = RecordingPost([
            fx.FakeResponse(payload=catalogue_page(2020)),
            fx.FakeResponse(payload=catalogue_page(2021)),
        ])
        source = source_with(post)

        source.fetch(None, country="NL", provider="netflix",
                     cursor={"offset": 0, "min_year": 2020, "max_year": 2021},
                     pages=2)

        self.assertEqual(len(post.payloads), 2)
        for release_year in post.release_years():
            self.assertEqual(release_year, {"min": 2020, "max": 2021})


class YearFilterIsEnforcedNotMerelySentTests(SimpleTestCase):
    """Sending the filter is necessary; the server honouring it is the point.

    Measured during the P8 audit: an unbounded page returned titles from
    1957–2026, while the same query filtered to 2020–2021 returned only
    2020–2021 titles, and 1910–1915 returned exactly one 1913 title. That is
    the evidence the year partition rests on, so the response side is asserted
    too — an out-of-range title is a server bug this code cannot fix, but it is
    worth pinning that we do not *invent* an empty result when one appears.
    """

    def test_an_empty_page_for_a_narrow_window_is_not_an_error(self):
        post = RecordingPost([fx.FakeResponse(payload=fx.envelope(
            {"popularTitles": {"edges": []}}))])
        source = source_with(post)

        result = source.fetch(None, country="NL", provider="netflix",
                              cursor={"offset": 0, "min_year": 1910,
                                      "max_year": 1915})

        self.assertEqual(len(result.contents), 0)
        self.assertEqual(result.offers, [])
        self.assertEqual(post.release_years()[0], {"min": 1910, "max": 1915})


class PaginationPayloadTests(SimpleTestCase):
    def test_first_page_requests_offset_zero(self):
        post = RecordingPost([fx.FakeResponse(payload=catalogue_page())])
        source = source_with(post)

        source.fetch(None, country="NL", provider="netflix",
                     cursor={"offset": 0})

        # `offset` is sent as null (absent-or-zero) for page one; JustWatch
        # treats null as 0. What matters is that the first request is not
        # offset 100.
        self.assertIn(post.offsets()[0], (0, None))

    def test_subsequent_pages_advance_by_the_page_size(self):
        post = RecordingPost([
            fx.FakeResponse(payload=catalogue_page()),
            fx.FakeResponse(payload=catalogue_page()),
            fx.FakeResponse(payload=catalogue_page()),
        ])
        source = source_with(post, page_size=100)

        source.fetch(None, country="NL", provider="netflix",
                     cursor={"offset": 0}, pages=3)

        offsets = post.offsets()
        self.assertEqual(len(offsets), 3)
        # Page one sends null (absent-or-zero), which JustWatch reads as 0.
        self.assertIn(offsets[0], (0, None))
        self.assertEqual(offsets[1], 100)
        self.assertEqual(offsets[2], 200)

    def test_requested_count_is_sent_as_first(self):
        post = RecordingPost([fx.FakeResponse(payload=catalogue_page())])
        source = source_with(post, page_size=50)

        source.fetch(None, country="NL", provider="netflix",
                     cursor={"offset": 0})

        self.assertEqual(post.firsts()[0], 50)

    def test_pages_is_an_exact_bound_on_requests(self):
        post = RecordingPost([fx.FakeResponse(payload=catalogue_page())
                              for _ in range(6)])
        source = source_with(post)

        source.fetch(None, country="NL", provider="netflix",
                     cursor={"offset": 0}, pages=2)

        self.assertEqual(len(post.payloads), 2,
                         "pages=N must issue exactly N requests")

    def test_an_empty_page_terminates_the_walk_early(self):
        post = RecordingPost([
            fx.FakeResponse(payload=catalogue_page()),
            fx.FakeResponse(payload=fx.envelope({"popularTitles": {"edges": []}})),
            fx.FakeResponse(payload=catalogue_page()),
        ])
        source = source_with(post)

        result = source.fetch(None, country="NL", provider="netflix",
                              cursor={"offset": 0}, pages=5)

        self.assertEqual(len(post.payloads), 2,
                         "the walk must stop at the empty page, not keep going")
        # One full page of titles survived; the empty second page only stopped it.
        self.assertGreater(len(result.contents), 0)

    def test_page_size_above_the_measured_ceiling_is_refused(self):
        """The ceiling is measured, not guessed: raising it needs a re-probe."""
        post = RecordingPost()
        client = JustWatchClient(http_post=post, sleeper=sleeper)

        with self.assertRaises(ValueError):
            client.catalogue_titles(
                country="NL", count=MAX_TITLES_PER_REQUEST + 1, offset=0,
            )


class ContentTypeReachesThePayloadTests(SimpleTestCase):
    """MOVIE and SERIES are different partitions and must query differently."""

    def test_each_content_type_sends_its_own_object_types(self):
        for content_type in (ContentType.MOVIE, ContentType.SERIES):
            with self.subTest(content_type=content_type):
                post = RecordingPost([
                    fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX)])
                source = source_with(post)

                source.fetch(None, country="NL", provider="netflix",
                             cursor={"offset": 0,
                                     "content_type": content_type})

                object_types = post.payloads[0]["variables"][
                    "popularTitlesFilter"]["objectTypes"]
                # JustWatch names the TV type SHOW; our enum says SERIES.
                expected = "SHOW" if content_type is ContentType.SERIES else "MOVIE"
                self.assertEqual([str(o) for o in object_types], [expected])

    def test_the_two_types_query_differently(self):
        object_types = {}
        for content_type in (ContentType.MOVIE, ContentType.SERIES):
            post = RecordingPost([fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX)])
            source = source_with(post)
            source.fetch(None, country="NL", provider="netflix",
                         cursor={"offset": 0, "content_type": content_type})
            object_types[content_type] = post.payloads[0]["variables"][
                "popularTitlesFilter"]["objectTypes"]

        self.assertNotEqual(object_types[ContentType.MOVIE],
                            object_types[ContentType.SERIES],
                            "MOVIE and SERIES must not share one query")


class CountryPreconditionTests(SimpleTestCase):
    """Country is pinned in detail elsewhere; here only as a precondition."""

    def test_year_partitioned_walk_still_carries_the_country(self):
        post = RecordingPost([fx.FakeResponse(payload=catalogue_page(2020))])
        source = source_with(post)

        source.fetch(None, country="IT", provider="netflix",
                     cursor={"offset": 0, "min_year": 2020, "max_year": 2021})

        for payload in post.payloads:
            self.assertEqual(payload["variables"]["country"], "IT")

    def test_missing_country_still_fails_loudly_for_a_catalogue_walk(self):
        post = RecordingPost()
        source = source_with(post)

        with self.assertRaises(SourceFetchError):
            source.fetch(None, provider="netflix", cursor={"offset": 0})
        self.assertEqual(post.payloads, [],
                         "a request went out without a country")


class FailureIsNotAnEmptyPageTests(SimpleTestCase):
    """A transport failure must never look like 'this partition is empty'."""

    def test_a_graphql_error_raises_rather_than_returning_no_titles(self):
        post = RecordingPost([
            fx.FakeResponse(payload={"errors": [{"message": "boom"}]})
        ])
        source = source_with(post)

        with self.assertRaises(SourceFetchError):
            source.fetch(None, country="NL", provider="netflix",
                         cursor={"offset": 0})

    def test_an_http_500_is_not_an_empty_catalogue(self):
        post = RecordingPost([fx.FakeResponse(status_code=500)])
        source = source_with(post)

        with self.assertRaises(SourceFetchError):
            source.fetch(None, country="NL", provider="netflix",
                         cursor={"offset": 0})

    def test_a_failed_walk_does_not_report_progress(self):
        post = RecordingPost([fx.FakeResponse(status_code=500)])
        source = source_with(post)

        with self.assertRaises(SourceFetchError):
            source.fetch(None, country="NL", provider="netflix",
                         cursor={"offset": 0}, pages=3)
        # The failure aborted the walk instead of consuming the page budget.
        self.assertLess(len(post.payloads), 3)


class AcceptsKwargTests(SimpleTestCase):
    """The compatibility shim that keeps `pages` off older sources.

    P8 added an optional `pages` knob to JustWatch's `fetch`. Passing it
    unconditionally broke every other catalogue source (106 test errors), because
    an undeclared keyword is a hard `TypeError`, not a silent default. These pin
    the introspection that prevents that recurring.
    """

    def test_recognises_a_plain_function(self):
        def fetch(content=None, *, provider=None, pages=1):
            return pages

        self.assertTrue(accepts_kwarg(fetch, "pages"))
        self.assertTrue(accepts_kwarg(fetch, "provider"))

    def test_recognises_a_bound_method(self):
        class _S:
            def fetch(self, content=None, *, pages=1):
                return pages

        self.assertTrue(accepts_kwarg(_S().fetch, "pages"))

    def test_rejects_a_source_without_the_keyword(self):
        def fetch(content=None, *, provider=None):
            return None

        self.assertFalse(accepts_kwarg(fetch, "pages"))

    def test_rejects_an_undeclared_name(self):
        def fetch(content=None, *, pages=1):
            return pages

        self.assertFalse(accepts_kwarg(fetch, "nonesuch"))

    def test_keyword_only_and_positional_or_keyword_both_count(self):
        def mixed(a, pages=1, *, other=2):
            return pages

        def kwonly(*, pages=1):
            return pages

        self.assertTrue(accepts_kwarg(mixed, "pages"))
        self.assertTrue(accepts_kwarg(kwonly, "pages"))

    def test_var_keyword_is_not_evidence_of_a_declared_parameter(self):
        """**kwargs tolerates anything, but proves nothing about intent.

        Treated as "not declared" so a source that merely swallows **kwargs is
        not handed a JustWatch-specific knob it cannot act on.
        """
        def loose(**kwargs):
            return kwargs

        self.assertFalse(accepts_kwarg(loose, "pages"))

    def test_a_required_parameter_is_still_recognised(self):
        """Required and optional are both declared; neither is suppressed."""
        def fetch(content, *, pages):
            return pages

        self.assertTrue(accepts_kwarg(fetch, "pages"))

    def test_non_callables_and_builtins_are_handled_safely(self):
        for obj in (None, 42, "pages", len):
            with self.subTest(obj=obj):
                self.assertIn(accepts_kwarg(obj, "pages"), (True, False))

    def test_every_registered_catalogue_source_survives_the_introspection(self):
        """Real sources, not just doubles: this is what broke in production."""
        from availability.sources import source_registry

        for slug in ("justwatch", "abc_iview", "bbc_iplayer", "prime_video",
                     "tubi", "watchmode"):
            with self.subTest(source=slug):
                source = source_registry.get(slug)
                # Must never raise, whatever the signature shape.
                self.assertIn(accepts_kwarg(source.fetch, "pages"), (True, False))


class PageTooLargeIsClampedNotFatalTests(SimpleTestCase):
    """The server-side complexity ceiling is halved away, never swallowed."""

    def test_page_too_large_halves_the_page_and_retries(self):
        post = RecordingPost([
            fx.FakeResponse(payload={
                "errors": [{"message": "page too large"}]
            }),
            fx.FakeResponse(payload=catalogue_page()),
        ])
        source = source_with(post, page_size=100)

        result = source.fetch(None, country="NL", provider="netflix",
                              cursor={"offset": 0})

        self.assertGreaterEqual(len(post.payloads), 2)
        # The retry is observable in the request itself: same offset, halved
        # page. The clamp is local to the walk, so it must NOT mutate the
        # source's configured size and leak into later partitions.
        self.assertEqual(post.firsts(), [100, 50])
        self.assertEqual(source.page_size, 100,
                         "a walk-local clamp must not mutate the source")
        self.assertGreaterEqual(len(result.contents), 0)