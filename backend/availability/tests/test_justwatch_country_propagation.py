"""JustWatch country propagation — the real defect this file pins.

The live failure was::

    SourceFetchError: justwatch requires an explicit country code; it is
    never inferred from IP, VPN or browser locale

Reproduced by calling the title-targeted refresh with the source's own
documented entry point::

    AvailabilityRefreshService(source=justwatch).refresh_content(movie)

That raised the error above for *every* title, because both title-level
callers — ``tracker.services.refresh_requests`` and
``tracker.services.bulk_refresh`` — invoked ``refresh_content(movie)`` and
never passed a country. The guard in ``JustWatchSource._require_country`` was
correct and is deliberately unchanged; the callers were incomplete.

The distinction worth protecting, and the reason these tests assert at the
``http_post`` boundary rather than on an internal variable:

    a source *object* holding a country   !=   a GraphQL request carrying it

A fix that sets the country on the adapter and forgets to put it in the
payload would pass a unit test on an internal attribute and still fail in
production. So every propagation case here asserts the actual JSON variables
that would be POSTed.

Country semantics are also pinned explicitly. The application selects its
market manually, so:

* the selected country reaches the payload verbatim (uppercase ISO-3166-1);
* it works for any supported country, not one hardcoded market;
* a missing country still raises rather than silently querying another
  market — an aggregator asked about the wrong one returns confidently wrong
  offers;
* the *request* country is never confused with the *offer* countries in the
  response, which are filtered and normalised separately.
"""
from unittest import TestCase as SimpleTestCase

from availability.sources.base import ContentRequest, SourceFetchError
from availability.sources.justwatch import JustWatchSource
from availability.sources.justwatch_client import JustWatchClient

from . import aggregator_fixtures as fx

SA = "streaming_availability"


def sleeper(_attempt):
    """No real backoff sleeps in tests."""


class RecordingPost:
    """Captures every payload the client would POST, in order.

    Recording the whole sequence (rather than the last call) is what makes the
    retry and pagination cases meaningful: they assert that *every* request in
    the sequence carries the country, not merely that one of them does.
    """

    def __init__(self, responses=()):
        self.responses = list(responses)
        self.payloads = []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.payloads.append(json)
        return self.responses.pop(0) if self.responses else fx.FakeResponse()

    @property
    def countries(self):
        """The ``country`` variable of every recorded payload."""
        return [
            (p or {}).get("variables", {}).get("country")
            for p in self.payloads
        ]


def source_with(post, **kwargs):
    return JustWatchSource(
        client=JustWatchClient(http_post=post, sleeper=sleeper), **kwargs
    )


class CountryReachesTheGraphQLPayloadTests(SimpleTestCase):
    """2/3. The selected country must appear in the request that is sent."""

    def test_nl_reaches_the_search_and_offers_payloads(self):
        post = RecordingPost([fx.FakeResponse(payload=fx.JW_SEARCH_MOVIE)])
        source = source_with(post)

        source.fetch(
            ContentRequest(tmdb_id=1061474, title="Superman", year=2025),
            country="NL",
        )

        self.assertTrue(post.payloads, "no request was sent")
        for payload in post.payloads:
            self.assertEqual(
                payload["variables"]["country"],
                "NL",
                "a GraphQL request left the client without its country",
            )

    def test_a_non_nl_country_propagates_unchanged(self):
        """The fix must not be NL-shaped."""
        for country in ("IT", "GB", "US", "JP"):
            with self.subTest(country=country):
                post = RecordingPost([fx.FakeResponse(payload=fx.JW_SEARCH_MOVIE)])
                source = source_with(post)

                source.fetch(
                    ContentRequest(tmdb_id=1061474, title="Superman", year=2025),
                    country=country,
                )

                self.assertTrue(post.payloads)
                for payload in post.payloads:
                    self.assertEqual(payload["variables"]["country"], country)

    def test_lowercase_input_is_normalized_at_the_client_boundary(self):
        """The application's country is stored lowercase in places; the
        JustWatch endpoint wants an uppercase ISO code."""
        post = RecordingPost([fx.FakeResponse(payload=fx.JW_SEARCH_MOVIE)])
        source = source_with(post)

        source.fetch(
            ContentRequest(tmdb_id=1061474, title="Superman", year=2025),
            country="nl",
        )

        for payload in post.payloads:
            self.assertEqual(payload["variables"]["country"], "NL")

    def test_catalogue_path_also_carries_the_country(self):
        """Catalogue/discovery queries are a separate code path with their own
        variables, and must not be the one that forgets."""
        post = RecordingPost([fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX)])
        source = source_with(post)

        source.fetch(None, country="JP")

        self.assertTrue(post.payloads)
        for payload in post.payloads:
            self.assertEqual(payload["variables"]["country"], "JP")


