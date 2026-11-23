from types import SimpleNamespace
import csv
import tempfile
from datetime import timedelta
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

    def test_corrected_coherence_row_parses_with_pilot_schema(self):
        cases = _parse_cohort([{
            "tmdb_id": "220289", "title": "Coherence", "media_type": "movie",
            "year": "2013", "country": "NL",
        }])
        self.assertEqual(cases, [{
            "tmdb_id": 220289, "title": "Coherence", "media_type": "movie",
            "year": 2013, "country": "NL",
        }])


class ComparisonClassificationTests(SimpleTestCase):
    def test_reviewed_aliases_require_both_names_and_exact_typed_id_and_year(self):
        from availability.audit_identity import titles_match

        for tmdb_id, kind, year, left, right in [
            (619264, "movie", 2019, "the platform", "el hoyo"),
            (2316, "series", 2005, "the office us", "the office"),
        ]:
            case = {"tmdb_id": tmdb_id, "media_type": kind, "year": year}
            self.assertTrue(titles_match(case, left, right))
            self.assertFalse(titles_match(case, left, "unrelated"))
            for change in [{"tmdb_id": 2996}, {"media_type": "episode"}, {"year": year + 1}]:
                self.assertFalse(titles_match({**case, **change}, left, right))

    def setUp(self):
        self.case = {"tmdb_id": 10, "title": "A", "media_type": "movie", "year": 2000, "country": "NL"}
        self.movie = Movie(tmdb_id=10, content_type="movie", title="A", canonical_title="A", year=2000)
        self.verified_offer = SimpleNamespace(
            currently_available=True, evidence="verified", country="NL",
            provider="netflix", offer_type="subscription",
        )

    def _compare_identity(self, case, movie, external_title=None):
        external = {
            **case, "tmdb_id": str(case["tmdb_id"]), "year": str(case["year"] or ""),
            "provider": "Netflix", "offer_type": "subscription",
            "explicitly_confirms_availability": "Yes",
        }
        if external_title is not None:
            external["title"] = external_title
        return _classify(case, external, movie, [self.verified_offer])

    def test_exact_wrong_id_is_rejected_even_when_external_title_copies_cohort(self):
        case = {**self.case, "tmdb_id": 300602, "title": "Coherence", "year": 2013}
        movie = Movie(tmdb_id=300602, content_type="movie", title="Coherence",
                      canonical_title="Capture the Flag", year=2015)
        state, _ = self._compare_identity(case, movie)
        self.assertEqual(state, "NOT_COMPARABLE")

    def test_corrected_id_and_independent_coherence_identity_can_agree(self):
        case = {**self.case, "tmdb_id": 220289, "title": "Coherence", "year": 2013}
        movie = Movie(tmdb_id=220289, content_type="movie", title="My display title",
                      title_is_override=True, canonical_title="Coherence", year=2013)
        state, _ = self._compare_identity(case, movie)
        self.assertEqual(state, "AGREEMENT_CONFIRMED")

    def test_documented_localized_alias_can_agree_for_same_film_and_year(self):
        case = {**self.case, "tmdb_id": 619264, "title": "Il buco", "year": 2019}
        movie = Movie(tmdb_id=619264, content_type="movie", title="The Platform",
                      canonical_title="The Platform", year=2019)
        state, _ = self._compare_identity(case, movie, external_title="The Platform")
        self.assertEqual(state, "AGREEMENT_CONFIRMED")

    def test_unsupported_or_uncertain_identity_never_agrees(self):
        case = {**self.case, "tmdb_id": 220289, "title": "Coherence", "year": 2013}
        movie = Movie(tmdb_id=220289, content_type="movie", title="Coherence")
        for title, year, external_title in [
            ("Coherence Redux", 2013, "Coherence"),
            ("", 2013, "Coherence"),
            ("Coherence", None, "Coherence"),
            ("Coherence", 2013, "Coherence Redux"),
        ]:
            with self.subTest(title=title, year=year, external_title=external_title):
                movie.canonical_title = title
                movie.year = year
                state, _ = self._compare_identity(case, movie, external_title)
                self.assertEqual(state, "NOT_COMPARABLE")

        case = {**self.case, "tmdb_id": 20, "title": "Il buco", "year": 2019}
        movie = Movie(tmdb_id=20, content_type="movie", title="The Platform",
                      canonical_title="The Platform", year=2019)
        state, _ = self._compare_identity(case, movie)
        self.assertEqual(state, "NOT_COMPARABLE")

    def test_secondary_positive_does_not_turn_missing_app_row_into_conflict(self):
        external = {"tmdb_id": "10", "country": "NL", "explicitly_confirms_availability": "Yes", "provider": "Netflix", "offer_type": "subscription"}
        result, _ = _classify(self.case, external, None, [], False)
        self.assertEqual(result, "APP_DATA_MISSING")

    def test_providerless_or_unverified_external_evidence_is_insufficient(self):
        external = {"tmdb_id": "10", "country": "NL", "explicitly_confirms_availability": "Yes", "provider": "not extractable", "offer_type": "subscription"}
        result, _ = _classify(self.case, external, self.movie, [], False)
        self.assertEqual(result, "INSUFFICIENT_EXTERNAL_EVIDENCE")
        external["explicitly_confirms_availability"] = "No"
        result, _ = _classify(self.case, external, self.movie, [], False)
        self.assertEqual(result, "INSUFFICIENT_EXTERNAL_EVIDENCE")

    def test_country_mismatch_is_not_comparable(self):
        external = {"tmdb_id": "10", "country": "IT", "explicitly_confirms_availability": "Yes", "provider": "Netflix", "offer_type": "subscription"}
        result, _ = _classify(self.case, external, self.movie, [], False)
        self.assertEqual(result, "NOT_COMPARABLE")

    def test_media_type_and_year_mismatch_are_not_comparable(self):
        external = {"tmdb_id": "10", "country": "NL", "media_type": "series", "year": "2000",
                    "explicitly_confirms_availability": "Yes", "provider": "Netflix", "offer_type": "subscription"}
        result, _ = _classify(self.case, external, self.movie, [], False)
        self.assertEqual(result, "NOT_COMPARABLE")
        external["media_type"] = "movie"
        external["year"] = "1984"
        result, _ = _classify(self.case, external, self.movie, [], False)
        self.assertEqual(result, "NOT_COMPARABLE")

    def test_provider_mismatch_with_secondary_aggregator_stays_unconfirmed(self):
        external = {"tmdb_id": "10", "country": "NL", "explicitly_confirms_availability": "Yes", "provider": "Netflix", "offer_type": "subscription"}
        app_offer = SimpleNamespace(currently_available=True, evidence="verified", country="NL", provider="prime")
        result, _ = _classify(self.case, external, self.movie, [app_offer], False)
        self.assertEqual(result, "INSUFFICIENT_APP_EVIDENCE")

    def test_rental_offer_cannot_agree_with_external_subscription_listing(self):
        external = {"tmdb_id": "10", "country": "NL", "explicitly_confirms_availability": "Yes",
                    "provider": "Netflix", "offer_type": "subscription"}
        app_offer = SimpleNamespace(currently_available=True, evidence="verified", country="NL",
                                    provider="netflix", offer_type="rent")
        result, _ = _classify(self.case, external, self.movie, [app_offer])
        self.assertEqual(result, "INSUFFICIENT_APP_EVIDENCE")

    def test_stale_matching_provider_cannot_borrow_an_unrelated_verified_offer(self):
        external = {"tmdb_id": "10", "country": "NL", "explicitly_confirms_availability": "Yes",
                    "provider": "Netflix", "offer_type": "subscription"}
        offers = [
            SimpleNamespace(currently_available=True, evidence="stale", country="NL",
                             provider="netflix", offer_type="subscription"),
            SimpleNamespace(currently_available=True, evidence="verified", country="NL",
                             provider="prime", offer_type="subscription"),
        ]
        result, _ = _classify(self.case, external, self.movie, offers)
        self.assertEqual(result, "APP_DATA_STALE")

    def test_local_release_year_mismatch_is_not_comparable(self):
        external = {"tmdb_id": "10", "country": "NL", "year": "2000",
                    "explicitly_confirms_availability": "Yes", "provider": "Netflix", "offer_type": "subscription"}
        wrong_release = Movie(tmdb_id=10, content_type="movie", canonical_title="A", year=1984)
        verified = SimpleNamespace(currently_available=True, evidence="verified", country="NL",
                                   provider="netflix", offer_type="subscription")
        result, _ = _classify(self.case, external, wrong_release, [verified])
        self.assertEqual(result, "NOT_COMPARABLE")

    def test_stale_rows_and_disabled_source_are_not_confirmed_errors(self):
        external = {"tmdb_id": "10", "country": "NL", "explicitly_confirms_availability": "Yes", "provider": "Netflix", "offer_type": "subscription"}
        stale_offer = SimpleNamespace(currently_available=True, evidence="stale", country="NL",
                                      provider="netflix", offer_type="subscription")
        result, _ = _classify(self.case, external, self.movie, [stale_offer], False)
        self.assertEqual(result, "APP_DATA_STALE")
        result, _ = _classify(self.case, external, self.movie, [], "disabled")
        self.assertEqual(result, "APP_SOURCE_DISABLED")

    def test_external_research_source_does_not_imply_disabled_app_source(self):
        external = {"tmdb_id": "10", "country": "NL", "explicitly_confirms_availability": "Yes",
                    "provider": "Netflix", "offer_type": "subscription", "evidence_source": "JustWatch"}
        result, _ = _classify(self.case, external, self.movie, [], "unknown")
        self.assertEqual(result, "APP_DATA_MISSING")

    def test_multi_provider_multi_class_listing_cannot_form_a_cross_product_agreement(self):
        external = {"tmdb_id": "10", "country": "NL", "explicitly_confirms_availability": "Yes",
                    "provider": "Netflix, Prime Video", "offer_type": "subscription + rent/buy"}
        netflix_rent = SimpleNamespace(currently_available=True, evidence="verified", country="NL",
                                       provider="netflix", offer_type="rent")
        result, _ = _classify(self.case, external, self.movie, [netflix_rent])
        self.assertEqual(result, "INSUFFICIENT_EXTERNAL_EVIDENCE")

    def test_unknown_app_territory_cannot_agree_with_a_country_listing(self):
        external = {"tmdb_id": "10", "country": "NL", "explicitly_confirms_availability": "Yes", "provider": "Netflix", "offer_type": "subscription"}
        unknown_scope = SimpleNamespace(currently_available=True, evidence="verified", country="", provider="netflix")
        result, _ = _classify(self.case, external, self.movie, [unknown_scope], False)
        self.assertEqual(result, "INSUFFICIENT_APP_EVIDENCE")

    def test_authoritative_negative_conflicts_only_with_verified_same_provider_offer(self):
        external = {"tmdb_id": "10", "country": "NL", "explicitly_confirms_unavailability": "Yes",
                    "evidence_authority": "primary", "provider": "Netflix"}
        current = SimpleNamespace(currently_available=True, evidence="verified", country="NL", provider="netflix")
        result, _ = _classify(self.case, external, self.movie, [current], False)
        self.assertEqual(result, "CONFLICT_CONFIRMED")
        external["evidence_authority"] = "secondary"
        result, _ = _classify(self.case, external, self.movie, [current], False)
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

    def test_manifest_gate_preserves_missing_conflicting_and_quarantined_catalogue_rows(self):
        cases = [
            (238, "movie", "The Godfather", 1972, "The Godfather", 1972),
            (603, "movie", "The Matrix", 1999, "The Matrix", 1999),
            (27205, "movie", "Inception", 2010, "Inception", 2010),
            (496243, "movie", "Parasite", 2019, "Parasite", 2019),
            (438631, "movie", "Dune", 2021, "Dune", 2021),
            (220289, "movie", "Coherence", 2013, "Coherence", 2014),
            (2316, "series", "The Office US", 2005, "The Office", 2005),
        ]
        Movie.objects.create(tmdb_id=238, title="Legacy display title", year=1972)
        Movie.objects.create(tmdb_id=27205, title="Inception", canonical_title="Unrelated", year=2010)
        Movie.objects.create(tmdb_id=496243, title="Parasite", year=2000)
        Movie.objects.create(tmdb_id=438631, title="Dune", year=2021, is_quarantined=True)
        Movie.objects.create(tmdb_id=220289, title="Coherence", canonical_title="Coherence", year=2013)
        Movie.objects.create(tmdb_id=2316, title="Same numeric ID in movie namespace", year=2005)
        provider, _ = Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "Netflix", "category": ProviderCategory.OFFICIAL}
        )
        source, _ = Source.objects.update_or_create(slug="justwatch", defaults={"name": "JustWatch", "enabled": True})
        country, _ = Country.objects.get_or_create(code="NL", defaults={"name": "Netherlands", "enabled": True})
        for tmdb_id in (238, 603):
            CanonicalOffer.objects.create(
                provider=provider, source=source, country=country, content_type="movie", tmdb_id=tmdb_id,
                offer_type="subscription", provider_content_id=str(tmdb_id), last_seen=timezone.now(),
                currently_available=True, identity="",
            )
        before = list(Movie.objects.order_by("pk").values())

        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            cohort, evidence, manifest = root / "cohort.csv", root / "evidence.csv", root / "manifest.csv"
            output = root / "out"
            with (
                cohort.open("w", newline="", encoding="utf-8") as cohort_file,
                evidence.open("w", newline="", encoding="utf-8") as evidence_file,
                manifest.open("w", newline="", encoding="utf-8") as manifest_file,
            ):
                cohort_writer, evidence_writer, manifest_writer = map(csv.writer, (cohort_file, evidence_file, manifest_file))
                cohort_writer.writerow(["tmdb_id", "media_type", "title", "year", "country"])
                evidence_writer.writerow(["tmdb_id", "media_type", "title", "year", "country", "provider",
                                          "offer_type", "explicitly_confirms_availability"])
                manifest_writer.writerow(["tmdb_id", "media_type", "canonical_title", "year", "source",
                                          "evidence_sha256", "observed_utc", "provenance"])
                for tmdb_id, kind, title, year, canonical_title, canonical_year in cases:
                    cohort_writer.writerow([tmdb_id, kind, title, year, "NL"])
                    evidence_writer.writerow([tmdb_id, kind, title, year, "NL", "Netflix", "subscription", "Yes"])
                    manifest_writer.writerow([tmdb_id, kind, canonical_title, canonical_year, "tmdb_direct_id_details",
                                              "a" * 64, "2026-11-23T00:00:00+00:00", "Direct ID fixture"])
            with (
                patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP must not be called")),
                patch("socket.socket.connect", side_effect=AssertionError("No new network connection expected")),
            ):
                call_command("audit_availability_accuracy", cohort=cohort, external_evidence=evidence,
                             identity_manifest=manifest, output=output, offline=True, verbosity=0)
            with (output / "comparison.csv").open(newline="", encoding="utf-8") as handle:
                results = {int(row["tmdb_id"]): row for row in csv.DictReader(handle)}
            with (output / "application_snapshot.csv").open(newline="", encoding="utf-8") as handle:
                snapshot = {int(row["tmdb_id"]): row for row in csv.DictReader(handle)}

        self.assertEqual(list(Movie.objects.order_by("pk").values()), before)
        accepted = results[238]
        self.assertEqual(accepted["local_identity_state"], "MISSING_CANONICAL_TITLE")
        self.assertEqual(accepted["identity_source"], "tmdb_manifest")
        self.assertEqual(accepted["comparison_state"], "AGREEMENT_CONFIRMED")
        missing = results[603]
        self.assertEqual(missing["comparison_state"], "APP_DATA_MISSING")
        self.assertEqual(missing["identity_source"], "tmdb_manifest")
        self.assertEqual(missing["app_movie_found"], "False")
        self.assertEqual(snapshot[603]["movie_id"], "")
        self.assertEqual(snapshot[603]["identity_match"], "False")
        self.assertEqual(snapshot[603]["profile_c_verdict"], "")
        for tmdb_id, state in [(27205, "CONFLICT"), (496243, "CONFLICT"), (438631, "QUARANTINED")]:
            with self.subTest(tmdb_id=tmdb_id):
                self.assertEqual(results[tmdb_id]["local_identity_state"], state)
                self.assertEqual(results[tmdb_id]["identity_source"], "")
                self.assertEqual(results[tmdb_id]["comparison_state"], "NOT_COMPARABLE")
        coherence = results[220289]
        self.assertEqual((coherence["year"], coherence["identity_manifest_year"]), ("2013", "2014"))
        self.assertEqual(coherence["identity_manifest_review"], "YEAR_MISMATCH")
        self.assertEqual(coherence["comparison_state"], "NOT_COMPARABLE")
        office = results[2316]
        self.assertEqual((office["title"], office["identity_manifest_title"]), ("The Office US", "The Office"))
        self.assertEqual(office["identity_manifest_review"], "")
        self.assertEqual(office["identity_source"], "tmdb_manifest")
        self.assertEqual(office["comparison_state"], "APP_DATA_MISSING")
        self.assertEqual(office["app_movie_found"], "False")

    def test_command_emits_comparable_snapshot_without_mutating_fixture_rows_or_http(self):
        movie = Movie.objects.create(tmdb_id=987654, title="Audit fixture", canonical_title="Audit fixture",
                                     year=2000, content_type="movie")
        provider, _ = Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "Netflix", "category": ProviderCategory.OFFICIAL}
        )
        source, _ = Source.objects.get_or_create(
            slug="justwatch", defaults={"name": "JustWatch", "enabled": True}
        )
        if not source.enabled:
            source.enabled = True
            source.save(update_fields=["enabled"])
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
                writer = csv.DictWriter(handle, fieldnames=["tmdb_id", "media_type", "country", "provider", "offer_type", "explicitly_confirms_availability"])
                writer.writeheader()
                writer.writerow({"tmdb_id": movie.tmdb_id, "media_type": "movie", "country": "NL", "provider": "Netflix", "offer_type": "subscription", "explicitly_confirms_availability": "Yes"})
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
            report = (output / "coverage_summary.md").read_text()
            self.assertIn("code revision: unrecorded", report)
            self.assertIn('--external-evidence "$EXTERNAL_EVIDENCE_CSV"', report)
            self.assertNotIn("--identity-manifest", report)
            self.assertNotIn("stage2c", report)
            self.assertIn("Confirmed conflicts in this run: 0", (output / "confirmed_discrepancies.md").read_text())

    def test_nonempty_cases_classify_missing_stale_offer_type_identity_and_source_state(self):
        country, _ = Country.objects.get_or_create(code="NL", defaults={"name": "Netherlands", "enabled": True})
        justwatch, _ = Source.objects.get_or_create(slug="justwatch", defaults={"name": "JustWatch", "enabled": True})
        justwatch.enabled = True
        justwatch.save(update_fields=["enabled"])
        enabled_source, _ = Source.objects.get_or_create(
            slug="audit_enabled", defaults={"name": "Audit enabled", "enabled": True}
        )
        enabled_source.enabled = True
        enabled_source.save(update_fields=["enabled"])
        disabled_source, _ = Source.objects.get_or_create(
            slug="audit_disabled", defaults={"name": "Audit disabled", "enabled": False}
        )
        disabled_source.enabled = False
        disabled_source.save(update_fields=["enabled"])
        netflix, _ = Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "Netflix", "category": ProviderCategory.OFFICIAL}
        )
        prime, _ = Provider.objects.get_or_create(
            slug="prime", defaults={"name": "Prime Video", "category": ProviderCategory.OFFICIAL}
        )

        cases = [
            (987661, "Missing offers", 2000),
            (987662, "Stale match", 2000),
            (987663, "Wrong offer class", 2000),
            (987664, "Dune 1984 fixture", 1984),
            (987665, "Disabled source", 2000),
            (987666, "Unconfigured source", 2000),
        ]
        movies = {
            tmdb_id: Movie.objects.create(tmdb_id=tmdb_id, title=title, canonical_title=title,
                                          year=year, content_type="movie")
            for tmdb_id, title, year in cases
        }
        old = timezone.now() - timedelta(days=90)
        CanonicalOffer.objects.create(
            provider=netflix, source=justwatch, country=country, content_type="movie", tmdb_id=987662,
            offer_type="subscription", provider_content_id="stale-netflix", last_seen=old,
            observed_at=old, currently_available=True, identity="",
        )
        CanonicalOffer.objects.create(
            provider=prime, source=justwatch, country=country, content_type="movie", tmdb_id=987662,
            offer_type="subscription", provider_content_id="fresh-prime", last_seen=timezone.now(),
            currently_available=True, identity="",
        )
        CanonicalOffer.objects.create(
            provider=netflix, source=justwatch, country=country, content_type="movie", tmdb_id=987663,
            offer_type="rent", provider_content_id="netflix-rent", last_seen=timezone.now(),
            currently_available=True, identity="",
        )
        CanonicalOffer.objects.create(
            provider=netflix, source=justwatch, country=country, content_type="movie", tmdb_id=987664,
            offer_type="subscription", provider_content_id="dune-1984-offer", last_seen=timezone.now(),
            currently_available=True, identity="",
        )

        external_by_id = {
            tmdb_id: {"tmdb_id": tmdb_id, "title": title, "media_type": "movie", "year": 2000,
                      "country": "NL", "provider": "Netflix", "offer_type": "subscription",
                      "explicitly_confirms_availability": "Yes"}
            for tmdb_id, title, _ in cases
        }
        external_by_id[987665]["app_source_slug"] = "audit_disabled"
        external_by_id[987666]["app_source_slug"] = "missing_source_config"
        external_by_id[987661]["app_source_slug"] = "audit_enabled"

        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            cohort, evidence, output = root / "cohort.csv", root / "evidence.csv", root / "out"
            columns = ["tmdb_id", "title", "media_type", "year", "country"]
            with cohort.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                for tmdb_id, title, year in cases:
                    writer.writerow({"tmdb_id": tmdb_id, "title": title, "media_type": "movie", "year": 2000, "country": "NL"})
            ext_columns = ["tmdb_id", "title", "media_type", "year", "country", "provider", "offer_type",
                           "explicitly_confirms_availability", "app_source_slug"]
            with evidence.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=ext_columns)
                writer.writeheader()
                writer.writerows(external_by_id.values())
            before = (Movie.objects.count(), CanonicalOffer.objects.count(), Source.objects.count())
            with patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP must not be called")):
                call_command("audit_availability_accuracy", cohort=str(cohort), external_evidence=str(evidence),
                             output=str(output), offline=True, verbosity=0)
            self.assertEqual(before, (Movie.objects.count(), CanonicalOffer.objects.count(), Source.objects.count()))
            with (output / "comparison.csv").open(newline="", encoding="utf-8") as handle:
                results = {int(row["tmdb_id"]): row for row in csv.DictReader(handle)}
        self.assertEqual(results[987661]["comparison_state"], "APP_DATA_MISSING")
        self.assertEqual(results[987662]["comparison_state"], "APP_DATA_STALE")
        self.assertEqual(results[987663]["comparison_state"], "INSUFFICIENT_APP_EVIDENCE")
        self.assertEqual(results[987664]["comparison_state"], "NOT_COMPARABLE")
        self.assertEqual(results[987665]["comparison_state"], "APP_SOURCE_DISABLED")
        self.assertEqual(results[987666]["comparison_state"], "INSUFFICIENT_APP_EVIDENCE")
