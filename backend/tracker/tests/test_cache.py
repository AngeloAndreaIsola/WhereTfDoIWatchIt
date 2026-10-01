"""Tests for the legacy ``_write_result`` helper + rate limiter."""
import datetime as dt

from django.test import TestCase

from tracker.models import Availability, LanguageEntry, LanguageInfo, Movie, Region
from tracker.services.cache import AvailabilityCache
from tracker.services.rate_limit import (
    DAILY_LIMIT,
    ApiCallCounter,
    RateLimitExceeded,
    RateLimiter,
)
from tracker.services.streaming_api import StreamingOffer, StreamingResult


def _offer(country="US", service="Netflix", offer_type="subscription", quality="4k", audio=("en",), subs=("en",)):
    return StreamingOffer(
        country=country,
        service_id=service.lower(),
        service_name=service,
        offer_type=offer_type,
        quality=quality,
        audio_languages=list(audio),
        subtitle_languages=list(subs),
    )


class CacheTests(TestCase):
    """The legacy write helper ``AvailabilityCache._write_result``.

    Reads retired with the Phase C read-path migration: freshness and
    rate-limit behaviour now live in the event-driven refresh
    (``test_event_driven_refresh``) and the canonical query services.
    """

    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=1, title="Cache Test", year=2020)
        self.cache = AvailabilityCache()

    def test_write_result_persists_offers_and_languages(self):
        result = StreamingResult(
            tmdb_id=1,
            offers=[_offer(), _offer(country="IT", service="Prime")],
        )
        written = self.cache._write_result(self.movie, result)

        self.assertEqual(written, 2)
        self.assertEqual(
            {a.region.country for a in self.movie.availabilities.all()},
            {"US", "IT"},
        )
        # Language entries created per movie+region.
        infos = LanguageInfo.objects.filter(movie=self.movie)
        self.assertEqual(infos.count(), 2)
        self.assertEqual(
            LanguageEntry.objects.filter(language_info__in=infos).count(), 4
        )  # 2 audio + 2 subs

    def test_write_result_is_idempotent(self):
        result = StreamingResult(
            tmdb_id=1,
            offers=[_offer(), _offer(country="IT", service="Prime")],
        )
        self.cache._write_result(self.movie, result)
        self.cache._write_result(self.movie, result)

        self.assertEqual(Availability.objects.filter(movie=self.movie).count(), 2)
        infos = LanguageInfo.objects.filter(movie=self.movie)
        self.assertEqual(infos.count(), 2)
        self.assertEqual(
            LanguageEntry.objects.filter(language_info__in=infos).count(), 4
        )


class RateLimiterTests(TestCase):
    def setUp(self):
        ApiCallCounter.objects.filter(service="rl_test").delete()

    def test_acquire_increments(self):
        limiter = RateLimiter("rl_test")
        limiter.acquire()
        limiter.acquire()
        counter = ApiCallCounter.objects.get(service="rl_test")
        self.assertEqual(counter.count, 2)

    def test_remaining_decreases(self):
        limiter = RateLimiter("rl_test")
        self.assertEqual(limiter.remaining(), DAILY_LIMIT)
        limiter.acquire()
        self.assertEqual(limiter.remaining(), DAILY_LIMIT - 1)

    def test_exceeds_raises(self):
        limiter = RateLimiter("rl_test")
        ApiCallCounter.objects.create(
            service="rl_test",
            call_date=dt.datetime.utcnow().date(),
            count=DAILY_LIMIT,
        )
        with self.assertRaises(RateLimitExceeded):
            limiter.acquire()
