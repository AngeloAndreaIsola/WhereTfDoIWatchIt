"""P0 — JustWatch availability data-integrity regressions.

Four defects are pinned here, each at the smallest seam that can produce the
wrong data:

1. **Pagination** — a ``page too large`` rejection used to recurse into a fresh
   ``_fetch_catalogue`` call. That discarded every title/offer already collected,
   dropped the partition id from the returned cursor, and mutated the shared
   ``page_size`` so the clamp leaked into the *next* partition. The walk is now
   one iterative loop whose retry state is local to the call.

2. **Title identity** — ``_search_titles`` used to return ``candidates[:1]``
   when neither the TMDb id nor the year matched, so "the first result wins"
   could attach a different title (or a movie with a TV show's numeric id) to
   the requested identity. The matcher now requires type-compatible TMDb id
   evidence, or conservative exact title (+ year) evidence, and answers "no
   match" otherwise.

3. **Provider attribution** — a provider partition used to stamp its discovered
   ``ProviderContent`` row with the title's *first mapped package*, so walking
   Netflix could create a Prime Video catalogue entry. The partition's provider
   is now passed explicitly through ``_content``.

4. The same tests double as order-independence checks for (3): offer order in
   the payload never decides the partition's provider.

No test here touches the network: the client's ``http_post`` seam is replaced
with a deterministic recorder over synthetic pages.
"""
from unittest import TestCase as SimpleTestCase

from availability.domain.enums import ContentType
from availability.sources.base import ContentRequest
from availability.sources.justwatch import JustWatchSource
from availability.sources.justwatch_client import JustWatchClient

from . import aggregator_fixtures as fx

MOVIE = ContentType.MOVIE
SERIES = ContentType.SERIES


def sleeper(_attempt):
    """No real backoff sleeps in tests."""


class RecordingPost:
    """Serve queued responses and record every payload actually POSTed."""

    def __init__(self, responses=()):
        self.responses = list(responses)
        self.payloads = []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.payloads.append(json)
        if not self.responses:
            raise AssertionError("unexpected extra JustWatch request")
        return self.responses.pop(0)

    def offsets(self):
        return [(p or {}).get("variables", {}).get("offset") for p in self.payloads]

    def firsts(self):
        return [(p or {}).get("variables", {}).get("first") for p in self.payloads]


def source_with(post, **kwargs):
    return JustWatchSource(
        client=JustWatchClient(http_post=post, sleeper=sleeper), **kwargs
    )


def page(*titles):
    return fx.envelope({
        "popularTitles": {"edges": fx.edges(*titles)}
    })


PAGE_TOO_LARGE = {"errors": [{"message": "page too large"}]}


def netflix_offer(offer_id, technical_name="netflix"):
    return fx.offer(
        offer_id, technical_name,
        "Netflix" if technical_name == "netflix" else "Amazon Prime Video",
        "nfx" if technical_name == "netflix" else "prv",
    )


# ---------------------------------------------------------------------------
# P0.1 — pagination retry must not lose results, cursor or partition context
# ---------------------------------------------------------------------------


