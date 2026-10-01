from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from availability.domain.enums import (
    ContentType,
    MatchMethod,
    OfferType,
    SyncMode,
    SyncStatus,
)
from availability.models import (
    Country,
    Offer,
    ProviderContent,
    Source,
    SyncCursor,
    SyncRun,
    build_offer_identity,
    build_provider_content_identity,
)
from availability.persistence.repositories import (
    CursorRepository,
    InactiveSyncError,
    MappingRepository,
    OfferRepository,
    ProviderContentRepository,
    SyncRunRepository,
)
from tracker.models import Provider


class OfferRepositoryTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(slug="prime-video", defaults={"name": "Prime Video"})
        self.country, _ = Country.objects.get_or_create(
            code="NL", defaults={"name": "Netherlands"}
        )
        self.it = Country.objects.create(code="IT", name="Italy")
        self.source = Source.objects.create(slug="justwatch", name="JustWatch", type="aggregator")
        self.repo = OfferRepository()

    def _payload(self, **overrides):
        payload = {
            "provider": self.provider,
            "country": self.country,
            "source": self.source,
            "content_type": ContentType.MOVIE,
            "tmdb_id": 550,
            "offer_type": OfferType.SUBSCRIPTION,
            "provider_content_id": "ASIN123",
            "provider_url": "https://example.com/watch",
            "quality": "4k",
            "audio_languages": None,
            "subtitle_languages": None,
        }
        payload.update(overrides)
        return payload

    def test_upsert_creates_fresh_offer(self):
        now = timezone.now()
        offer, created = self.repo.upsert(**self._payload(), now=now)
        self.assertTrue(created)
        self.assertEqual(offer.identity, "prime-video|NL|movie|550|||subscription|ASIN123")
        self.assertEqual(offer.first_seen, now)
        self.assertEqual(offer.last_seen, now)
        self.assertTrue(offer.currently_available)

    def test_upsert_preserves_first_seen_and_advances_last_seen(self):
        first = timezone.now() - timedelta(hours=5)
        second = timezone.now()
        original, _ = self.repo.upsert(**self._payload(), now=first)

        again, created = self.repo.upsert(
            **self._payload(provider_url="https://example.com/updated"), now=second
        )
        self.assertFalse(created)
        self.assertEqual(again.pk, original.pk)
        again.refresh_from_db()
        self.assertEqual(again.first_seen, first)
        self.assertEqual(again.last_seen, second)
        self.assertEqual(again.provider_url, "https://example.com/updated")
        self.assertTrue(again.currently_available)

    def test_different_countries_are_distinct_offers(self):
        _nl, created_nl = self.repo.upsert(**self._payload(), now=timezone.now())
        _it, created_it = self.repo.upsert(**self._payload(country=self.it), now=timezone.now())
        self.assertTrue(created_nl)
        self.assertTrue(created_it)
        self.assertEqual(Offer.objects.count(), 2)

    def test_season_and_episode_level_offers_are_distinct(self):
        base = {
            "content_type": ContentType.SERIES,
            "tmdb_id": 2316,
            "offer_type": OfferType.FREE,
            "provider_content_id": "show-1",
        }
        _series, created_series = self.repo.upsert(**self._payload(**base), now=timezone.now())
        _season, created_season = self.repo.upsert(
            **self._payload(**base, season_number=3), now=timezone.now()
        )
        _episode, created_episode = self.repo.upsert(
            **self._payload(**base, season_number=3, episode_number=4), now=timezone.now()
        )
        self.assertTrue(created_series)
        self.assertTrue(created_season)
        self.assertTrue(created_episode)
        self.assertEqual(Offer.objects.count(), 3)

    def test_language_null_and_empty_survive_upserts(self):
        offer, _ = self.repo.upsert(
            **self._payload(audio_languages=None, subtitle_languages=["nl"]),
            now=timezone.now(),
        )
        offer.refresh_from_db()
        self.assertIsNone(offer.audio_languages)
        self.assertEqual(offer.subtitle_languages, ["nl"])

        updated, created = self.repo.upsert(
            **self._payload(audio_languages=[], subtitle_languages=["nl"]),
            now=timezone.now(),
        )
        self.assertFalse(created)
        updated.refresh_from_db()
        self.assertEqual(updated.audio_languages, [])
        self.assertIsNotNone(updated.audio_languages)

    def test_get_for_content_filters_by_country_case_insensitively(self):
        self.repo.upsert(**self._payload(), now=timezone.now())
        self.repo.upsert(**self._payload(country=self.it), now=timezone.now())
        self.assertEqual(self.repo.get_for_content(550, ContentType.MOVIE).count(), 2)
        self.assertEqual(
            self.repo.get_for_content(550, ContentType.MOVIE, country_code="nl").count(), 1
        )
        self.assertEqual(self.repo.get_for_content(999, ContentType.MOVIE).count(), 0)


class OfferDeactivateTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(slug="prime-video", defaults={"name": "Prime Video"})
        self.country, _ = Country.objects.get_or_create(
            code="NL", defaults={"name": "Netherlands"}
        )
        self.it = Country.objects.create(code="IT", name="Italy")
        self.source = Source.objects.create(slug="justwatch", name="JustWatch", type="aggregator")
        self.repo = OfferRepository()
        self.runs = SyncRunRepository()

    def _payload(self, **overrides):
        payload = {
            "provider": self.provider,
            "country": self.country,
            "source": self.source,
            "content_type": ContentType.MOVIE,
            "tmdb_id": 550,
            "offer_type": OfferType.SUBSCRIPTION,
        }
        payload.update(overrides)
        return payload

    def _active_offer(self, **overrides):
        offer, _ = self.repo.upsert(**self._payload(**overrides), now=timezone.now())
        return offer

    def _authoritative_run(self):
        run = self.runs.start(
            provider=self.provider,
            source=self.source,
            mode=SyncMode.FULL,
            country=self.country,
            authoritative=True,
        )
        return self.runs.mark_succeeded(run)

    def test_deactivate_refuses_every_non_authoritative_state(self):
        offer = self._active_offer(provider_content_id="A1")
        cases = [
            {"status": SyncStatus.PENDING, "authoritative": False},
            {"status": SyncStatus.RUNNING, "authoritative": True},
            {"status": SyncStatus.FAILED, "authoritative": True},
            {"status": SyncStatus.SUCCEEDED, "authoritative": False},
        ]
        for case in cases:
            with self.subTest(**case):
                run = SyncRun.objects.create(
                    provider=self.provider,
                    source=self.source,
                    country=self.country,
                    mode=SyncMode.FULL,
                    **case,
                )
                with self.assertRaises(InactiveSyncError):
                    self.repo.deactivate_unseen(run, set())
                offer.refresh_from_db()
                self.assertTrue(offer.currently_available)

    def test_successful_authoritative_run_deactivates_unseen_without_deleting(self):
        seen = self._active_offer(provider_content_id="SEEN1")
        unseen = self._active_offer(provider_content_id="UNSEEN1")
        unseen_last_seen = Offer.objects.get(pk=unseen.pk).last_seen

        run = self._authoritative_run()
        deactivated = self.repo.deactivate_unseen(run, {seen.identity})

        self.assertEqual(deactivated, 1)
        unseen.refresh_from_db()
        seen.refresh_from_db()
        self.assertFalse(unseen.currently_available)
        self.assertTrue(seen.currently_available)
        # Row survives and last_seen does not move on deactivation.
        self.assertTrue(Offer.objects.filter(pk=unseen.pk).exists())
        self.assertEqual(unseen.last_seen, unseen_last_seen)
        self.assertEqual(unseen.sync_run_id, run.pk)

    def test_deactivation_is_scoped_to_run_country(self):
        self._active_offer(provider_content_id="NL1")
        self._active_offer(country=self.it, provider_content_id="IT1")

        run = self._authoritative_run()
        deactivated = self.repo.deactivate_unseen(run, set())

        self.assertEqual(deactivated, 1)
        nl_offer = Offer.objects.get(provider_content_id="NL1")
        it_offer = Offer.objects.get(provider_content_id="IT1")
        self.assertFalse(nl_offer.currently_available)
        self.assertTrue(it_offer.currently_available)

    def test_reappearing_offer_reactivates_existing_row(self):
        original, _ = self.repo.upsert(
            **self._payload(provider_content_id="GONE"), now=timezone.now()
        )
        original_first_seen = original.first_seen

        run = self._authoritative_run()
        self.repo.deactivate_unseen(run, set())
        original.refresh_from_db()
        self.assertFalse(original.currently_available)

        reappeared, created = self.repo.upsert(
            **self._payload(provider_content_id="GONE"), now=timezone.now()
        )
        self.assertFalse(created)
        self.assertEqual(reappeared.pk, original.pk)
        reappeared.refresh_from_db()
        self.assertTrue(reappeared.currently_available)
        self.assertEqual(reappeared.first_seen, original_first_seen)
        self.assertEqual(Offer.objects.count(), 1)

    def test_identity_matches_model_helper(self):
        offer = self._active_offer(provider_content_id="ASIN1")
        expected = build_offer_identity(
            "prime-video", "NL", "movie", 550, None, None, "subscription", "ASIN1"
        )
        self.assertEqual(offer.identity, expected)


class MappingRepositoryTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(slug="bbc-iplayer", defaults={"name": "BBC iPlayer"})
        self.source = Source.objects.create(slug="bbc-api", name="BBC API", type="api")
        self.repo = MappingRepository()

    def _record(self, **overrides):
        payload = {
            "provider": self.provider,
            "source": self.source,
            "provider_content_id": "pid-1",
            "content_type": ContentType.SERIES,
            "tmdb_id": 100,
            "confidence": 0.9,
            "match_method": MatchMethod.EXTERNAL_ID,
        }
        payload.update(overrides)
        return self.repo.record(**payload)

    def test_new_mapping_created(self):
        now = timezone.now()
        mapping, created = self._record(tmdb_id=2316, confidence=1.0,
                                        match_method=MatchMethod.PROVIDER_MAPPING, now=now)
        self.assertTrue(created)
        self.assertEqual(mapping.tmdb_id, 2316)
        self.assertEqual(mapping.last_verified, now)
        self.assertIsNotNone(mapping.first_seen)

    def test_fuzzy_never_overwrites_manual_mapping(self):
        self._record(tmdb_id=100, confidence=1.0, match_method=MatchMethod.MANUAL)
        mapping, changed = self._record(
            tmdb_id=999, confidence=0.82, match_method=MatchMethod.FUZZY_TITLE_YEAR
        )
        self.assertFalse(changed)
        mapping.refresh_from_db()
        self.assertEqual(mapping.tmdb_id, 100)
        self.assertEqual(mapping.match_method, MatchMethod.MANUAL)

    def test_higher_confidence_overwrites(self):
        self._record(tmdb_id=100, confidence=0.7, match_method=MatchMethod.TITLE_YEAR)
        now = timezone.now()
        mapping, changed = self._record(
            tmdb_id=200, confidence=0.9, match_method=MatchMethod.EXTERNAL_ID, now=now
        )
        self.assertTrue(changed)
        mapping.refresh_from_db()
        self.assertEqual(mapping.tmdb_id, 200)
        self.assertEqual(mapping.confidence, 0.9)
        self.assertEqual(mapping.match_method, MatchMethod.EXTERNAL_ID)
        self.assertEqual(mapping.last_verified, now)

    def test_lower_confidence_is_ignored(self):
        self._record(tmdb_id=100, confidence=0.9, match_method=MatchMethod.EXTERNAL_ID)
        mapping, changed = self._record(
            tmdb_id=300, confidence=0.7, match_method=MatchMethod.FUZZY_TITLE_YEAR
        )
        self.assertFalse(changed)
        mapping.refresh_from_db()
        self.assertEqual(mapping.tmdb_id, 100)

    def test_equal_confidence_same_target_only_bumps_last_verified(self):
        first_seen = timezone.now() - timedelta(days=1)
        self._record(tmdb_id=100, confidence=0.9,
                     match_method=MatchMethod.EXTERNAL_ID, now=first_seen)
        second = timezone.now()
        mapping, changed = self._record(
            tmdb_id=100, confidence=0.9, match_method=MatchMethod.EXTERNAL_ID, now=second
        )
        self.assertTrue(changed)
        mapping.refresh_from_db()
        self.assertEqual(mapping.tmdb_id, 100)
        self.assertEqual(mapping.last_verified, second)

    def test_equal_confidence_different_target_is_ignored(self):
        self._record(tmdb_id=100, confidence=0.9, match_method=MatchMethod.EXTERNAL_ID)
        mapping, changed = self._record(
            tmdb_id=200, confidence=0.9, match_method=MatchMethod.TITLE_YEAR
        )
        self.assertFalse(changed)
        mapping.refresh_from_db()
        self.assertEqual(mapping.tmdb_id, 100)

    def _content(self, provider_content_id="pid-1"):
        return ProviderContent.objects.create(
            provider=self.provider,
            source=self.source,
            provider_content_id=provider_content_id,
            content_type=ContentType.SERIES,
            title="Sherlock",
        )

    def test_record_links_the_provider_content_row_it_resolved(self):
        content = self._content()
        mapping, created = self._record(content=content)
        self.assertTrue(created)
        self.assertEqual(mapping.content_id, content.pk)
        self.assertEqual(content.mappings.count(), 1)

    def test_record_links_without_touching_a_preserved_manual_mapping(self):
        self._record(tmdb_id=100, confidence=1.0, match_method=MatchMethod.MANUAL)
        content = self._content()
        mapping, changed = self._record(
            tmdb_id=999, confidence=0.9, match_method=MatchMethod.TITLE_YEAR,
            content=content,
        )
        # The link is provenance, not precedence: the mapping still refuses
        # the lower-confidence offer, but now points at the row it is about.
        self.assertFalse(changed)
        mapping.refresh_from_db()
        self.assertEqual(mapping.tmdb_id, 100)
        self.assertEqual(mapping.match_method, MatchMethod.MANUAL)
        self.assertEqual(mapping.content_id, content.pk)

    def test_backfilling_a_link_never_changes_the_mapping_result(self):
        self._record(tmdb_id=100, confidence=0.9, match_method=MatchMethod.EXTERNAL_ID)
        content = self._content()
        mapping, changed = self._record(
            tmdb_id=999, confidence=0.7, match_method=MatchMethod.FUZZY_TITLE_YEAR,
            content=content,
        )
        self.assertFalse(changed)
        mapping.refresh_from_db()
        self.assertEqual(mapping.tmdb_id, 100)
        self.assertEqual(mapping.content_id, content.pk)


class ProviderContentRepositoryTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(
            slug="cbc_gem", defaults={"name": "CBC Gem"}
        )
        self.source = Source.objects.create(slug="cbc-gem-api", name="CBC API", type="api")
        self.other = Source.objects.create(slug="other-api", name="Other API", type="api")
        self.repo = ProviderContentRepository()

    def _payload(self, **overrides):
        payload = {
            "provider": self.provider,
            "source": self.source,
            "provider_content_id": "beirut",
            "content_type": ContentType.MOVIE,
            "title": "Beirut",
            "release_date": date(2018, 1, 1),
            "provider_url": "https://gem.cbc.ca/beirut",
            "metadata": {"poster": "poster.jpg"},
        }
        payload.update(overrides)
        return payload

    def test_upsert_creates_a_row_keyed_on_provider_native_identity(self):
        now = timezone.now()
        content, created = self.repo.upsert(**self._payload(), now=now)
        self.assertTrue(created)
        self.assertEqual(content.identity, "cbc_gem|beirut|movie||")
        self.assertEqual(
            content.identity,
            build_provider_content_identity("cbc_gem", "beirut", ContentType.MOVIE),
        )
        self.assertEqual(content.first_seen, now)
        self.assertEqual(content.last_seen, now)
        self.assertTrue(content.currently_available)
        self.assertIsNone(content.season_number)
        self.assertIsNone(content.episode_number)

    def test_upsert_converges_on_one_row_and_keeps_first_seen(self):
        first = timezone.now() - timedelta(hours=5)
        second = timezone.now()
        row, created_first = self.repo.upsert(**self._payload(), now=first)
        duplicate, created_second = self.repo.upsert(
            **self._payload(title="Beirut", metadata={}), now=second
        )

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(duplicate.pk, row.pk)
        self.assertEqual(ProviderContent.objects.count(), 1)
        self.assertEqual(row.first_seen, first)
        row.refresh_from_db()
        self.assertEqual(row.first_seen, first)
        self.assertEqual(row.last_seen, second)

    def test_sparse_observation_never_erases_what_was_already_seen(self):
        row, _ = self.repo.upsert(**self._payload(), now=timezone.now())
        # A refresh that carries no title/url/metadata is not evidence that
        # those values disappeared upstream.
        self.repo.upsert(
            **self._payload(title="", provider_url="", metadata=None),
            now=timezone.now() + timedelta(minutes=1),
        )
        row.refresh_from_db()
        self.assertEqual(row.title, "Beirut")
        self.assertEqual(row.provider_url, "https://gem.cbc.ca/beirut")
        self.assertEqual(row.metadata, {"poster": "poster.jpg"})

    def test_metadata_merges_shallowly_across_refreshes(self):
        row, _ = self.repo.upsert(**self._payload(), now=timezone.now())
        self.repo.upsert(
            **self._payload(metadata={"description": "a film", "poster": "new.jpg"}),
            now=timezone.now(),
        )
        row.refresh_from_db()
        self.assertEqual(
            row.metadata,
            {"poster": "new.jpg", "description": "a film"},
        )

    def test_season_slots_distinguish_rows_under_one_provider_id(self):
        self.repo.upsert(**self._payload(content_type=ContentType.SERIES),
                         now=timezone.now())
        first, created_first = self.repo.upsert(
            **self._payload(content_type=ContentType.SEASON, season_number=1),
            now=timezone.now(),
        )
        second, created_second = self.repo.upsert(
            **self._payload(content_type=ContentType.SEASON, season_number=2),
            now=timezone.now(),
        )
        self.assertTrue(created_first)
        self.assertTrue(created_second)
        self.assertNotEqual(first.identity, second.identity)
        self.assertEqual(ProviderContent.objects.count(), 3)

    def test_source_is_provenance_not_identity(self):
        first, created_first = self.repo.upsert(**self._payload(), now=timezone.now())
        second, created_second = self.repo.upsert(
            **self._payload(source=self.other), now=timezone.now()
        )

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(ProviderContent.objects.count(), 1)
        second.refresh_from_db()
        self.assertEqual(second.source_id, self.other.pk)

    def test_get_resolves_the_row_by_identity_or_none(self):
        row, _ = self.repo.upsert(**self._payload(), now=timezone.now())
        found = self.repo.get(
            provider=self.provider,
            provider_content_id="beirut",
            content_type=ContentType.MOVIE,
        )
        missing = self.repo.get(
            provider=self.provider,
            provider_content_id="nope",
            content_type=ContentType.MOVIE,
        )
        self.assertEqual(found.pk, row.pk)
        self.assertIsNone(missing)

    def test_identity_slots_must_be_valid(self):
        with self.assertRaisesMessage(ValueError, "Invalid content type"):
            self.repo.upsert(**self._payload(content_type="trailer"))
        with self.assertRaisesMessage(ValueError, "season number"):
            self.repo.upsert(**self._payload(content_type=ContentType.SEASON))
        with self.assertRaisesMessage(ValueError, "episode number"):
            self.repo.upsert(
                **self._payload(content_type=ContentType.EPISODE, season_number=1)
            )
        with self.assertRaisesMessage(ValueError, "negative"):
            self.repo.upsert(
                **self._payload(content_type=ContentType.SEASON, season_number=-1)
            )
        self.assertEqual(ProviderContent.objects.count(), 0)


