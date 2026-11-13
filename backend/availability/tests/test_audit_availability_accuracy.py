from types import SimpleNamespace
import csv
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.management.base import CommandError
from django.core.management import call_command
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone

from availability.management.commands.audit_availability_accuracy import (
    _classify,
    _parse_cohort,
    _provider_access,
)
from availability.domain.enums import ContentType
from availability.models import Country, Offer as CanonicalOffer, Source
from tracker.models import Movie, Preference, Provider, ProviderCategory
from tracker.services.access import AccessVerdict, resolve_title
from tracker.services.offers import Offer


class CohortValidationTests(SimpleTestCase):
    def test_input_is_normalized_and_ordered_deterministically(self):
        parsed = _parse_cohort([
            {"tmdb_id": "20", "title": "B", "media_type": "TV", "year": "2020", "country": "nl"},
            {"tmdb_id": "10", "title": "A", "media_type": "movie", "year": "1990", "country": "it"},
        ])
        self.assertEqual([(r["tmdb_id"], r["country"]) for r in parsed], [(10, "IT"), (20, "NL")])
        self.assertEqual(parsed[1]["media_type"], ContentType.SERIES)

    def test_rejects_invalid_country_and_duplicate_identity(self):
        with self.assertRaises(CommandError):
            _parse_cohort([{"tmdb_id": "1", "title": "A", "media_type": "movie", "year": "2000", "country": "NLD"}])
        row = {"tmdb_id": "1", "title": "A", "media_type": "movie", "year": "2000", "country": "NL"}
        with self.assertRaises(CommandError):
            _parse_cohort([row, row])

    def test_rejects_invalid_tmdb_identity_or_media_type(self):
        row = {"tmdb_id": "x", "title": "A", "media_type": "movie", "year": "2000", "country": "NL"}
        with self.assertRaises(CommandError):
            _parse_cohort([row])
        row["tmdb_id"] = "1"
        row["media_type"] = "episode"
        with self.assertRaises(CommandError):
            _parse_cohort([row])

    def test_empty_cohort_is_valid(self):
        self.assertEqual(_parse_cohort([]), [])


class ComparisonClassificationTests(SimpleTestCase):
    def setUp(self):
        self.case = {"tmdb_id": 10, "title": "A", "media_type": "movie", "year": 2000, "country": "NL"}

    def test_secondary_positive_does_not_turn_missing_app_row_into_conflict(self):
        external = {"tmdb_id": "10", "country": "NL", "explicitly_confirms_availability": "Yes", "provider": "Netflix"}
        result, _ = _classify(self.case, external, None, [], False)
        self.assertEqual(result, "APP_DATA_MISSING")

    def test_providerless_or_unverified_external_evidence_is_insufficient(self):
        external = {"tmdb_id": "10", "country": "NL", "explicitly_confirms_availability": "Yes", "provider": "not extractable"}
        result, _ = _classify(self.case, external, object(), [], False)
        self.assertEqual(result, "INSUFFICIENT_EXTERNAL_EVIDENCE")
        external["explicitly_confirms_availability"] = "No"
        result, _ = _classify(self.case, external, object(), [], False)
        self.assertEqual(result, "INSUFFICIENT_EXTERNAL_EVIDENCE")

    def test_country_mismatch_is_not_comparable(self):
        external = {"tmdb_id": "10", "country": "IT", "explicitly_confirms_availability": "Yes", "provider": "Netflix"}
        result, _ = _classify(self.case, external, object(), [], False)
        self.assertEqual(result, "NOT_COMPARABLE")

    def test_media_type_and_year_mismatch_are_not_comparable(self):
        external = {"tmdb_id": "10", "country": "NL", "media_type": "series", "year": "2000",
                    "explicitly_confirms_availability": "Yes", "provider": "Netflix"}
        result, _ = _classify(self.case, external, object(), [], False)
        self.assertEqual(result, "NOT_COMPARABLE")
        external["media_type"] = "movie"
        external["year"] = "1984"
        result, _ = _classify(self.case, external, object(), [], False)
        self.assertEqual(result, "NOT_COMPARABLE")

    def test_provider_mismatch_with_secondary_aggregator_stays_unconfirmed(self):
        external = {"tmdb_id": "10", "country": "NL", "explicitly_confirms_availability": "Yes", "provider": "Netflix"}
        app_offer = SimpleNamespace(currently_available=True, evidence="verified", country="NL", provider="prime")
        result, _ = _classify(self.case, external, object(), [app_offer], False)
        self.assertEqual(result, "INSUFFICIENT_APP_EVIDENCE")

    def test_stale_rows_and_disabled_source_are_not_confirmed_errors(self):
        external = {"tmdb_id": "10", "country": "NL", "explicitly_confirms_availability": "Yes", "provider": "Netflix"}
        stale_offer = SimpleNamespace(currently_available=True, evidence="stale", country="NL", provider="netflix")
        result, _ = _classify(self.case, external, object(), [stale_offer], False)
        self.assertEqual(result, "APP_DATA_STALE")
        result, _ = _classify(self.case, external, object(), [], True)
        self.assertEqual(result, "APP_SOURCE_DISABLED")

    def test_unknown_app_territory_cannot_agree_with_a_country_listing(self):
        external = {"tmdb_id": "10", "country": "NL", "explicitly_confirms_availability": "Yes", "provider": "Netflix"}
        unknown_scope = SimpleNamespace(currently_available=True, evidence="verified", country="", provider="netflix")
        result, _ = _classify(self.case, external, object(), [unknown_scope], False)
        self.assertEqual(result, "INSUFFICIENT_APP_EVIDENCE")

    def test_authoritative_negative_conflicts_only_with_verified_same_provider_offer(self):
        external = {"tmdb_id": "10", "country": "NL", "explicitly_confirms_unavailability": "Yes",
                    "evidence_authority": "primary", "provider": "Netflix"}
        current = SimpleNamespace(currently_available=True, evidence="verified", country="NL", provider="netflix")
        result, _ = _classify(self.case, external, object(), [current], False)
        self.assertEqual(result, "CONFLICT_CONFIRMED")
        external["evidence_authority"] = "secondary"
        result, _ = _classify(self.case, external, object(), [current], False)
        self.assertEqual(result, "INSUFFICIENT_EXTERNAL_EVIDENCE")