class PageTooLargePaginationTests(SimpleTestCase):
    def test_retry_keeps_collected_results_and_advances_correctly(self):
        """A clamp on page 2 must not discard page 1 or the pages after it."""
        post = RecordingPost([
            fx.FakeResponse(payload=page(
                fx.title("tm1", "First", tmdb_id="1",
                         offers=[netflix_offer("o1")]))),
            fx.FakeResponse(payload=PAGE_TOO_LARGE),       # offset 100, first 100
            fx.FakeResponse(payload=page(
                fx.title("tm2", "Second", tmdb_id="2",
                         offers=[netflix_offer("o2")]))),  # retry, first 50
            fx.FakeResponse(payload=page(
                fx.title("tm3", "Third", tmdb_id="3",
                         offers=[netflix_offer("o3")]))),  # offset 150, first 50
        ])
        source = source_with(post, page_size=100)

        result = source.fetch(
            None, country="NL", provider="netflix",
            cursor={"offset": 0, "content_type": MOVIE,
                    "partition": "movie:NL:netflix"},
            pages=3,
        )

        # Every successfully fetched title survived the clamp.
        self.assertEqual(
            [content.title for content in result.contents],
            ["First", "Second", "Third"],
        )
        self.assertEqual(
            [offer.provider_content_id for offer in result.offers],
            ["tm1", "tm2", "tm3"],
        )

        # Cursor progression is exact: 0 -> 100 -> (retry at 100) -> 150 -> 200.
        # The first page sends null/0; the retry repeats the *failed* offset.
        self.assertIn(post.offsets()[0], (0, None))
        self.assertEqual(post.offsets()[1:], [100, 100, 150])
        self.assertEqual(post.firsts(), [100, 100, 50, 50])

        # The clamp is local to this walk: the returned cursor still carries the
        # partition id, and its offset counts the pages really consumed.
        self.assertEqual(result.cursor, "movie:NL:netflix=200")
        self.assertEqual(source.page_size, 100, "the clamp must not leak")

        # The page budget is not consumed by the rejected request: three pages
        # of data were walked, not two.
        self.assertEqual(len(result.contents), 3)

    def test_a_clamp_on_the_first_page_does_not_lose_the_page_budget(self):
        post = RecordingPost([
            fx.FakeResponse(payload=PAGE_TOO_LARGE),
            fx.FakeResponse(payload=page(fx.title("tm1", "First", tmdb_id="1"))),
            fx.FakeResponse(payload=page(fx.title("tm2", "Second", tmdb_id="2"))),
        ])
        source = source_with(post, page_size=100)

        result = source.fetch(None, country="NL", provider="netflix",
                              cursor={"offset": 0, "content_type": MOVIE},
                              pages=2)

        self.assertEqual([c.title for c in result.contents], ["First", "Second"])
        self.assertEqual(post.firsts(), [100, 50, 50])
        self.assertIn(post.offsets()[1], (0, None))
        self.assertEqual(post.offsets()[2], 50)

    def test_the_clamp_does_not_leak_into_the_next_partition(self):
        first = RecordingPost([
            fx.FakeResponse(payload=PAGE_TOO_LARGE),
            fx.FakeResponse(payload=page(fx.title("tm1", "First", tmdb_id="1"))),
        ])
        source = source_with(first, page_size=100)
        source.fetch(None, country="NL", provider="netflix",
                     cursor={"offset": 0, "content_type": MOVIE,
                             "partition": "movie:NL:netflix"})

        # Same source instance, next partition/request: it must start at the
        # configured page size again, not the halved one from the other walk.
        self.assertEqual(source.page_size, 100)
        second = RecordingPost([
            fx.FakeResponse(payload=page(fx.title("tm2", "Second", tmdb_id="2")))
        ])
        source.client = JustWatchClient(http_post=second, sleeper=sleeper)
        result = source.fetch(None, country="NL", provider="prime",
                              cursor={"offset": 0, "content_type": MOVIE,
                                      "partition": "movie:NL:prime"})

        self.assertEqual(second.firsts(), [100])
        self.assertEqual(result.cursor, "movie:NL:prime=100")


# ---------------------------------------------------------------------------
# P0.2 — a search result must not win merely by being first
# ---------------------------------------------------------------------------


EMPTY_SEASONS = fx.envelope({"node": {"seasons": []}})