class ProviderContentRepositoryReadTests(TestCase):
    """Phase 8 read surface: filters, ordering, mapping and offer presence."""

    def setUp(self):
        self.cbc, _ = Provider.objects.get_or_create(
            slug="cbc_gem", defaults={"name": "CBC Gem"}
        )
        self.prime, _ = Provider.objects.get_or_create(
            slug="prime-video", defaults={"name": "Prime Video"}
        )
        self.source = Source.objects.create(slug="cbc-gem-api", name="CBC API", type="api")
        self.country, _ = Country.objects.get_or_create(
            code="CA", defaults={"name": "Canada"}
        )
        self.offers = OfferRepository()
        self.repo = ProviderContentRepository()

    def _row(self, provider_content_id, *, provider=None, content_type=ContentType.MOVIE,
             title=None, **overrides):
        payload = {
            "provider": provider or self.cbc,
            "source": self.source,
            "provider_content_id": provider_content_id,
            "content_type": content_type,
            "title": provider_content_id if title is None else title,
        }
        payload.update(overrides)
        row, _created = self.repo.upsert(**payload, now=timezone.now())
        return row

    def _offer(self, *, provider, provider_content_id, content_type=ContentType.MOVIE,
               tmdb_id=550, season_number=None, episode_number=None,
               currently_available=True):
        offer, _created = self.offers.upsert(
            provider=provider,
            country=self.country,
            source=self.source,
            content_type=content_type,
            tmdb_id=tmdb_id,
            season_number=season_number,
            episode_number=episode_number,
            offer_type=OfferType.SUBSCRIPTION,
            provider_content_id=provider_content_id,
            now=timezone.now(),
        )
        if not currently_available:
            Offer.objects.filter(pk=offer.pk).update(currently_available=False)
        return offer

    def test_list_filters_by_provider_and_provider_native_id(self):
        beirut = self._row("beirut", title="Beirut")
        self._row("float", title="Float")

        rows = self.repo.list(provider=self.cbc, provider_content_id="beirut")
        self.assertEqual([row.pk for row in rows], [beirut.pk])

    def test_get_resolves_the_full_identity_including_season_slots(self):
        series = self._row("show", content_type=ContentType.SERIES)
        season = self._row("show", content_type=ContentType.SEASON, season_number=2)
        episode = self._row(
            "show", content_type=ContentType.EPISODE,
            season_number=2, episode_number=5,
        )

        self.assertEqual(
            self.repo.get(provider=self.cbc, provider_content_id="show",
                          content_type=ContentType.SERIES).pk,
            series.pk,
        )
        self.assertEqual(
            self.repo.get(provider=self.cbc, provider_content_id="show",
                          content_type=ContentType.SEASON, season_number=2).pk,
            season.pk,
        )
        self.assertIsNone(
            self.repo.get(provider=self.cbc, provider_content_id="show",
                          content_type=ContentType.SEASON, season_number=3)
        )
        self.assertEqual(
            self.repo.get(provider=self.cbc, provider_content_id="show",
                          content_type=ContentType.EPISODE,
                          season_number=2, episode_number=5).pk,
            episode.pk,
        )
        self.assertIsNone(
            self.repo.get(provider=self.cbc, provider_content_id="show",
                          content_type=ContentType.EPISODE,
                          season_number=2, episode_number=6)
        )

    def test_identical_native_ids_across_providers_stay_separate(self):
        cbc_row = self._row("123", title="One")
        prime_row = self._row("123", title="Two", provider=self.prime)

        self.assertNotEqual(cbc_row.identity, prime_row.identity)
        self.assertEqual(
            [row.pk for row in self.repo.list(provider=self.cbc, provider_content_id="123")],
            [cbc_row.pk],
        )
        self.assertEqual(
            [row.pk for row in self.repo.list(provider=self.prime, provider_content_id="123")],
            [prime_row.pk],
        )
        self.assertEqual(
            [row.pk for row in self.repo.list(provider_content_id="123")],
            sorted([cbc_row.pk, prime_row.pk]),
        )
        # Bulk mapping lookup is keyed per provider too — one provider's
        # mapping never leaks onto the other's row.
        MappingRepository().record(
            provider=self.prime,
            source=self.source,
            provider_content_id="123",
            content_type=ContentType.MOVIE,
            tmdb_id=4242,
            confidence=1.0,
            match_method=MatchMethod.MANUAL,
        )
        self.assertEqual(self.repo.tmdb_ids_for([cbc_row]), {})
        self.assertEqual(
            self.repo.tmdb_ids_for([prime_row]), {(self.prime.pk, "123"): 4242}
        )

    def test_list_filters_by_content_type(self):
        movie = self._row("story", content_type=ContentType.MOVIE)
        self._row("story", content_type=ContentType.SERIES)
        self._row("story", content_type=ContentType.SEASON, season_number=1)
        self._row("story", content_type=ContentType.EPISODE,
                  season_number=1, episode_number=1)

        rows = self.repo.list(provider=self.cbc, content_type=ContentType.MOVIE)
        self.assertEqual([row.pk for row in rows], [movie.pk])
        self.assertEqual(self.repo.list(provider=self.cbc).count(), 4)

    def test_list_filters_by_season_and_episode_slots(self):
        self._row("show", content_type=ContentType.SERIES)
        first = self._row("show", content_type=ContentType.SEASON, season_number=1)
        second = self._row("show", content_type=ContentType.SEASON, season_number=2)
        episode = self._row("show", content_type=ContentType.EPISODE,
                            season_number=1, episode_number=3)

        seasons = self.repo.list(provider=self.cbc, content_type=ContentType.SEASON)
        self.assertEqual([row.pk for row in seasons], [first.pk, second.pk])
        self.assertEqual(
            [row.pk for row in self.repo.list(
                provider=self.cbc, content_type=ContentType.SEASON, season_number=2
            )],
            [second.pk],
        )
        self.assertEqual(
            [row.pk for row in self.repo.list(
                provider=self.cbc, content_type=ContentType.EPISODE,
                season_number=1, episode_number=3,
            )],
            [episode.pk],
        )
        self.assertEqual(
            list(self.repo.list(
                provider=self.cbc, content_type=ContentType.EPISODE, season_number=2
            )),
            [],
        )

    def test_list_filters_by_current_observation(self):
        active = self._row("active", title="Active")
        stale = self._row("stale", title="Stale")
        ProviderContent.objects.filter(pk=stale.pk).update(currently_available=False)

        self.assertEqual(
            [row.pk for row in self.repo.list(provider=self.cbc, currently_available=True)],
            [active.pk],
        )
        self.assertEqual(
            [row.pk for row in self.repo.list(provider=self.cbc, currently_available=False)],
            [stale.pk],
        )

    def test_search_is_case_insensitive_and_matches_partial_titles(self):
        beirut = self._row("beirut", title="Beirut")
        self._row("float", title="Float")
        self._row("other", title="A Beiruti Summer", provider=self.prime)

        rows = self.repo.search_by_title("BEIRU")
        self.assertEqual([row.title for row in rows], ["A Beiruti Summer", "Beirut"])
        # The provider filter narrows the same way list() does.
        self.assertEqual(
            [row.pk for row in self.repo.search_by_title("BEIRU", provider=self.cbc)],
            [beirut.pk],
        )
        self.assertEqual(
            list(self.repo.search_by_title("beiruti", provider=self.cbc)), []
        )
        self.assertEqual(list(self.repo.search_by_title("zzz-nothing")), [])

    def test_ordering_is_deterministic(self):
        self._row("zeta", title="Zeta")
        self._row("alpha", title="Alpha")
        self._row("mid", title="Mid")
        expected = sorted(
            row.identity for row in ProviderContent.objects.all()
        )

        first = [row.identity for row in self.repo.list()]
        second = [row.identity for row in self.repo.list()]
        self.assertEqual(first, expected)
        self.assertEqual(second, expected)

        matching = [
            row for row in ProviderContent.objects.all() if "a" in row.title.lower()
        ]
        search_expected = [
            (row.title, row.identity)
            for row in sorted(matching, key=lambda row: (row.title, row.identity))
        ]
        self.assertEqual(
            [(row.title, row.identity) for row in self.repo.search_by_title("a")],
            search_expected,
        )
        self.assertEqual(
            [(row.title, row.identity) for row in self.repo.search_by_title("a")],
            search_expected,
        )

    def test_tmdb_ids_for_reports_mapped_and_unresolved_rows(self):
        mapped = self._row("mapped", title="Mapped")
        unresolved = self._row("unresolved", title="Unresolved")
        MappingRepository().record(
            provider=self.cbc,
            source=self.source,
            provider_content_id="mapped",
            content_type=ContentType.MOVIE,
            tmdb_id=550,
            confidence=1.0,
            match_method=MatchMethod.MANUAL,
        )

        ids = self.repo.tmdb_ids_for([mapped, unresolved])
        self.assertEqual(ids, {(self.cbc.pk, "mapped"): 550})
        self.assertNotIn((self.cbc.pk, "unresolved"), ids)
        self.assertEqual(self.repo.tmdb_ids_for([]), {})

    def test_active_offer_counts_are_keyed_by_provider_native_id(self):
        counted = self._row("story", content_type=ContentType.SERIES, title="Story")
        self._row("bare", title="Bare")
        other_provider_row = self._row("story", provider=self.prime,
                                       content_type=ContentType.SERIES, title="Story")

        self._offer(provider=self.cbc, provider_content_id="story",
                    content_type=ContentType.SERIES, tmdb_id=900)
        self._offer(provider=self.cbc, provider_content_id="story",
                    content_type=ContentType.SEASON, season_number=1, tmdb_id=900)
        self._offer(provider=self.cbc, provider_content_id="bare",
                    tmdb_id=901, currently_available=False)
        # A different provider publishing the same native id must not bleed in.
        self._offer(provider=self.prime, provider_content_id="story",
                    content_type=ContentType.SERIES, tmdb_id=902)

        counts = self.repo.active_offer_counts([counted, other_provider_row])
        # Series row counts the active offers carrying its provider-native id
        # (series + season), at any season granularity.
        self.assertEqual(counts[(self.cbc.pk, "story")], 2)
        self.assertEqual(counts[(self.prime.pk, "story")], 1)
        # Deactivated offers are not active presence.
        bare = self.repo.get(provider=self.cbc, provider_content_id="bare",
                             content_type=ContentType.MOVIE)
        self.assertEqual(self.repo.active_offer_counts([bare]), {})
        self.assertEqual(self.repo.active_offer_counts([]), {})

    def test_repeated_refreshes_never_duplicate_rows_in_reads(self):
        first, created_first = self.repo.upsert(
            provider=self.cbc,
            source=self.source,
            provider_content_id="beirut",
            content_type=ContentType.MOVIE,
            title="Beirut",
            now=timezone.now(),
        )
        second, created_second = self.repo.upsert(
            provider=self.cbc,
            source=self.source,
            provider_content_id="beirut",
            content_type=ContentType.MOVIE,
            title="Beirut",
            now=timezone.now() + timedelta(minutes=5),
        )

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(self.repo.list(provider=self.cbc).count(), 1)
        self.assertEqual(self.repo.search_by_title("beirut").count(), 1)


class SyncRunRepositoryTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(slug="pluto-tv", defaults={"name": "Pluto TV"})
        self.source = Source.objects.create(slug="pluto-api", name="Pluto API", type="api")
        self.repo = SyncRunRepository()

    def test_start_marks_running(self):
        run = self.repo.start(provider=self.provider, source=self.source, mode=SyncMode.FULL)
        self.assertEqual(run.status, SyncStatus.RUNNING)
        self.assertIsNotNone(run.started_at)
        self.assertIsNone(run.completed_at)
        self.assertFalse(run.authoritative)

    def test_start_can_declare_authoritative(self):
        run = self.repo.start(
            provider=self.provider, source=self.source, mode=SyncMode.FULL, authoritative=True
        )
        self.assertTrue(run.authoritative)

    def test_mark_succeeded_records_counters(self):
        run = self.repo.start(provider=self.provider, source=self.source, mode=SyncMode.FULL)
        self.repo.mark_succeeded(
            run, items_discovered=10, items_processed=10, offers_created=3, offers_updated=7
        )
        run.refresh_from_db()
        self.assertEqual(run.status, SyncStatus.SUCCEEDED)
        self.assertIsNotNone(run.completed_at)
        self.assertEqual(run.items_discovered, 10)
        self.assertEqual(run.offers_created, 3)
        self.assertEqual(run.offers_updated, 7)

    def test_mark_failed_records_error(self):
        run = self.repo.start(
            provider=self.provider, source=self.source, mode=SyncMode.INCREMENTAL
        )
        self.repo.mark_failed(run, "HTTP 503")
        run.refresh_from_db()
        self.assertEqual(run.status, SyncStatus.FAILED)
        self.assertEqual(run.last_error, "HTTP 503")
        self.assertEqual(run.error_count, 1)
        self.assertIsNotNone(run.completed_at)


class CursorRepositoryTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(slug="bbc-iplayer", defaults={"name": "BBC iPlayer"})
        self.source = Source.objects.create(slug="bbc-api", name="BBC API", type="api")
        self.country = Country.objects.create(code="GB", name="United Kingdom")
        self.repo = CursorRepository()

    def test_get_returns_none_when_missing(self):
        self.assertIsNone(self.repo.get(self.provider, self.source, self.country))

    def test_set_then_get_round_trip(self):
        now = timezone.now()
        self.repo.set(self.provider, self.source, self.country, "cursor-1", now=now)
        cursor = self.repo.get(self.provider, self.source, self.country)
        self.assertEqual(cursor.cursor, "cursor-1")
        self.assertEqual(cursor.last_successful_sync, now)

    def test_set_overwrites_existing_cursor(self):
        self.repo.set(self.provider, self.source, self.country, "cursor-1")
        updated = timezone.now()
        self.repo.set(self.provider, self.source, self.country, "cursor-2", now=updated)
        cursor = self.repo.get(self.provider, self.source, self.country)
        self.assertEqual(cursor.cursor, "cursor-2")
        self.assertEqual(cursor.last_successful_sync, updated)
        self.assertEqual(SyncCursor.objects.count(), 1)
