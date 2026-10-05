"""Unit tests for the Streaming Availability v4 parser.

These run fully offline against a saved fixture — no live RapidAPI call — so
they never touch the 100 req/day free-tier quota. The fixture exercises the
real v4 nested schema (``streamingOptions`` -> ``service.id`` / ``audios`` /
``subtitles``) plus 3-letter language codes so we cover normalization.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import requests
from django.test import SimpleTestCase

from tracker.services.streaming_api import (
    StreamingAvailabilityClient,
    UpstreamTitleNotFound,
    service_display_name,
)

FIXTURE = Path(__file__).parent / "fixtures" / "streaming_movie_sample.json"


class ParseOffersTests(SimpleTestCase):
    """Parse the v4 sample and assert offer shape + normalization."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with FIXTURE.open() as fh:
            cls.payload = json.load(fh)
        cls.offers = StreamingAvailabilityClient._parse_offers(cls.payload)

    def _find(self, country, service_id):
        return next(
            o for o in self.offers
            if o.country == country and o.service_id == service_id
        )

    def test_excludes_rent_and_buy_offers(self):
        # The US "apple" entry is a rent offer and must not survive the filter.
        self.assertTrue(all(o.offer_type in {"subscription", "free"} for o in self.offers))
        self.assertFalse(any(o.service_id == "apple" for o in self.offers))
        # us(prime) + jp(prime) + gb(netflix) = 3; apple(rent) dropped.
        self.assertEqual(len(self.offers), 3)

    def test_prime_offer_parsed_with_stable_id_and_display_name(self):
        jp = self._find("JP", "prime")
        self.assertEqual(jp.service_id, "prime")
        self.assertEqual(jp.service_name, "Prime Video")
        self.assertEqual(jp.offer_type, "subscription")
        self.assertEqual(jp.quality, "hd")

    def test_language_codes_normalized_to_canonical_uppercase(self):
        # jpn -> JA, eng -> EN (order preserved from the source list).
        jp = self._find("JP", "prime")
        self.assertEqual(jp.audio_languages, ["JA", "EN"])
        self.assertEqual(jp.subtitle_languages, ["EN", "JA"])
        # spa -> ES on the US prime offer.
        us = self._find("US", "prime")
        self.assertEqual(us.audio_languages, ["EN", "ES"])
        self.assertEqual(us.subtitle_languages, ["EN", "ES"])
        self.assertEqual(us.quality, "uhd")

    def test_non_prime_provider_kept_with_its_own_id(self):
        # Netflix is a different provider — parsed, not dropped. The prime
        # endpoint's provider filter (not the parser) is what excludes it later.
        gb = self._find("GB", "netflix")
        self.assertEqual(gb.service_id, "netflix")
        self.assertEqual(gb.service_name, "Netflix")


class PrimeOfferTypeTests(SimpleTestCase):
    """Only ``subscription``/``free`` Prime entries survive parsing.

    A series payload can list Prime Video by other routes — a Prime Video
    Channel (``addon``, billed through the channel provider) or a ``buy``/
    ``rent`` offer. None of those mean "included with the Prime subscription",
    so none of them may reach the Availability cache, where they would later be
    presented as an ordinary Prime Video subscription.
    """

    @staticmethod
    def _prime_payload():
        entry = lambda offer_type: {
            "type": offer_type,
            "quality": "hd",
            "audios": ["eng"],
            "subtitles": ["eng"],
            "service": {"id": "prime", "name": "Prime Video"},
            "link": f"https://www.primevideo.com/detail/{offer_type}",
        }
        return {
            "streamingOptions": {
                "CA": [entry("addon")],
                "AT": [entry("buy"), entry("rent")],
                "US": [entry("subscription")],
                "NL": [entry("free")],
            }
        }

    def test_only_subscription_and_free_prime_offers_survive(self):
        offers = StreamingAvailabilityClient._parse_offers(self._prime_payload())

        self.assertEqual(
            sorted((o.country, o.offer_type) for o in offers),
            [("NL", "free"), ("US", "subscription")],
        )
        self.assertTrue(all(o.service_id == "prime" for o in offers))

    def test_prime_addon_and_buy_never_become_availability_rows(self):
        """The parser is the stage that must reject them — nothing later can."""
        offers = StreamingAvailabilityClient._parse_offers(self._prime_payload())
        surviving_countries = {o.country for o in offers}

        self.assertNotIn("CA", surviving_countries)  # Prime Video Channel
        self.assertNotIn("AT", surviving_countries)  # purchase offer


class UpstreamNotFoundTests(SimpleTestCase):
    """A 404 must be signalled as a PERMANENT miss, not a transient fault.

    The distinction decides whether the caller retries. Every retry of an absent
    title re-earns the same 404 while spending metered API budget, so the client
    translates 404 at the HTTP boundary into ``UpstreamTitleNotFound`` — and
    leaves every other failure as an ordinary exception, so transient faults
    (5xx, 429, timeouts) keep their retry ladder.
    """

    class _Resp:
        def __init__(self, status_code):
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"{self.status_code} error")

        def json(self):
            return {"streamingOptions": {}}

    def _client_get(self, status_code):
        client = StreamingAvailabilityClient(api_key="test-key")
        with patch(
            "tracker.services.streaming_api.requests.get",
            return_value=self._Resp(status_code),
        ):
            return client.get_availability(1234, content_type="movie")

    def test_404_raises_upstream_title_not_found(self):
        with self.assertRaises(UpstreamTitleNotFound) as ctx:
            self._client_get(404)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("1234", str(ctx.exception))

    def test_5xx_stays_a_plain_transient_exception(self):
        with self.assertRaises(requests.HTTPError):
            self._client_get(503)

    def test_429_stays_a_plain_transient_exception(self):
        # A rate-limit response is worth retrying, so it must NOT be
        # misclassified as "title absent" and skipped.
        with self.assertRaises(requests.HTTPError):
            self._client_get(429)

    def test_upstream_not_found_is_not_an_http_error(self):
        # Deliberately not a requests.HTTPError subclass: callers must be able
        # to tell "provider has no such title" from "the request went wrong".
        self.assertFalse(issubclass(UpstreamTitleNotFound, requests.HTTPError))


class ServiceDisplayNameTests(SimpleTestCase):
    def test_known_ids_map_to_pretty_names(self):
        self.assertEqual(service_display_name("prime"), "Prime Video")
        self.assertEqual(service_display_name("netflix"), "Netflix")

    def test_unknown_id_falls_back_to_title_case(self):
        self.assertEqual(service_display_name("someservice"), "Someservice")
        self.assertEqual(service_display_name(""), "")