class TitleIdentityTests(SimpleTestCase):
    def set_source(self, *responses):
        post = RecordingPost([fx.FakeResponse(payload=response)
                              for response in responses])
        return source_with(post), post

    def fetch(self, source, **request):
        return source.fetch(
            ContentRequest(**request), country="NL"
        )

    def test_exact_tmdb_id_with_matching_media_type_resolves(self):
        source, _ = self.set_source(page(
            fx.title("tm1", "Superman", year=2025, tmdb_id="1061474"),
        ))

        result = self.fetch(source, tmdb_id=1061474, title="Superman",
                            year=2025, content_type="movie")

        self.assertEqual(len(result.contents), 1)
        self.assertEqual(result.contents[0].provider_content_id, "tm1")

    def test_the_same_numeric_tmdb_id_in_the_other_namespace_is_rejected(self):
        """TMDb movie ids and TV ids are different namespaces."""
        source, _ = self.set_source(
            # A SHOW carrying the numeric id the movie request asked for.
            page(fx.title("ts1", "Fight Club", object_type="SHOW", year=1999,
                          tmdb_id="550", full_path="/nl/tv-series/fight-club")),
            EMPTY_SEASONS,
        )

        result = self.fetch(source, tmdb_id=550, title="Fight Club",
                            year=1999, content_type="movie")

        self.assertEqual(result.contents, [])
        self.assertEqual(result.offers, [])

    def test_a_wrong_id_with_the_same_title_is_not_accepted(self):
        source, _ = self.set_source(page(
            fx.title("tm999", "Fight Club", year=1999, tmdb_id="999999"),
        ))

        result = self.fetch(source, tmdb_id=550, title="Fight Club",
                            year=1999, content_type="movie")

        self.assertEqual(result.contents, [])

    def test_distinct_native_nodes_claiming_the_same_typed_tmdb_id_are_ambiguous(self):
        for node_ids in (("tm1", "tm2"), ("tm2", "tm1")):
            with self.subTest(node_ids=node_ids):
                source, _ = self.set_source(page(*(
                    fx.title(node_id, "Fight Club", year=1999, tmdb_id="550",
                             offers=[netflix_offer("o1")])
                    for node_id in node_ids
                )))
                result = self.fetch(source, tmdb_id=550, title="Fight Club",
                                    year=1999, content_type=MOVIE)

                self.assertEqual(result.contents, [])
                self.assertEqual(result.offers, [])

    def test_repeated_native_node_still_resolves(self):
        node = fx.title("tm1", "Fight Club", year=1999, tmdb_id="550",
                        offers=[netflix_offer("o1")])
        source, _ = self.set_source(page(node, node))
        result = self.fetch(source, tmdb_id=550, title="Fight Club",
                            year=1999, content_type=MOVIE)

        self.assertEqual([offer.provider_content_id for offer in result.offers], ["tm1"])

    def test_nodes_without_native_identity_are_unresolved(self):
        for tmdb_id in ("550", None):
            with self.subTest(tmdb_id=tmdb_id):
                source, _ = self.set_source(page(
                    fx.title(None, "Fight Club", year=1999, tmdb_id=tmdb_id,
                             offers=[netflix_offer("o1")]),
                    fx.title(None, "Fight Club", year=1999, tmdb_id=tmdb_id,
                             offers=[netflix_offer("o2")]),
                ))
                result = self.fetch(source, tmdb_id=550, title="Fight Club",
                                    year=1999, content_type=MOVIE)

                self.assertEqual(result.contents, [])
                self.assertEqual(result.offers, [])

    def test_multiple_ambiguous_candidates_stay_unresolved(self):
        source, _ = self.set_source(page(
            fx.title("tm1", "The Thing", year=1982, tmdb_id="1091"),
            fx.title("tm2", "The Thing", year=1982, tmdb_id="60935"),
        ))

        # No TMDb id from the caller: two distinct identities share title/year,
        # so there is no evidence that picks one.
        result = self.fetch(source, tmdb_id=0, title="The Thing",
                            year=1982, content_type="movie")

        self.assertEqual(result.contents, [])

    def test_no_usable_identity_evidence_is_an_honest_empty_result(self):
        source, _ = self.set_source(page(
            fx.title("tm1", "Superman II", year=2026, tmdb_id="999999"),
        ))

        result = self.fetch(source, tmdb_id=0, title="Completely Different",
                            year=1999, content_type="movie")

        self.assertEqual(result.contents, [])
        self.assertEqual(result.offers, [])

    def test_title_and_year_are_sufficient_when_ids_are_unavailable(self):
        source, _ = self.set_source(
            page(
                fx.title("ts258721", "The Gentlemen", object_type="SHOW",
                         year=2024, tmdb_id=None,
                         full_path="/nl/tv-series/the-gentlemen",
                         total_season_count=0),
            ),
            EMPTY_SEASONS,
        )

        result = self.fetch(source, tmdb_id=0, title="the gentlemen",
                            year=2024, content_type="series")

        self.assertEqual(len(result.contents), 1)
        self.assertEqual(result.contents[0].provider_content_id, "ts258721")

    def test_exact_title_with_a_conflicting_year_is_not_sufficient(self):
        source, _ = self.set_source(page(
            fx.title("tm1", "The Thing", year=2011, tmdb_id=None),
        ))

        result = self.fetch(source, tmdb_id=0, title="The Thing",
                            year=1982, content_type="movie")

        self.assertEqual(result.contents, [])


