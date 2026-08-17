"""Tests for caching layer + rate limiter."""
import datetime as dt
from unittest.mock import MagicMock

from django.test import TestCase
from django.utils import timezone

from tracker.models import Availability, LanguageEntry, LanguageInfo, Movie, Region
from tracker.services.cache import AvailabilityCache
from tracker.services.rate_limit import (
    DAILY_LIMIT,
    ApiCallCounter,
    RateLimitExceeded,
    RateLimiter,
)
from tracker.services.streaming_api import StreamingOffer, StreamingResult


def _offer(country="us", service="Netflix", offer_type="subscription", quality="4k", audio=("en",), subs=("en",)):
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
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=1, title="Cache Test", year=2020)
        self.client = MagicMock()
        self.client.get_availability.return_value = StreamingResult(
            tmdb_id=1,
            offers=[_offer(), _offer(country="it", service="Prime")],
        )
        self.limiter = RateLimiter("test_cache")
        ApiCallCounter.objects.filter(service="test_cache").delete()
        self.cache = AvailabilityCache(client=self.client, limiter=self.limiter)

    def test_fresh_cache_skips_api(self):
        # Pre-seed a fresh Availability row.
        region = Region.objects.create(provider="Netflix", country="US")
        Availability.objects.create(
            movie=self.movie, region=region,
            last_checked_at=timezone.now(), is_available=True,
            offer_type="subscription", quality="4k",
        )
        status_obj = self.cache.get_or_refresh(self.movie)
        self.assertFalse(status_obj.refreshed)
        self.assertEqual(status_obj.reason, "fresh")
        self.client.get_availability.assert_not_called()

    def test_stale_cache_triggers_refresh(self):
        region = Region.objects.create(provider="Netflix", country="US")
        Availability.objects.create(
            movie=self.movie, region=region,
            last_checked_at=timezone.now() - dt.timedelta(days=8),
            is_available=True, offer_type="subscription", quality="4k",
        )
        status_obj = self.cache.get_or_refresh(self.movie)
        self.assertTrue(status_obj.refreshed)
        self.assertEqual(status_obj.reason, "stale")
        self.assertEqual(status_obj.offers_written, 2)
        self.assertGreater(LanguageInfo.objects.count(), 0)

    def test_missing_cache_refreshes(self):
        status_obj = self.cache.get_or_refresh(self.movie)
        self.assertTrue(status_obj.refreshed)
        self.assertEqual(status_obj.offers_written, 2)
        # Language entries created per movie+region.
        infos = LanguageInfo.objects.filter(movie=self.movie)
        self.assertEqual(infos.count(), 2)
        self.assertEqual(LanguageEntry.objects.filter(language_info__in=infos).count(), 4)  # 2 audio + 2 subs

    def test_rate_limit_returns_cached(self):
        # Fill counter to cap.
        ApiCallCounter.objects.create(
            service="test_cache",
            call_date=dt.datetime.utcnow().date(),
            count=DAILY_LIMIT,
        )
        status_obj = self.cache.get_or_refresh(self.movie)
        self.assertFalse(status_obj.refreshed)
        self.assertEqual(status_obj.reason, "rate_limited")
        self.client.get_availability.assert_not_called()


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