class SyntheticProfilesTests(SimpleTestCase):
    def test_no_subscriptions_profile_does_not_grant_paid_subscription(self):
        provider = Provider(slug="netflix", name="Netflix", category=ProviderCategory.OFFICIAL, is_enabled=True)
        access = _provider_access([provider], [SimpleNamespace(provider="netflix", offer_type="subscription")], "B")
        preference = Preference(current_country="NL", vpn_enabled=False,
                               ranking_preferences={"source_priority": ["official"]})
        result = resolve_title(SimpleNamespace(id=1), [Offer(country="NL", service="netflix", offer_type="subscription")],
                               preference, current_country="NL", access=access,
                               provider_map={"netflix": provider})
        self.assertEqual(result.verdict, AccessVerdict.AVAILABLE_ELSEWHERE)

    def test_all_relevant_profile_respects_addon_base_requirement(self):
        base = Provider(slug="prime", name="Prime Video", category=ProviderCategory.OFFICIAL, is_enabled=True)
        channel = Provider(slug="paramount_amazon", name="Paramount+ Amazon Channel",
                           category=ProviderCategory.OFFICIAL, is_enabled=True,
                           config={"addon_parent": "prime"})
        access = _provider_access([base, channel],
                                  [SimpleNamespace(provider="paramount_amazon", offer_type="subscription")], "C")
        self.assertIn("prime", access.included)
        self.assertIn("paramount_amazon", access.addon)

    def test_profile_country_and_unknown_language_are_not_manufactured(self):
        provider = Provider(slug="netflix", name="Netflix", category=ProviderCategory.OFFICIAL, is_enabled=True)
        access = _provider_access([provider], [SimpleNamespace(provider="netflix", offer_type="subscription")], "C")
        preference = Preference(current_country="NL", vpn_enabled=False,
                               ranking_preferences={"source_priority": ["official"]})
        result = resolve_title(SimpleNamespace(id=1), [Offer(country="IT", service="netflix", offer_type="subscription")],
                               preference, current_country="NL", access=access,
                               provider_map={"netflix": provider})
        self.assertEqual(result.verdict, AccessVerdict.AVAILABLE_ELSEWHERE)
        self.assertIsNone(result.offers[0].is_language_match)
        self.assertEqual(result.offers[0].audio_languages, [])


class ReadOnlyAuditPostgreSQLTests(TransactionTestCase):
    """Exercise the command against committed PostgreSQL fixtures and a read-only transaction."""

    reset_sequences = True

    def test_command_emits_comparable_snapshot_without_mutating_fixture_rows_or_http(self):
        movie = Movie.objects.create(tmdb_id=987654, title="Audit fixture", year=2000, content_type="movie")
        provider, _ = Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "Netflix", "category": ProviderCategory.OFFICIAL}
        )
        source, _ = Source.objects.get_or_create(
            slug="justwatch", defaults={"name": "JustWatch", "enabled": True}
        )
        country, _ = Country.objects.get_or_create(code="NL", defaults={"name": "Netherlands", "enabled": True})
        CanonicalOffer.objects.create(
            provider=provider, source=source, country=country, content_type="movie",
            tmdb_id=movie.tmdb_id, offer_type="subscription", provider_content_id="audit-fixture",
            last_seen=timezone.now(), currently_available=True, identity="",
        )
        second_provider, _ = Provider.objects.get_or_create(
            slug="prime", defaults={"name": "Prime Video", "category": ProviderCategory.OFFICIAL}
        )
        CanonicalOffer.objects.create(
            provider=second_provider, source=source, country=country, content_type="movie",
            tmdb_id=movie.tmdb_id, offer_type="rent", provider_content_id="audit-fixture-rental",
            last_seen=timezone.now(), currently_available=True, identity="",
        )
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            cohort = root / "cohort.csv"
            evidence = root / "evidence.csv"
            output = root / "out"
            with cohort.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["tmdb_id", "title", "media_type", "year", "country"])
                writer.writeheader()
                writer.writerow({"tmdb_id": movie.tmdb_id, "title": movie.title, "media_type": "movie", "year": 2000, "country": "NL"})
            with evidence.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["tmdb_id", "media_type", "country", "provider", "explicitly_confirms_availability"])
                writer.writeheader()
                writer.writerow({"tmdb_id": movie.tmdb_id, "media_type": "movie", "country": "NL", "provider": "Netflix", "explicitly_confirms_availability": "Yes"})
            before = (Movie.objects.count(), CanonicalOffer.objects.count(), Source.objects.count())
            with patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP must not be called")):
                call_command("audit_availability_accuracy", cohort=str(cohort), external_evidence=str(evidence),
                             output=str(output), offline=True, verbosity=0)
            after = (Movie.objects.count(), CanonicalOffer.objects.count(), Source.objects.count())
            self.assertEqual(before, after)
            with (output / "comparison.csv").open(newline="", encoding="utf-8") as handle:
                comparison = next(csv.DictReader(handle))
            self.assertEqual(comparison["comparison_state"], "AGREEMENT_CONFIRMED")
            self.assertEqual(comparison["app_offer_count"], "2")
            self.assertEqual(comparison["app_positive_provider_count"], "2")
