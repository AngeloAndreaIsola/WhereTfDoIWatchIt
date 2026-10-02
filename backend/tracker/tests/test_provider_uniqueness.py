"""Regression tests: one provider row per site (domain-root uniqueness).

Migration 0033_merge_duplicate_providers folded the duplicate seed lineages
(the ``rai-play`` / ``bbc`` / ``nhk`` aliases) into their canonical rows and
added ``uniq_provider_domain_root``. These tests pin the invariant itself,
the post-migration data state, the API view, and the URL-driven provider
lookup that used to mint a second row from a domain's first label.
"""
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from tracker.models import (
    ExternalProviderMapping,
    Movie,
    Provider,
    ProviderCategory,
    ScrapedContentMapping,
    Subscription,
    provider_domain_root,
    provider_domain_root_expr,
)
from tracker.scrapers import SCRAPER_REGISTRY
from tracker.views import detect_provider_from_url

LEGACY_ALIAS_SLUGS = ("rai-play", "bbc", "nhk")
CANONICAL_BROADCASTER_SLUGS = ("raiplay", "bbc_iplayer", "nhk_world")


class DomainRootExpressionTests(TestCase):
    def test_python_root_matches_sql_expression(self):
        samples = [
            "media.test.example/one/two",
            "plain.example.org",
            "Mixed.Case.NET/Path",
            "  lead.example.com/px",
            "trail.example.dev/  ",
        ]
        for i, domain in enumerate(samples):
            Provider.objects.create(slug=f"root-sample-{i}", name=f"Root sample {i}", domain=domain)

        rows = Provider.objects.filter(
            slug__startswith="root-sample-"
        ).annotate(_root=provider_domain_root_expr())
        self.assertEqual(rows.count(), len(samples))
        for row in rows:
            self.assertEqual(row._root, provider_domain_root(row.domain))

    def test_root_is_host_before_first_path_separator(self):
        self.assertEqual(provider_domain_root("bbc.co.uk/iplayer"), "bbc.co.uk")
        self.assertEqual(provider_domain_root("raiplay.it"), "raiplay.it")
        self.assertEqual(provider_domain_root("NHK.OR.JP/nhkworld/"), "nhk.or.jp")
        self.assertEqual(provider_domain_root(""), "")
        self.assertEqual(provider_domain_root(None), "")


class DomainRootConstraintTests(TestCase):
    def test_duplicate_domain_root_is_rejected(self):
        Provider.objects.create(slug="dup-one", name="One", domain="dup.example.com/one")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Provider.objects.create(slug="dup-two", name="Two", domain="dup.example.com/two")

    def test_domain_root_comparison_is_case_insensitive(self):
        Provider.objects.create(slug="case-one", name="One", domain="Case.Example.COM/page")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Provider.objects.create(slug="case-two", name="Two", domain="case.example.com")

    def test_providers_without_a_domain_are_exempt(self):
        Provider.objects.create(slug="no-domain-a", name="A", domain="")
        Provider.objects.create(slug="no-domain-b", name="B", domain="")
        self.assertEqual(
            Provider.objects.filter(slug__startswith="no-domain-").count(), 2
        )

    def test_constraint_is_declared_on_the_model(self):
        names = [c.name for c in Provider._meta.constraints]
        self.assertIn("uniq_provider_domain_root", names)


class MergedSeedDataTests(TestCase):
    """The state every migrated database ends up in (CI runs migrations)."""

    def test_legacy_alias_slugs_do_not_exist(self):
        for slug in LEGACY_ALIAS_SLUGS:
            self.assertFalse(
                Provider.objects.filter(slug=slug).exists(),
                f"legacy alias provider '{slug}' should have been merged away",
            )

    def test_canonical_broadcasters_exist_exactly_once(self):
        for slug in CANONICAL_BROADCASTER_SLUGS:
            provider = Provider.objects.filter(slug=slug).first()
            self.assertIsNotNone(provider, f"canonical provider '{slug}' should exist")
            self.assertEqual(provider.category, ProviderCategory.BVOD)
            self.assertEqual(Provider.objects.filter(slug=slug).count(), 1)

    def test_no_two_providers_share_a_domain_root(self):
        seen = {}
        for provider in Provider.objects.exclude(domain="").order_by("id"):
            root = provider_domain_root(provider.domain)
            self.assertTrue(
                root not in seen,
                f"domain root '{root}' shared by '{seen.get(root)}' and '{provider.slug}'",
            )
            seen[root] = provider.slug

    def test_no_duplicate_display_names(self):
        names = list(Provider.objects.values_list("name", flat=True))
        self.assertEqual(len(names), len(set(names)))


class ProvidersEndpointDuplicationTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_endpoint_returns_each_site_once(self):
        resp = self.client.get("/api/providers/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        slugs = [p["slug"] for p in data]
        self.assertEqual(len(slugs), len(set(slugs)))

        roots = [provider_domain_root(p["domain"]) for p in data if p.get("domain")]
        self.assertEqual(len(roots), len(set(roots)))

        for slug in CANONICAL_BROADCASTER_SLUGS:
            self.assertEqual(slugs.count(slug), 1, f"'{slug}' should appear exactly once")
        for slug in LEGACY_ALIAS_SLUGS:
            self.assertNotIn(slug, slugs)


class DetectProviderFromUrlTests(TestCase):
    def test_known_site_reuses_its_existing_provider(self):
        cases = [
            ("https://www.raiplay.it/programmi/x", "raiplay"),
            ("https://www.bbc.co.uk/iplayer/episode/x", "bbc_iplayer"),
            ("https://www.nhk.or.jp/nhkworld/programs/", "nhk_world"),
            ("https://kisskh.co/Drama/Demon-Slayer", "kisskh"),
        ]
        before = Provider.objects.count()
        for url, expected_slug in cases:
            provider, query = detect_provider_from_url(url)
            self.assertEqual(provider.slug, expected_slug, url)
            self.assertTrue(query)
        self.assertEqual(Provider.objects.count(), before)

    def test_unknown_domain_creates_exactly_one_scraper_provider(self):
        before = Provider.objects.count()
        first, _ = detect_provider_from_url("https://fresh-unknown.example/watch/foo-bar")
        second, _ = detect_provider_from_url("https://www.fresh-unknown.example/other/path")

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.category, ProviderCategory.SCRAPER)
        self.assertEqual(Provider.objects.count(), before + 1)

    def test_new_domain_that_would_shadow_an_existing_root_reuses_it(self):
        """A sub-path of a known site must not mint a sibling row."""
        before = Provider.objects.count()
        provider, _ = detect_provider_from_url("https://bbc.co.uk/sport/football")
        self.assertEqual(provider.slug, "bbc_iplayer")
        self.assertEqual(Provider.objects.count(), before)


class ScraperRegistryTests(TestCase):
    def test_registry_has_no_legacy_alias_slugs(self):
        for slug in LEGACY_ALIAS_SLUGS:
            self.assertNotIn(slug, SCRAPER_REGISTRY)

    def test_registry_covers_canonical_broadcasters(self):
        for slug in CANONICAL_BROADCASTER_SLUGS:
            self.assertIn(slug, SCRAPER_REGISTRY)