class NoAccidentalCountryFallbackTests(SimpleTestCase):
    """6. A missing country must fail loudly, never guess a market."""

    def test_missing_country_raises_before_any_request_is_sent(self):
        post = RecordingPost()
        source = source_with(post)

        with self.assertRaises(SourceFetchError) as ctx:
            source.fetch(ContentRequest(tmdb_id=550, title="Fight Club"))

        self.assertIn("explicit country", str(ctx.exception))
        self.assertEqual(
            post.payloads, [], "a request went out despite the missing country"
        )

    def test_the_error_never_names_a_substitute_country(self):
        """The message must not smuggle in a default."""
        post = RecordingPost()
        source = source_with(post)
        with self.assertRaises(SourceFetchError) as ctx:
            source.fetch(None)
        message = str(ctx.exception).lower()
        for code in ("nl", "us", "gb", "it", "jp"):
            self.assertNotIn(
                f"defaulting to {code}", message, "a fallback market was suggested"
            )

    def test_malformed_country_is_rejected_before_the_request(self):
        post = RecordingPost()
        source = source_with(post)
        with self.assertRaises(ValueError):
            source.fetch(None, country="NLD")
        self.assertEqual(post.payloads, [])


class RetryPreservesCountryTests(SimpleTestCase):
    """4. Every retry must re-send the country, not a country-less variant."""

    def test_every_retry_carries_the_same_country(self):
        # Two 429s then success: the client retries transport faults, and the
        # retried payload must be identical in its country.
        post = RecordingPost(
            [
                fx.FakeResponse(status_code=429),
                fx.FakeResponse(status_code=429),
                fx.FakeResponse(payload=fx.JW_SEARCH_MOVIE),
            ]
        )
        source = source_with(post)

        source.fetch(
            ContentRequest(tmdb_id=1061474, title="Superman", year=2025),
            country="IT",
        )

        self.assertGreaterEqual(
            len(post.payloads), 2, "no retry happened; the test proves nothing"
        )
        for country in post.countries:
            self.assertEqual(country, "IT")


class PaginationPreservesCountryTests(SimpleTestCase):
    """5. Later pages must be requested for the same market."""

    def test_every_page_carries_the_country(self):
        post = RecordingPost(
            [
                fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX),
                fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX),
            ]
        )
        source = source_with(post, page_size=100)

        # Walk two pages of the same partition explicitly.
        source.fetch(None, country="GB", cursor={"offset": 0})
        source.fetch(None, country="GB", cursor={"offset": 100})

        self.assertEqual(len(post.payloads), 2)
        for country in post.countries:
            self.assertEqual(country, "GB")


class RequestCountryIsNotOfferCountryTests(SimpleTestCase):
    """7. The market asked about and the markets answered for are different.

    JustWatch returns offers for many countries. Those are filtered and
    normalised on the response side and must not be conflated with the single
    country the request asked about — conflating them would let a response for
    several markets quietly define "the" country.
    """

    def test_multi_country_offers_are_filtered_not_substituted(self):
        post = RecordingPost([fx.FakeResponse(payload=fx.JW_SEARCH_MOVIE)])
        source = source_with(post)

        result = source.fetch(
            ContentRequest(tmdb_id=1061474, title="Superman", year=2025),
            country="NL",
        )

        # The request country is what we asked about, regardless of response.
        for payload in post.payloads:
            self.assertEqual(payload["variables"]["country"], "NL")

        # And whatever the response contained, every returned offer is
        # normalised to a 2-letter market of its own.
        for offer in result.offers:
            self.assertEqual(
                len(offer.country), 2, f"offer country not normalised: {offer.country!r}"
            )
            self.assertTrue(offer.country.isalpha())


class SourceHasCountryButPayloadDoesNotTests(SimpleTestCase):
    """The exact regression class this fix closed.

    A country present on the adapter is not evidence of anything. These assert
    the payload, so a future refactor that drops the variable before the POST
    fails here instead of in production.
    """

    def test_country_variable_is_present_in_every_payload_of_a_title_fetch(self):
        post = RecordingPost([fx.FakeResponse(payload=fx.JW_SEARCH_MOVIE)])
        source = source_with(post)
        source.fetch(
            ContentRequest(tmdb_id=1061474, title="Superman", year=2025),
            country="US",
        )
        for payload in post.payloads:
            self.assertIn("country", payload["variables"])
            self.assertTrue(payload["variables"]["country"])

    def test_the_graphql_query_text_itself_demands_a_country(self):
        """The operation must declare the variable, so a payload without one
        would be a GraphQL error rather than a silent wrong-market answer."""
        post = RecordingPost([fx.FakeResponse(payload=fx.JW_SEARCH_MOVIE)])
        source = source_with(post)
        source.fetch(
            ContentRequest(tmdb_id=1061474, title="Superman", year=2025),
            country="NL",
        )
        for payload in post.payloads:
            query = payload["query"]
            self.assertIn("$country: Country!", query)
            self.assertIn("country: $country", query)