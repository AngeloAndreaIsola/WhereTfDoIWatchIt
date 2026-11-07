"""Offline report decisions use real source policy and real report evidence."""
import json
import socket
import sys
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.test import TestCase
from django.utils import timezone

SCRIPT_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import offline_counterfactual_report as report
from availability.domain.enums import ContentType
from availability.freshness import freshness_for
from availability.models import Country, Offer, Source
from availability.sources import source_registry
from tracker.models import Movie, Provider, ScrapedContentMapping
from tracker.services import availability_report
from tracker.services.source_policy import SOURCE_ROLES, counterfactual, decide_sources_for_title

SA = "streaming_availability"


class OfflineCounterfactualTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.applicable = report.applicable_sources()

    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=99100001, title="Synthetic DB Fixture", year=2020)
        self.provider, _ = Provider.objects.get_or_create(
            slug="offline-fixture-provider", defaults={"name": "Offline fixture", "category": "stream"}
        )
        self.nl, _ = Country.objects.get_or_create(code="NL", defaults={"name": "Netherlands"})
        self.it, _ = Country.objects.get_or_create(code="IT", defaults={"name": "Italy"})

    def offer(self, slug, country=None, last_seen=None):
        source, _ = Source.objects.get_or_create(slug=slug, defaults={"name": slug})
        return Offer.objects.create(
            tmdb_id=self.movie.tmdb_id, provider=self.provider, country=country or self.nl,
            source=source, content_type=ContentType.MOVIE, offer_type="subscription",
            provider_content_id=slug, currently_available=True,
            last_seen=last_seen or timezone.now(), first_seen=last_seen or timezone.now(),
        )

    def decision_from_report(self, country="NL"):
        entry = availability_report.title_source_report([self.movie.pk], country=country)[self.movie.pk]
        decision = decide_sources_for_title(entry, applicable_slugs=self.applicable, ladder_switch=True)
        return entry, decision

    def fixture_title(self, state="unknown", resolved=()):
        resolved = set(resolved)
        return report.FixtureTitle(
            1, 99100001, "Synthetic fixture", None, state, resolved,
            {"NL": 1, "IT": 1, "JP": 1}, {"NL": ["nl"], "IT": ["it"], "JP": ["ja"]},
            {country: {"state": state, "sources": [{"source": slug, "available": True} for slug in sorted(resolved)]}
             for country in report.REGIONS},
        )

    def test_c01_gate_off_matches_production_decision(self):
        title = self.fixture_title("available", {"justwatch"})
        expected = decide_sources_for_title(title.report_entries_by_country["NL"],
                                            applicable_slugs=self.applicable, ladder_switch=False)
        self.assertEqual(report.decide_gate(title, ladder_on=False), expected)

    def test_c02_gate_on_matches_production_decision(self):
        title = self.fixture_title("available", {"justwatch"})
        expected = decide_sources_for_title(title.report_entries_by_country["NL"],
                                            applicable_slugs=self.applicable, ladder_switch=True)
        self.assertEqual(report.decide_gate(title, ladder_on=True), expected)

    def test_c03_trusted_fresh_cheaper_offer_suppresses_metered(self):
        self.offer("justwatch")
        entry, decision = self.decision_from_report()
        self.assertEqual(entry["state"], "available")
        self.assertNotIn(SA, decision["allowed_sources"])
        self.assertEqual(decision["skipped_sources"][SA], "not_needed")

    def test_c04_unknown_title_keeps_metered_eligible(self):
        entry, decision = self.decision_from_report()
        self.assertEqual(entry["state"], "unknown")
        self.assertIn(SA, decision["allowed_sources"])

    def test_c05_stale_positive_offer_does_not_suppress_metered(self):
        stale_at = timezone.now() - freshness_for("justwatch") - timedelta(minutes=1)
        self.offer("justwatch", last_seen=stale_at)
        entry, decision = self.decision_from_report()
        source_row = next(row for row in entry["sources"] if row["source"] == "justwatch")
        self.assertFalse(source_row["available"])
        self.assertEqual(entry["state"], "unknown")
        self.assertIn(SA, decision["allowed_sources"])

    def test_c06_unconfirmed_scraped_mapping_does_not_suppress_metered(self):
        ScrapedContentMapping.objects.create(
            provider=self.provider, scraped_title="Untrusted synthetic match", scraped_year=2020,
            country_code="NL", movie=self.movie, is_confirmed=False, confidence_score=0.25,
            is_available=True, last_scraped_at=timezone.now(),
        )
        entry, decision = self.decision_from_report()
        self.assertFalse(any(row["available"] for row in entry["sources"]))
        self.assertIn(SA, decision["allowed_sources"])

    def test_c07_cross_country_offer_does_not_suppress_metered(self):
        self.offer("justwatch", country=self.it)
        entry, decision = self.decision_from_report(country="NL")
        self.assertEqual(entry["state"], "unknown")
        self.assertIn(SA, decision["allowed_sources"])

    def test_c08_catalogue_only_sources_are_not_title_applicable(self):
        catalogue_only = set()
        for slug in source_registry.slugs():
            caps = source_registry.get(slug).capabilities
            if caps.supports_catalogue_sync and not caps.supports_title_fetch:
                catalogue_only.add(slug)
        self.assertTrue(catalogue_only)
        self.assertTrue(catalogue_only.isdisjoint(self.applicable))

    def test_c09_fixture_has_100_and_200_unique_synthetic_identities(self):
        first = report.FixtureGenerator().generate(200)
        repeated = report.FixtureGenerator().generate(200)
        self.assertEqual(first, repeated)
        self.assertEqual(len({row.movie_id for row in first[:100]}), 100)
        self.assertEqual(len({row.movie_id for row in first}), 200)
        self.assertEqual(len({row.tmdb_id for row in first}), 200)
        self.assertTrue(all(row.title.startswith("Synthetic Title ") and row.year is None for row in first))

    def test_c10_fixture_run_makes_no_network_calls(self):
        with self.assertNumQueries(0), mock.patch.object(socket.socket, "connect", side_effect=AssertionError("network used")), \
             mock.patch.object(socket, "create_connection", side_effect=AssertionError("network used")):
            self.assertEqual(report.run_batch(100).total_titles, 100)

    def test_c11_unavailable_runtime_metrics_are_not_measured(self):
        result = report.run_batch(100)
        self.assertEqual(result.actual_requests, "not_measured")
        self.assertEqual(result.actual_quota_spend, "not_measured")
        self.assertEqual(result.sql_metrics["total_queries"], "not_measured")
        self.assertEqual(result.sql_metrics["execution_time_ms"], "not_measured")

    def test_c12_actual_savings_are_not_claimed(self):
        result = report.run_batch(100)
        self.assertEqual(result.actual_savings, "not_measured")
        self.assertIn("or infer a defect", result.policy_observations[0])

    def test_c13_json_and_markdown_totals_agree(self):
        result = report.run_batch(100)
        data = json.loads(report.as_json(result))
        markdown = report.as_markdown(result)
        self.assertEqual(data["total_titles"], result.total_titles)
        self.assertIn(f"Synthetic titles: {data['total_titles']}", markdown)
        self.assertIn(f"NL available answers lost if metered evidence were removed: {data['titles_losing_answer']}", markdown)
        for country, values in data["nl_it_jp_breakdown"].items():
            row = f"| {country} | {values['titles']} | {values.get('available', 0)} | {values['offers']} | {values['with_lang_match']} | {values['metered_eligible_gate_off']} | {values['metered_eligible_gate_on']} |"
            self.assertIn(row, markdown)
        self.assertEqual(set(data["nl_it_jp_breakdown"]), {"NL", "IT", "JP"})

    def test_c14_fresh_metered_only_answer_is_a_limited_counterfactual_observation(self):
        self.offer(SA)
        entry, decision = self.decision_from_report()
        self.assertEqual(entry["state"], "available")
        self.assertNotIn(SA, decision["allowed_sources"])
        projection = counterfactual({self.movie.pk: entry["state"]},
                                   {self.movie.pk: {row["source"] for row in entry["sources"] if row["available"]}})
        self.assertEqual(projection["titles_losing_their_answer"], 1)
        self.assertTrue(any("fresh metered-only positive" in item
                            for item in report.run_batch(100).policy_observations))

    def test_source_roles_match_registered_sources(self):
        self.assertEqual(set(SOURCE_ROLES), set(source_registry.slugs()))


    def test_every_market_decision_matches_policy_and_totals(self):
        fixtures = report.FixtureGenerator().generate(100)
        result = report.run_batch(100)
        for title, rendered in zip(fixtures, result.per_title):
            for country, entry in title.report_entries_by_country.items():
                for gate, label in ((False, "off"), (True, "on")):
                    expected = decide_sources_for_title(entry, applicable_slugs=self.applicable, ladder_switch=gate)
                    actual = rendered.decisions_by_country[country]
                    self.assertEqual(actual[f"gate_{label}_allowed"], expected["allowed_sources"])
                    self.assertEqual(actual[f"gate_{label}_skipped"], expected["skipped_sources"])
                if entry["state"] != "available":
                    self.assertEqual(title.offers_by_region[country], 0)
        for country, values in result.hypothetical_eligibility["by_country"].items():
            for label in ("off", "on"):
                expected_count = sum(SA in row.decisions_by_country[country][f"gate_{label}_allowed"] for row in result.per_title)
                self.assertEqual(values[f"metered_eligible_gate_{label}"], expected_count)

    def test_report_output_is_deterministic_for_100_and_200_titles(self):
        for size in (100, 200):
            self.assertEqual(report.as_json(report.run_batch(size)), report.as_json(report.run_batch(size)))

    def test_explicit_empty_applicability_does_not_fall_back_to_registry(self):
        self.assertEqual(report.decide_gate(self.fixture_title(), ladder_on=False, applicable_slugs=[])["allowed_sources"], [])
