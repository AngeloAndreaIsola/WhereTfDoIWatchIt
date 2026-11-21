"""Issue #174 (F5) — fixture-only pins for JustWatch ``availableTo`` handling.

Research artifact companion: ``docs/justwatch-availableto-semantics.md``.
These tests are deterministic and offline: no HTTP seam is exercised, no
database row is written, and no production code is modified. They pin the
*observed current behaviour* of the transform chain so the semantics memo
cites executable evidence rather than prose:

1. ``JustWatchSource._parse_datetime`` — the only place ``availableTo``
   becomes ``Offer.available_until`` (``justwatch.py:564``). Its silent
   ``None`` on invalid input and its naive->UTC coercion are deliberate
   conservative choices, and they are pinned here exactly because they are
   easy to "fix" into guessing later.
2. Transport passthrough — ``justwatch_client._parse_offer`` copies the raw
   ``availableTo`` value without interpretation (``justwatch_client.py:919``).
3. Adapter transform — ``_offers_for`` over fixture-built title nodes, for
   subscription / rent / FAST / null / date-only / invalid inputs. FAST and
   rent matter: the official JustWatch feed contract uses ``available_to``
   for broadcast-slot ends and storefront windows too, so the adapter's
   offer-type-independent date passthrough is a documented failure mode,
   not a bug these tests "fix".
4. Retained corpus — every JustWatch offer in ``aggregator_fixtures`` carries
   ``availableTo: null``. There is no retained non-null sample anywhere in
   the repository. If a future fixture adds one, this test fails on purpose:
   the memo's "no retained non-null evidence" claim must be revisited then.
"""
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import TestCase as SimpleTestCase

from availability.domain.enums import ContentType, OfferType
from availability.sources.justwatch import JustWatchSource
from availability.sources.justwatch_client import _parse_offer, _parse_title

from . import aggregator_fixtures as fx

UTC = dt_timezone.utc


def _title_with_offer(monetization="FLATRATE", available_to=None,
                      object_type="MOVIE", season_number=None,
                      episode_number=None):
    """Build a parsed JustWatch title from the retained fixture helpers."""
    node = fx.title(
        "tm174001", "Fixture Title", object_type=object_type, year=2024,
        tmdb_id="174001", imdb_id="tt1740001",
        season_number=season_number, episode_number=episode_number,
        offers=[fx.offer("o174", "netflix", "Netflix", "nfx",
                         monetization=monetization, available_to=available_to)],
    )
    return _parse_title(node)


def _adapt(title, country="NL"):
    source = JustWatchSource()
    content_type = source._content_type(title)
    offers = source._offers_for(
        title, country=country, content_type=content_type,
        tmdb_id=title.tmdb_id_int, content=title,
    )
    assert len(offers) == 1, "fixture title must yield exactly one mapped offer"
    return offers[0]


class ParseDatetimeSemantics(SimpleTestCase):
    """``_parse_datetime`` is the whole timestamp/timezone contract today."""

    def test_none_and_empty_and_whitespace_are_unknown(self):
        for value in (None, "", "   "):
            self.assertIsNone(JustWatchSource._parse_datetime(value))

    def test_iso_utc_z_suffix(self):
        parsed = JustWatchSource._parse_datetime("2026-12-01T23:59:59Z")
        self.assertEqual(parsed, datetime(2026, 12, 1, 23, 59, 59, tzinfo=UTC))

    def test_iso_explicit_offset_is_preserved_not_rebased(self):
        # The official feed schema example uses +00:00; a non-zero offset
        # must survive as stated, never be silently reinterpreted.
        parsed = JustWatchSource._parse_datetime("2026-12-01T20:20:39+01:00")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.utcoffset().total_seconds(), 3600)
        self.assertEqual(parsed, datetime(2026, 12, 1, 20, 20, 39,
                                          tzinfo=dt_timezone(timedelta(hours=1))))

    def test_date_only_becomes_midnight_utc(self):
        # documents the coercion: a date-only value carries no time or zone,
        # and the adapter assumes 00:00 UTC. Whether providers mean local
        # end-of-day is NOT proven (see memo §7/§10).
        parsed = JustWatchSource._parse_datetime("2026-12-01")
        self.assertEqual(parsed, datetime(2026, 12, 1, 0, 0, 0, tzinfo=UTC))

    def test_naive_datetime_is_assumed_utc(self):
        parsed = JustWatchSource._parse_datetime("2026-12-01T20:20:39")
        self.assertEqual(parsed, datetime(2026, 12, 1, 20, 20, 39, tzinfo=UTC))

    def test_invalid_values_are_silently_unknown(self):
        for value in ("not-a-date", "2026-13-45", "0", 0, False, 1764547200):
            self.assertIsNone(JustWatchSource._parse_datetime(value))

    def test_surrounding_whitespace_is_stripped(self):
        parsed = JustWatchSource._parse_datetime("  2026-12-01T00:00:00Z  ")
        self.assertEqual(parsed, datetime(2026, 12, 1, 0, 0, 0, tzinfo=UTC))