class MergeMigrationTests(TestCase):
    """Migration 0033's merge step, exercised on a duplicate pair.

    Grouping (one member per domain root) runs during every migration — that
    is what MergedSeedDataTests sees when it asserts the seeded aliases are
    gone. What needs a fixture pair here is the fold itself: the duplicate row
    carries an empty domain, which the constraint exempts, and the merge step
    is then invoked exactly as the migration invokes it.
    """

    def setUp(self):
        import importlib

        from availability.services.legacy_ingest import ingest_scraped_mapping
        from tracker.tests.availability_factories import local_offer

        migration = importlib.import_module(
            "tracker.migrations.0033_merge_duplicate_providers"
        )
        self._merge_provider = migration._merge_provider
        self._merge_all = migration.merge_duplicate_providers
        self._ingest = ingest_scraped_mapping
        self._local_offer = local_offer

    def _arrange(self):
        canonical = Provider.objects.create(
            slug="mrg-site",
            name="Merge Site",
            domain="mrg.example.com/one",
            category=ProviderCategory.BVOD,
        )
        duplicate = Provider.objects.create(
            slug="mrg-site-alias",
            name="Merge Site Alias",
            domain="",
            category=ProviderCategory.SCRAPER,
        )

        Subscription.objects.create(user_id=1, provider=canonical, name="canonical")
        Subscription.objects.create(user_id=1, provider=duplicate, name="duplicate")
        ExternalProviderMapping.objects.create(
            provider=duplicate, integration_name="streaming_availability", external_id="mrg"
        )

        movie = Movie.objects.create(tmdb_id=999001, title="Merge Me", year=2021)
        twin_canonical = ScrapedContentMapping.objects.create(
            provider=canonical, scraped_title="Merge Me", scraped_year=2021
        )
        twin_duplicate = ScrapedContentMapping.objects.create(
            provider=duplicate,
            scraped_title="Merge Me",
            scraped_year=2021,
            movie=movie,
            is_confirmed=True,
            confidence_score=0.9,
        )
        only_duplicate = ScrapedContentMapping.objects.create(
            provider=duplicate, scraped_title="Only Dup", scraped_year=1999
        )
        self._ingest(twin_canonical)
        self._ingest(twin_duplicate)
        self._ingest(only_duplicate)

        # One offer on each side for the same content (a real duplicate) and
        # one offer that only the alias provider knows about.
        self._local_offer(movie, service="mrg-site")
        self._local_offer(movie, service="mrg-site-alias")
        other = Movie.objects.create(tmdb_id=999002, title="Only Offer", year=2020)
        self._local_offer(other, service="mrg-site-alias")

        return canonical, duplicate, movie, twin_canonical, twin_duplicate, only_duplicate

    def test_merge_folds_duplicates_into_the_canonical_row(self):
        from availability.models import ContentMapping, Offer, ProviderContent

        canonical, duplicate, movie, twin_canonical, twin_duplicate, only_duplicate = (
            self._arrange()
        )
        duplicate_pk = duplicate.pk

        self._merge_provider(canonical, duplicate)

        # The alias provider row is gone; the canonical one survives.
        self.assertFalse(Provider.objects.filter(pk=duplicate_pk).exists())
        self.assertTrue(Provider.objects.filter(pk=canonical.pk).exists())

        # One subscription per user, one external mapping, repointed.
        self.assertEqual(
            Subscription.objects.filter(user_id=1, provider=canonical).count(), 1
        )
        self.assertEqual(Subscription.objects.filter(user_id=1).count(), 1)
        mapping = ExternalProviderMapping.objects.get(
            provider=canonical, integration_name="streaming_availability"
        )
        self.assertEqual(mapping.external_id, "mrg")
        self.assertFalse(
            ExternalProviderMapping.objects.filter(provider_id=duplicate_pk).exists()
        )

        # The twin absorbed the alias's movie link, confirmation and confidence.
        twin_canonical.refresh_from_db()
        self.assertEqual(twin_canonical.movie_id, movie.pk)
        self.assertTrue(twin_canonical.is_confirmed)
        self.assertEqual(twin_canonical.confidence_score, 0.9)
        self.assertFalse(
            ScrapedContentMapping.objects.filter(pk=twin_duplicate.pk).exists()
        )

        # The alias-only scrape row moved to the canonical provider.
        only_duplicate.refresh_from_db()
        self.assertEqual(only_duplicate.provider_id, canonical.pk)

        # No alias-owned availability rows survive; projections follow the
        # tracker rows (merged row re-ingested, moved row re-pointed).
        self.assertFalse(ProviderContent.objects.filter(provider_id=duplicate_pk).exists())
        self.assertFalse(ContentMapping.objects.filter(provider_id=duplicate_pk).exists())
        self.assertTrue(
            ContentMapping.objects.filter(
                provider=canonical, provider_content_id=f"legacy-{twin_canonical.pk}"
            ).exists()
        )
        self.assertTrue(
            ProviderContent.objects.filter(
                provider=canonical, provider_content_id=f"legacy-{only_duplicate.pk}"
            ).exists()
        )

        # Offers: the genuine duplicate collapses to one row, the alias-only
        # offer follows the provider with its identity rebuilt.
        self.assertEqual(
            Offer.objects.filter(tmdb_id=999001, provider=canonical).count(), 1
        )
        only_offer = Offer.objects.get(tmdb_id=999002)
        self.assertEqual(only_offer.provider_id, canonical.pk)
        self.assertTrue(only_offer.identity.startswith(f"{canonical.slug}|"))

    def test_merge_is_a_no_op_without_duplicate_roots(self):
        canonical = Provider.objects.create(
            slug="mrg-single", name="Single", domain="mrg-single.example.com"
        )
        before = Provider.objects.count()
        self._merge_all(None, None)
        self.assertEqual(Provider.objects.count(), before)
        self.assertTrue(Provider.objects.filter(pk=canonical.pk).exists())