# ---------------------------------------------------------------------------
# P0.4 — offers and catalogue rows follow the partition's provider
# ---------------------------------------------------------------------------


class ProviderAttributionTests(SimpleTestCase):
    """A Netflix walk may never create a Prime Video catalogue attribution."""

    @staticmethod
    def mixed_page(*, prime_first):
        offers = [
            netflix_offer("n1"),
            netflix_offer("p1", technical_name="amazonprimevideo"),
        ]
        if prime_first:
            offers.reverse()
        return page(
            fx.title("tm1", "Superman", year=2025, tmdb_id="1061474",
                     offers=offers),
        )

    def walk(self, provider, *, prime_first):
        post = RecordingPost([fx.FakeResponse(
            payload=self.mixed_page(prime_first=prime_first))])
        source = source_with(post)
        result = source.fetch(None, country="NL", provider=provider,
                              cursor={"offset": 0, "content_type": MOVIE})
        return result, post

    def test_netflix_partition_attributes_to_netflix(self):
        result, _ = self.walk("netflix", prime_first=False)

        self.assertEqual(
            {content.provider_id for content in result.contents}, {"netflix"}
        )
        self.assertEqual(
            {offer.provider_id for offer in result.offers}, {"netflix"}
        )

    def test_netflix_partition_is_not_redirected_to_the_first_package(self):
        """The old defect: Prime Video was the first offer and won the row."""
        result, _ = self.walk("netflix", prime_first=True)

        self.assertEqual(
            {content.provider_id for content in result.contents}, {"netflix"},
            "the partition provider, not the first mapped package, owns the row",
        )
        self.assertEqual(
            {offer.provider_id for offer in result.offers}, {"netflix"}
        )

    def test_prime_partition_attributes_to_prime(self):
        result, _ = self.walk("prime", prime_first=True)

        self.assertEqual(
            {content.provider_id for content in result.contents}, {"prime"}
        )
        self.assertEqual(
            {offer.provider_id for offer in result.offers}, {"prime"}
        )

    def test_each_partition_keeps_its_own_provider_association(self):
        netflix_result, _ = self.walk("netflix", prime_first=True)
        prime_result, _ = self.walk("prime", prime_first=False)

        netflix_rows = {
            (c.provider_id, c.provider_content_id) for c in netflix_result.contents
        }
        prime_rows = {
            (c.provider_id, c.provider_content_id) for c in prime_result.contents
        }
        self.assertEqual(netflix_rows, {("netflix", "tm1")})
        self.assertEqual(prime_rows, {("prime", "tm1")})

        self.assertEqual(
            {(o.provider_id, o.provider_content_id)
             for o in netflix_result.offers},
            {("netflix", "tm1")},
        )
        self.assertEqual(
            {(o.provider_id, o.provider_content_id)
             for o in prime_result.offers},
            {("prime", "tm1")},
        )

    def test_attribution_is_independent_of_offer_order(self):
        for prime_first in (False, True):
            with self.subTest(prime_first=prime_first):
                result, _ = self.walk("netflix", prime_first=prime_first)
                self.assertEqual(
                    {c.provider_id for c in result.contents}, {"netflix"}
                )