class TransportPassthrough(SimpleTestCase):
    def test_parse_offer_copies_available_to_raw(self):
        raw = "2026-12-01T23:59:59Z"
        offer = _parse_offer(fx.offer("o174", "netflix", "Netflix", "nfx",
                                      available_to=raw))
        self.assertEqual(offer.available_to, raw)

    def test_parse_offer_null_stays_null(self):
        offer = _parse_offer(fx.offer("o174", "netflix", "Netflix", "nfx"))
        self.assertIsNone(offer.available_to)


class AdapterTransform(SimpleTestCase):
    """Fixture input -> ProviderOffer output, through the real parser chain."""

    def test_subscription_offer_date_reaches_available_until(self):
        offer = _adapt(_title_with_offer(
            available_to="2026-12-01T23:59:59Z"))
        self.assertEqual(offer.offer_type, OfferType.SUBSCRIPTION)
        self.assertEqual(offer.country, "NL")
        self.assertEqual(offer.provider_id, "netflix")
        self.assertEqual(offer.available_until,
                         datetime(2026, 12, 1, 23, 59, 59, tzinfo=UTC))
        # available_from is never written by this adapter.
        self.assertIsNone(offer.available_from)

    def test_null_available_to_is_unknown_not_a_date(self):
        offer = _adapt(_title_with_offer(available_to=None))
        self.assertIsNone(offer.available_until)

    def test_invalid_available_to_is_unknown_not_a_crash(self):
        offer = _adapt(_title_with_offer(available_to="whenever"))
        self.assertIsNone(offer.available_until)

    def test_date_only_available_to_is_midnight_utc(self):
        offer = _adapt(_title_with_offer(available_to="2026-12-01"))
        self.assertEqual(offer.available_until,
                         datetime(2026, 12, 1, 0, 0, 0, tzinfo=UTC))

    def test_rent_offer_window_is_passed_through_unchanged(self):
        # A rent offer's available_to is a storefront window end per the
        # official feed examples; the adapter does not distinguish it.
        offer = _adapt(_title_with_offer(
            monetization="RENT", available_to="2026-12-01T23:59:59Z"))
        self.assertEqual(offer.offer_type, OfferType.RENT)
        self.assertEqual(offer.available_until,
                         datetime(2026, 12, 1, 23, 59, 59, tzinfo=UTC))

    def test_fast_slot_end_is_passed_through_as_unknown_offer_type(self):
        # Official feed contract: FAST offers MUST carry available_from/to
        # as broadcast slot bounds (minutes apart). 'fast' is not in
        # JUSTWATCH_MONETIZATION_TYPES, so the row persists as UNKNOWN with
        # a slot-end 'expiry'. Leaving Soon must never read such rows.
        offer = _adapt(_title_with_offer(
            monetization="FAST", available_to="2026-11-21T20:30:39+00:00"))
        self.assertEqual(offer.offer_type, OfferType.UNKNOWN)
        self.assertEqual(offer.available_until,
                         datetime(2026, 11, 21, 20, 30, 39, tzinfo=UTC))

    def test_season_granularity_carries_season_number(self):
        title = _title_with_offer(
            object_type="SEASON", season_number=2,
            available_to="2026-12-01T23:59:59Z")
        offer = _adapt(title)
        self.assertEqual(offer.content_type, ContentType.SEASON)
        self.assertEqual(offer.season_number, 2)
        self.assertIsNone(offer.episode_number)
        self.assertEqual(offer.available_until,
                         datetime(2026, 12, 1, 23, 59, 59, tzinfo=UTC))


class RetainedCorpusEvidence(SimpleTestCase):
    """The retained JustWatch fixtures contain no non-null availableTo."""

    def test_every_retained_justwatch_offer_has_null_available_to(self):
        offers = []
        listing_fixtures = (
            fx.JW_CATALOGUE_NL_NETFLIX, fx.JW_CATALOGUE_UNMAPPED_ONLY,
            fx.JW_SEARCH_RAKUTEN_NL, fx.JW_SEARCH_MOVIE, fx.JW_SEARCH_SHOW,
            fx.JW_SEARCH_MULTI_COUNTRY_ACCESS, fx.JW_SEARCH_SERIES_JP,
            fx.JW_SEARCH_SERIES_UNRESOLVED,
        )
        for envelope in listing_fixtures:
            for edge in envelope["data"]["popularTitles"]["edges"]:
                offers.extend(edge["node"].get("offers") or [])
        for season in fx.JW_SEASONS["node"]["seasons"]:
            offers.extend(season.get("offers") or [])
        for episode in fx.JW_EPISODES["node"]["episodes"]:
            offers.extend(episode.get("offers") or [])
        for country_offers in fx.JW_OFFERS_MULTI_COUNTRY["node"].values():
            offers.extend(country_offers)

        # Guard against a vacuous pass: the corpus must actually contain
        # offers, and every one of them must state availableTo: null.
        self.assertEqual(len(offers), 24)
        for offer in offers:
            self.assertIn("availableTo", offer)
            self.assertIsNone(offer["availableTo"])
