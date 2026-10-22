"""Safety and correctness rules for ``remediate_mapping_audit``.

The command deletes and rewrites production availability rows, so the tests pin
the properties that make that safe:

* dry-run and manifest generation write nothing to the database;
* a manifest from another database, an edited manifest, a malformed manifest or
  a different plan is rejected before anything happens;
* a missing target row, an extra row in the targeted scope, a changed target
  row and a changed protected mapping all abort;
* if any invariant fails after the writes, the mapping update and every delete
  roll back together;
* only the exact allow-listed primary keys are touched and unrelated rows are
  left byte-identical;
* the targeted evidence row is removed and no evidence/price orphan remains;
* unrelated evidence and price rows are never purged, including on failure;
* mapping ``399`` ends up manual/TMDb 2996/1.0;
* the matcher-deployment, writer-barrier and digest-bound confirmation gates are
  exercised, including their failure paths;
* a post-commit validation failure raises a non-zero command outcome instead of
  reporting success.

The suite deliberately builds a database that matches ``APPROVED_PLAN`` with
explicit primary keys, so it exercises the real allow-list end to end rather
than a patched-away copy of it.
"""
from __future__ import annotations

import json
import tempfile
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections
from django.test import TestCase
from django.utils import timezone

from availability.models import (
    ContentMapping,
    Country,
    Offer,
    OfferEvidence,
    OfferPrice,
    ProviderContent,
    Source,
)
from availability.services import mapping_remediation
from availability.services import write_barrier
from availability.services.mapping_remediation import (
    APPROVED_PLAN,
    DATABASE_IDENTITY_SCHEMA,
    build_manifest,
    compute_digest,
    confirmation_digest_prefix,
    validate_confirmation,
)
from tracker.models import Movie, Provider

OFFICE_PCID = "b00jd68z"


class FakeVerifiedBarrier:
    """Stand-in for a PostgreSQL exclusive barrier in the SQLite test DB."""

    verified = True
    held = True

    def __init__(self):
        self.released = False

    def release(self):
        self.released = True
        self.held = False


def identity(
    *,
    name="tracker",
    host="db",
    port="5432",
    address="10.0.0.5",
    server_port=5432,
    system_identifier="7300000000000000001",
):
    """A complete PostgreSQL identity, without credentials."""
    strength = (
        "cluster"
        if system_identifier
        else ("network" if address and server_port else "weak")
    )
    return {
        "schema": DATABASE_IDENTITY_SCHEMA,
        "vendor": "postgresql",
        "name": name,
        "configured": {"host": host, "port": port},
        "server": {
            "address": address,
            "port": server_port,
            "version": "16.4",
            "system_identifier": system_identifier,
        },
        "strength": strength,
    }



def _content(provider, source, pcid, content_type, title):
    return ProviderContent.objects.create(
        provider=provider,
        source=source,
        provider_content_id=pcid,
        content_type=content_type,
        title=title,
    )


def _mapping(mapping_id, provider, source, pcid, content_type, tmdb_id, content):
    return ContentMapping.objects.create(
        id=mapping_id,
        provider=provider,
        source=source,
        provider_content_id=pcid,
        content_type=content_type,
        tmdb_id=tmdb_id,
        confidence=0.7,
        match_method="title_year",
        content=content,
    )


def _offer(
    offer_id,
    provider,
    source,
    country,
    content_type,
    tmdb_id,
    pcid,
    *,
    season=None,
    episode=None,
    offer_type="free",
):
    return Offer.objects.create(
        id=offer_id,
        provider=provider,
        country=country,
        source=source,
        content_type=content_type,
        tmdb_id=tmdb_id,
        season_number=season,
        episode_number=episode,
        offer_type=offer_type,
        provider_content_id=pcid,
        provider_url=f"https://example.test/{offer_id}",
    )


def seed_world():
    """Build a database that exactly matches ``APPROVED_PLAN``.

    Every allow-listed row uses its real primary key. The ``unrelated_*`` rows
    are deliberately outside the plan so "nothing else changed" can be pinned.
    """
    bbc, _ = Provider.objects.get_or_create(slug="bbc_iplayer", defaults={"name": "BBC iPlayer"})
    cbc, _ = Provider.objects.get_or_create(slug="cbc_gem", defaults={"name": "CBC Gem"})
    tubi, _ = Provider.objects.get_or_create(slug="tubi", defaults={"name": "Tubi"})
    other, _ = Provider.objects.get_or_create(
        slug="unrelated_provider", defaults={"name": "Unrelated"}
    )

    bbc_src, _ = Source.objects.get_or_create(slug="bbc_iplayer", defaults={"name": "BBC"})
    cbc_src, _ = Source.objects.get_or_create(slug="cbc_gem", defaults={"name": "CBC"})
    tubi_src, _ = Source.objects.get_or_create(slug="tubi", defaults={"name": "Tubi"})
    other_src, _ = Source.objects.get_or_create(
        slug="unrelated_source", defaults={"name": "Unrelated"}
    )

    gb, _ = Country.objects.get_or_create(code="GB", defaults={"name": "United Kingdom"})
    ca, _ = Country.objects.get_or_create(code="CA", defaults={"name": "Canada"})
    us, _ = Country.objects.get_or_create(code="US", defaults={"name": "United States"})

    office_content = _content(bbc, bbc_src, OFFICE_PCID, "series", "The Office")
    _mapping(399, bbc, bbc_src, OFFICE_PCID, "series", 2316, office_content)
    _mapping(
        400, bbc, bbc_src, "b04v89fn", "movie", 10601,
        _content(bbc, bbc_src, "b04v89fn", "movie", "Peter Pan"),
    )
    _mapping(
        386, bbc, bbc_src, "b06s2h64", "movie", 12092,
        _content(bbc, bbc_src, "b06s2h64", "movie", "Hansel & Gretel"),
    )
    _mapping(
        388, bbc, bbc_src, "m000c74q", "movie", 527634,
        _content(bbc, bbc_src, "m000c74q", "movie", "Hansel & Gretel 2"),
    )
    _mapping(
        1, cbc, cbc_src, "drive", "movie", 64690,
        _content(cbc, cbc_src, "drive", "movie", "Drive"),
    )
    _mapping(
        4760, tubi, tubi_src, "608226", "movie", 1247,
        _content(tubi, tubi_src, "608226", "movie", "The Good Shepherd"),
    )

    for protected_id in APPROVED_PLAN["protected_mapping_ids"]:
        _mapping(
            protected_id,
            other,
            other_src,
            f"protected-{protected_id}",
            "movie",
            700000 + protected_id,
            None,
        )

    unrelated_mapping = _mapping(
        90002, other, other_src, "unrelated-content", "movie", 999999, None
    )

    offers = {
        3504: _offer(3504, bbc, bbc_src, gb, "series", 2316, OFFICE_PCID),
        3505: _offer(3505, bbc, bbc_src, gb, "season", 2316, OFFICE_PCID, season=1),
        3512: _offer(3512, bbc, bbc_src, gb, "season", 2316, OFFICE_PCID, season=2),
        3519: _offer(3519, bbc, bbc_src, gb, "movie", 10601, "b04v89fn"),
        3455: _offer(3455, bbc, bbc_src, gb, "movie", 12092, "b06s2h64"),
        3457: _offer(3457, bbc, bbc_src, gb, "movie", 527634, "m000c74q"),
        1: _offer(1, cbc, cbc_src, ca, "movie", 64690, "drive"),
        10820: _offer(
            10820, tubi, tubi_src, us, "movie", 1247, "608226",
            offer_type="free_with_ads",
        ),
    }
    for episode in range(1, 7):
        offers[3505 + episode] = _offer(
            3505 + episode, bbc, bbc_src, gb, "episode", 2316, OFFICE_PCID,
            season=1, episode=episode,
        )
        offers[3512 + episode] = _offer(
            3512 + episode, bbc, bbc_src, gb, "episode", 2316, OFFICE_PCID,
            season=2, episode=episode,
        )

    unrelated_offer = _offer(
        90001, other, other_src, gb, "movie", 999999, "unrelated-content",
        offer_type="subscription",
    )

    OfficeEvidence = OfferEvidence.objects.create(
        id=1,
        source=tubi_src,
        offer_identity=offers[10820].identity,
        provider_slug="tubi",
        country_code="US",
        content_type="movie",
        tmdb_id=1247,
        offer_type="free_with_ads",
        provider_content_id="608226",
        is_present=True,
        authoritative=False,
    )
    unrelated_evidence = OfferEvidence.objects.create(
        id=90003,
        source=other_src,
        offer_identity=unrelated_offer.identity,
        provider_slug="unrelated_provider",
        country_code="GB",
        content_type="movie",
        tmdb_id=999999,
        offer_type="subscription",
        provider_content_id="unrelated-content",
        is_present=True,
    )
    unrelated_price = OfferPrice.objects.create(
        id=90004,
        source=other_src,
        offer_identity=unrelated_offer.identity,
        provider_slug="unrelated_provider",
        country_code="GB",
        content_type="movie",
        tmdb_id=999999,
        offer_type="subscription",
        provider_content_id="unrelated-content",
        amount=Decimal("1.99"),
        currency="GBP",
    )

    movies = {}
    for tmdb_id, title, content_type, year in (
        (10601, "Peter Pan", "movie", 2003),
        (12092, "Hansel & Gretel", "movie", 2013),
        (527634, "Hansel & Gretel: Witch Hunters 2", "movie", 2018),
        (64690, "Drive", "movie", 2011),
        (1247, "The Good Shepherd", "movie", 2006),
        (2316, "The Office", "series", 2005),
    ):
        movies[tmdb_id] = Movie.objects.create(
            tmdb_id=tmdb_id, title=title, year=year, content_type=content_type
        )

    return {
        "providers": {"bbc": bbc, "cbc": cbc, "tubi": tubi, "other": other},
        "sources": {"bbc": bbc_src, "cbc": cbc_src, "tubi": tubi_src, "other": other_src},
        "countries": {"gb": gb, "ca": ca, "us": us},
        "offers": offers,
        "unrelated_offer": unrelated_offer,
        "unrelated_mapping": unrelated_mapping,
        "unrelated_evidence": unrelated_evidence,
        "unrelated_price": unrelated_price,
        "movies": movies,
    }


class RemediationTestCase(TestCase):
    def setUp(self):
        self.world = seed_world()

    def write_manifest(self, manifest, directory):
        path = Path(directory) / "manifest.json"
        mapping_remediation.write_manifest(path, manifest)
        return path

    def make_manifest_file(self, directory, manifest=None):
        return self.write_manifest(manifest or build_manifest(), directory)

    def confirm_for(self, path, ticket="AVAIL-1234"):
        manifest = mapping_remediation.load_manifest(path)
        return f"{ticket}@{confirmation_digest_prefix(manifest['digest'])}"

    def apply(self, path, confirm=None, stdout=None, stderr=None):
        if confirm is None:
            confirm = self.confirm_for(path)
        with patch.object(
            mapping_remediation, "verify_local_matcher_fix", return_value=True
        ), patch.object(
            mapping_remediation,
            "acquire_maintenance_barrier",
            side_effect=lambda: FakeVerifiedBarrier(),
        ):
            call_command(
                "remediate_mapping_audit",
                "--manifest",
                str(path),
                "--apply",
                "--confirm",
                confirm,
                stdout=stdout or StringIO(),
                stderr=stderr or StringIO(),
            )

    def assert_targets_intact(self):
        self.assertTrue(
            ContentMapping.objects.filter(
                id=399, match_method="title_year", tmdb_id=2316
            ).exists()
        )
        for mapping_id in APPROVED_PLAN["delete_mapping_ids"]:
            self.assertTrue(
                ContentMapping.objects.filter(id=mapping_id).exists(),
                f"mapping {mapping_id} should still exist",
            )
        self.assertEqual(
            Offer.objects.filter(
                id__in=APPROVED_PLAN["delete_offer_ids"]
            ).count(),
            len(APPROVED_PLAN["delete_offer_ids"]),
        )
        self.assertTrue(OfferEvidence.objects.filter(id=1).exists())


class ApprovedPlanTests(TestCase):
    def test_allow_list_is_exactly_the_audited_plan(self):
        self.assertEqual(APPROVED_PLAN["update_mapping"]["id"], 399)
        self.assertEqual(APPROVED_PLAN["update_mapping"]["tmdb_id"], 2996)
        self.assertEqual(APPROVED_PLAN["update_mapping"]["match_method"], "manual")
        self.assertEqual(APPROVED_PLAN["update_mapping"]["confidence"], 1.0)
        self.assertEqual(APPROVED_PLAN["delete_mapping_ids"], [400, 386, 388, 1, 4760])
        self.assertEqual(APPROVED_PLAN["delete_evidence_ids"], [1])
        self.assertEqual(
            APPROVED_PLAN["protected_mapping_ids"],
            [128, 133, 146, 151, 419, 4369, 4375, 4376],
        )
        self.assertEqual(len(APPROVED_PLAN["delete_offer_ids"]), 20)
        self.assertEqual(
            sorted(APPROVED_PLAN["delete_offer_ids"]),
            sorted(list(range(3504, 3520)) + [3455, 3457, 1, 10820]),
        )
        self.assertEqual(APPROVED_PLAN["expected_counts"]["mappings"], 6)
        self.assertEqual(APPROVED_PLAN["expected_counts"]["offers"], 20)
        self.assertEqual(APPROVED_PLAN["expected_counts"]["evidence"], 1)
        self.assertEqual(APPROVED_PLAN["expected_counts"]["offer_prices"], 0)
        self.assertEqual(APPROVED_PLAN["expected_counts"]["protected_mappings"], 8)

    def test_confirmation_rejects_generic_words(self):
        digest = "sha256:" + "a" * 64
        for value in (
            "", "  ", "yes", "Y", "confirm", "apply", "true", "ok", "1",
            "yes@aaaaaaaaaaaa", "confirm@aaaaaaaaaaaa",
        ):
            with self.assertRaises(mapping_remediation.RemediationError):
                validate_confirmation(value, manifest_digest=digest)

    def test_confirmation_accepts_ticket_and_matching_prefix(self):
        digest = "sha256:" + "0123456789ab" + "b" * 52
        self.assertEqual(
            validate_confirmation(
                "AVAIL-1234@0123456789ab", manifest_digest=digest
            ),
            "AVAIL-1234@0123456789ab",
        )
        self.assertEqual(
            validate_confirmation(
                "  GH-42@0123456789AB  ", manifest_digest=digest
            ),
            "GH-42@0123456789ab",
        )

    def test_confirmation_rejects_wrong_digest_prefix(self):
        digest = "sha256:" + "0123456789ab" + "b" * 52
        with self.assertRaises(mapping_remediation.RemediationError) as caught:
            validate_confirmation(
                "AVAIL-1234@ffffffffffff", manifest_digest=digest
            )
        self.assertIn("does not match", str(caught.exception))

    def test_confirmation_rejects_malformed_prefix(self):
        digest = "sha256:" + "0123456789ab" + "b" * 52
        for value in (
            "AVAIL-1234",
            "AVAIL-1234@",
            "AVAIL-1234@0123456789",
            "AVAIL-1234@0123456789abc",
            "AVAIL-1234@not-hex-prefix",
            "AVAIL-1234@@0123456789ab",
        ):
            with self.assertRaises(mapping_remediation.RemediationError):
                validate_confirmation(value, manifest_digest=digest)



class ManifestGenerationTests(RemediationTestCase):
    def test_dry_run_writes_nothing_to_the_database(self):
        before_counts = (
            ContentMapping.objects.count(),
            Offer.objects.count(),
            OfferEvidence.objects.count(),
            OfferPrice.objects.count(),
        )
        before_updated = ContentMapping.objects.get(id=399).updated_at
        before_last_seen = Offer.objects.get(id=3504).last_seen

        out = StringIO()
        call_command("remediate_mapping_audit", stdout=out)

        self.assertEqual(
            (
                ContentMapping.objects.count(),
                Offer.objects.count(),
                OfferEvidence.objects.count(),
                OfferPrice.objects.count(),
            ),
            before_counts,
        )
        self.assertEqual(ContentMapping.objects.get(id=399).updated_at, before_updated)
        self.assertEqual(Offer.objects.get(id=3504).last_seen, before_last_seen)
        self.assertIn("No database rows were changed.", out.getvalue())
        self.assertIn(mapping_remediation.MANIFEST_SCHEMA, out.getvalue())

    def test_write_manifest_produces_a_digest_verifiable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            out = StringIO()
            call_command(
                "remediate_mapping_audit", "--write-manifest", str(path), stdout=out
            )
            self.assertTrue(path.exists())
            loaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(loaded["schema"], mapping_remediation.MANIFEST_SCHEMA)
        self.assertEqual(loaded["digest"], compute_digest(loaded))
        self.assertEqual(loaded["database"], mapping_remediation.database_identity())
        self.assertFalse(ContentMapping.objects.get(id=399).match_method == "manual")

    def test_generation_fails_when_a_target_row_is_missing(self):
        Offer.objects.filter(id=3519).delete()
        with self.assertRaises(CommandError):
            call_command("remediate_mapping_audit", stdout=StringIO())

    def test_generation_fails_when_an_extra_row_is_in_scope(self):
        world = self.world
        _offer(
            90010,
            world["providers"]["bbc"],
            world["sources"]["bbc"],
            world["countries"]["gb"],
            "movie",
            555,
            OFFICE_PCID,
        )
        with self.assertRaises(CommandError):
            call_command("remediate_mapping_audit", stdout=StringIO())

    def test_generation_fails_when_a_protected_mapping_changed(self):
        ContentMapping.objects.filter(id=128).delete()
        with self.assertRaises(CommandError):
            call_command("remediate_mapping_audit", stdout=StringIO())


class CanonicalDigestTests(RemediationTestCase):
    """The digest must be a reproducible hash of the mutation contract.

    The review gate is the pair (manifest file, digest prefix). A digest that
    changes on every dry run cannot be reviewed; a digest that ignores material
    fields would let an edited plan through. These tests pin both sides.
    """

    def test_identical_plans_at_different_times_share_a_digest(self):
        first = build_manifest(now=timezone.now())
        second = build_manifest(now=timezone.now() - timedelta(days=30))
        self.assertNotEqual(first["generated_at"], second["generated_at"])
        self.assertEqual(first["digest"], second["digest"])

    def test_volatile_metadata_is_not_part_of_the_digest(self):
        manifest = build_manifest()
        edited = json.loads(json.dumps(manifest))
        edited["generated_at"] = "1999-12-31T23:59:59+00:00"
        self.assertEqual(compute_digest(manifest), compute_digest(edited))

    def test_every_material_mutation_changes_the_digest(self):
        base = build_manifest()

        def mutate(label, change):
            edited = json.loads(json.dumps(base))
            change(edited)
            self.assertNotEqual(
                compute_digest(edited), base["digest"],
                f"{label} must change the digest",
            )

        mutate("approved target id",
               lambda m: m["plan"]["delete_offer_ids"].pop())
        mutate("proposed update",
               lambda m: m["plan"]["update_mapping"].update(tmdb_id=1))
        mutate("target row column",
               lambda m: m["targets"]["offers"][0]["columns"].update(
                   offer_type="subscription"))
        mutate("target association",
               lambda m: m["targets"]["mappings"][0]["associations"].update(
                   tmdb_id=1))
        mutate("protected mapping",
               lambda m: m["targets"]["protected_mappings"][0]["associations"].update(
                   tmdb_id=1))
        mutate("database identity",
               lambda m: m["database"].update(name="another_database"))
        mutate("target removed",
               lambda m: m["targets"]["evidence"].pop())

    def test_unordered_collections_do_not_change_the_digest(self):
        base = build_manifest()
        reordered = json.loads(json.dumps(base))
        for field in (
            "delete_mapping_ids",
            "delete_offer_ids",
            "delete_evidence_ids",
            "protected_mapping_ids",
        ):
            reordered["plan"][field] = list(reversed(reordered["plan"][field]))
        for section in ("mappings", "offers", "evidence", "protected_mappings"):
            reordered["targets"][section] = list(
                reversed(reordered["targets"][section])
            )
        reordered["targets"]["offer_prices"]["ids"] = list(
            reversed(reordered["targets"]["offer_prices"]["ids"])
        )
        self.assertEqual(compute_digest(reordered), base["digest"])

    def test_pre_canonical_manifest_schema_is_rejected(self):
        manifest = build_manifest()
        manifest["schema"] = "remediate_mapping_audit/manifest-v2"
        manifest["digest"] = compute_digest(manifest)
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp, manifest)
            with self.assertRaises(CommandError) as caught:
                call_command(
                    "remediate_mapping_audit", "--manifest", str(path),
                    stdout=StringIO(), stderr=StringIO(),
                )
        self.assertIn("unsupported manifest schema", str(caught.exception))

    def test_tampered_target_is_rejected_despite_a_recomputed_digest(self):
        """The digest is not trusted on its own: live rows are revalidated."""
        manifest = build_manifest()
        manifest["targets"]["offers"][0]["columns"]["offer_type"] = "subscription"
        manifest["digest"] = compute_digest(manifest)
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp, manifest)
            with self.assertRaises(CommandError) as caught:
                self.apply(path)
        self.assertIn("does not match", str(caught.exception))
        self.assert_targets_intact()


class ManifestValidationTests(RemediationTestCase):
    def test_valid_manifest_passes_preflight_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp)
            out = StringIO()
            call_command(
                "remediate_mapping_audit", "--manifest", str(path), stdout=out
            )
        self.assertIn("Validation only", out.getvalue())
        self.assertTrue(
            ContentMapping.objects.filter(
                id=399, match_method="title_year", tmdb_id=2316
            ).exists()
        )
        self.assertEqual(
            Offer.objects.filter(
                id__in=APPROVED_PLAN["delete_offer_ids"]
            ).count(),
            len(APPROVED_PLAN["delete_offer_ids"]),
        )

    def test_missing_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "absent.json"
            with self.assertRaises(CommandError):
                call_command(
                    "remediate_mapping_audit", "--manifest", str(path),
                    stdout=StringIO(),
                )

    def test_malformed_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(CommandError):
                call_command(
                    "remediate_mapping_audit", "--manifest", str(path),
                    stdout=StringIO(),
                )

    def test_edited_manifest_is_rejected(self):
        manifest = build_manifest()
        manifest["targets"]["offers"][0]["columns"]["offer_type"] = "subscription"
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp, manifest)
            with self.assertRaises(CommandError) as caught:
                call_command(
                    "remediate_mapping_audit", "--manifest", str(path),
                    stdout=StringIO(),
                )
        self.assertIn("digest", str(caught.exception))

    def test_manifest_from_another_database_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp)
            with patch.object(
                mapping_remediation,
                "database_identity",
                return_value=identity(name="somewhere_else"),
            ):
                with self.assertRaises(CommandError) as caught:
                    call_command(
                        "remediate_mapping_audit", "--manifest", str(path),
                        stdout=StringIO(),
                    )
        self.assertIn("different database", str(caught.exception))

    def _manifest_with_identity(self, directory, target_identity):
        with patch.object(
            mapping_remediation, "database_identity", return_value=target_identity
        ):
            manifest = build_manifest()
        return self.make_manifest_file(directory, manifest)

    def test_same_name_database_on_a_different_host_is_rejected(self):
        reviewed = identity(host="db-a", address="10.0.0.11")
        live = identity(host="db-b", address="10.0.0.12")
        with tempfile.TemporaryDirectory() as tmp:
            path = self._manifest_with_identity(tmp, reviewed)
            with patch.object(
                mapping_remediation, "database_identity", return_value=live
            ):
                with self.assertRaises(CommandError) as caught:
                    call_command(
                        "remediate_mapping_audit", "--manifest", str(path),
                        stdout=StringIO(),
                    )
        self.assertIn("different database instance", str(caught.exception))

    def test_same_name_database_on_a_different_port_is_rejected(self):
        reviewed = identity(server_port=5432, port="5432")
        live = identity(server_port=6432, port="6432")
        with tempfile.TemporaryDirectory() as tmp:
            path = self._manifest_with_identity(tmp, reviewed)
            with patch.object(
                mapping_remediation, "database_identity", return_value=live
            ):
                with self.assertRaises(CommandError):
                    call_command(
                        "remediate_mapping_audit", "--manifest", str(path),
                        stdout=StringIO(),
                    )

    def test_same_name_database_with_a_different_cluster_identifier_is_rejected(self):
        reviewed = identity(system_identifier="7300000000000000001")
        live = identity(system_identifier="7300000000000000002")
        with tempfile.TemporaryDirectory() as tmp:
            path = self._manifest_with_identity(tmp, reviewed)
            with patch.object(
                mapping_remediation, "database_identity", return_value=live
            ):
                with self.assertRaises(CommandError):
                    call_command(
                        "remediate_mapping_audit", "--manifest", str(path),
                        stdout=StringIO(),
                    )

    def test_unprovable_postgres_identity_is_rejected(self):
        weak = identity(address=None, server_port=None, system_identifier=None)
        self.assertEqual(weak["strength"], "weak")
        with tempfile.TemporaryDirectory() as tmp:
            path = self._manifest_with_identity(tmp, weak)
            with patch.object(
                mapping_remediation, "database_identity", return_value=weak
            ):
                with self.assertRaises(CommandError) as caught:
                    call_command(
                        "remediate_mapping_audit", "--manifest", str(path),
                        stdout=StringIO(),
                    )
        self.assertIn("cannot prove which PostgreSQL server", str(caught.exception))

    def test_manifest_v1_is_rejected(self):
        manifest = build_manifest()
        manifest["schema"] = "remediate_mapping_audit/manifest-v1"
        manifest["digest"] = compute_digest(manifest)
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp, manifest)
            with self.assertRaises(CommandError) as caught:
                call_command(
                    "remediate_mapping_audit", "--manifest", str(path),
                    stdout=StringIO(),
                )
        self.assertIn("unsupported manifest schema", str(caught.exception))


    def test_manifest_from_a_different_plan_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp)
            other_plan = json.loads(json.dumps(APPROVED_PLAN))
            other_plan["delete_offer_ids"] = other_plan["delete_offer_ids"][:-1]
            with patch.object(
                mapping_remediation, "APPROVED_PLAN", other_plan
            ):
                with self.assertRaises(CommandError) as caught:
                    call_command(
                        "remediate_mapping_audit", "--manifest", str(path),
                        stdout=StringIO(),
                    )
        self.assertIn("approved plan", str(caught.exception))


class MutationGateTests(RemediationTestCase):
    def test_apply_requires_a_manifest(self):
        with self.assertRaises(CommandError):
            call_command(
                "remediate_mapping_audit", "--apply", "--confirm", "AVAIL-1234",
                stdout=StringIO(),
            )

    def test_apply_requires_a_confirmation(self):
        with self.assertRaises(CommandError):
            call_command(
                "remediate_mapping_audit", "--apply", "--manifest", "whatever",
                stdout=StringIO(),
            )

    def test_apply_rejects_a_generic_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp)
            prefix = confirmation_digest_prefix(
                mapping_remediation.load_manifest(path)["digest"]
            )
            with self.assertRaises(CommandError):
                self.apply(path, confirm="yes")
            with self.assertRaises(CommandError):
                self.apply(path, confirm=f"yes@{prefix}")

    def test_apply_refuses_when_the_matcher_fix_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp)
            with patch.object(
                mapping_remediation, "verify_local_matcher_fix", return_value=False
            ), patch.object(
                mapping_remediation,
                "acquire_maintenance_barrier",
                side_effect=lambda: FakeVerifiedBarrier(),
            ):
                with self.assertRaises(CommandError) as caught:
                    call_command(
                        "remediate_mapping_audit", "--manifest", str(path),
                        "--apply", "--confirm", self.confirm_for(path),
                        stdout=StringIO(), stderr=StringIO(),
                    )
        self.assertIn("matcher", str(caught.exception))
        self.assert_targets_intact()

    def test_apply_refuses_when_the_barrier_cannot_be_acquired(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp)
            with patch.object(
                mapping_remediation, "verify_local_matcher_fix", return_value=True
            ), patch.object(
                mapping_remediation,
                "acquire_maintenance_barrier",
                side_effect=write_barrier.MaintenanceInProgress(
                    "another writer holds the barrier"
                ),
            ):
                with self.assertRaises(CommandError) as caught:
                    call_command(
                        "remediate_mapping_audit", "--manifest", str(path),
                        "--apply", "--confirm", self.confirm_for(path),
                        stdout=StringIO(), stderr=StringIO(),
                    )
        self.assertIn("could not acquire", str(caught.exception))
        self.assert_targets_intact()

    def test_apply_refuses_an_unverified_barrier_outside_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp)
            unverified = write_barrier.MaintenanceBarrier(
                verified=False, held=False
            )
            with patch.object(
                mapping_remediation, "verify_local_matcher_fix", return_value=True
            ), patch.object(
                mapping_remediation,
                "acquire_maintenance_barrier",
                return_value=unverified,
            ), patch.object(
                mapping_remediation,
                "unverified_barrier_allowed",
                return_value=False,
            ):
                with self.assertRaises(CommandError) as caught:
                    call_command(
                        "remediate_mapping_audit", "--manifest", str(path),
                        "--apply", "--confirm", self.confirm_for(path),
                        stdout=StringIO(), stderr=StringIO(),
                    )
        self.assertIn("cannot prove", str(caught.exception))
        self.assert_targets_intact()



class MutationTests(RemediationTestCase):
    def test_apply_updates_mapping_399_and_deletes_only_the_allow_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp)
            out = StringIO()
            self.apply(path, stdout=out)

        mapping = ContentMapping.objects.get(id=399)
        self.assertEqual(mapping.tmdb_id, 2996)
        self.assertEqual(mapping.match_method, "manual")
        self.assertEqual(mapping.confidence, 1.0)

        for mapping_id in APPROVED_PLAN["delete_mapping_ids"]:
            self.assertFalse(ContentMapping.objects.filter(id=mapping_id).exists())
        for offer_id in APPROVED_PLAN["delete_offer_ids"]:
            self.assertFalse(Offer.objects.filter(id=offer_id).exists())
        self.assertFalse(OfferEvidence.objects.filter(id=1).exists())

        for protected_id in APPROVED_PLAN["protected_mapping_ids"]:
            self.assertTrue(ContentMapping.objects.filter(id=protected_id).exists())

        self.assertFalse(
            OfferEvidence.objects.exclude(
                offer_identity__in=Offer.objects.values("identity")
            ).exists()
        )
        self.assertFalse(
            OfferPrice.objects.exclude(
                offer_identity__in=Offer.objects.values("identity")
            ).exists()
        )
        self.assertIn("Mutation committed and post-commit validation passed.", out.getvalue())
        self.assertNotIn("POST-COMMIT VALIDATION FAILED", out.getvalue())

    def test_unrelated_rows_are_untouched(self):
        manifests_before = ContentMapping.objects.get(id=90002)
        offers_before = Offer.objects.get(id=90001)
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp)
            self.apply(path)

        self.assertTrue(ContentMapping.objects.filter(id=90002).exists())
        self.assertEqual(ContentMapping.objects.get(id=90002).tmdb_id, manifests_before.tmdb_id)
        self.assertTrue(Offer.objects.filter(id=90001).exists())
        self.assertEqual(Offer.objects.get(id=90001).offer_type, offers_before.offer_type)
        self.assertTrue(OfferEvidence.objects.filter(id=90003).exists())
        self.assertTrue(OfferPrice.objects.filter(id=90004).exists())

    def test_unrelated_orphan_is_not_purged_and_aborts(self):
        """No global purge: a pre-existing orphan must fail closed, not vanish."""
        manifest = build_manifest()
        orphan = OfferEvidence.objects.create(
            id=90005,
            source=self.world["sources"]["other"],
            offer_identity="orphan|GB|movie|424242|||free|gone",
            provider_slug="unrelated_provider",
            country_code="GB",
            content_type="movie",
            tmdb_id=424242,
            offer_type="free",
            provider_content_id="gone",
            is_present=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp, manifest)
            with self.assertRaises(CommandError) as caught:
                self.apply(path)

        self.assertIn("orphaned OfferEvidence", str(caught.exception))
        self.assertTrue(OfferEvidence.objects.filter(id=orphan.id).exists())
        self.assert_targets_intact()

    def test_failed_invariant_rolls_back_update_and_deletes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp)
            with patch.object(
                mapping_remediation,
                "_assert_invariants",
                side_effect=mapping_remediation.RemediationError("boom"),
            ):
                with self.assertRaises(CommandError):
                    self.apply(path)

        self.assert_targets_intact()
        self.assertEqual(
            Offer.objects.filter(
                id__in=APPROVED_PLAN["delete_offer_ids"]
            ).count(),
            len(APPROVED_PLAN["delete_offer_ids"]),
        )
        # The unrelated dependent rows must survive a failed transaction too.
        self.assertTrue(OfferEvidence.objects.filter(id=90003).exists())
        self.assertTrue(OfferPrice.objects.filter(id=90004).exists())

    def test_missing_target_aborts_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp)
            Offer.objects.filter(id=3519).delete()
            with self.assertRaises(CommandError):
                self.apply(path)

        self.assertTrue(
            ContentMapping.objects.filter(
                id=399, match_method="title_year", tmdb_id=2316
            ).exists()
        )
        for offer_id in APPROVED_PLAN["delete_offer_ids"]:
            if offer_id == 3519:
                continue
            self.assertTrue(Offer.objects.filter(id=offer_id).exists())

    def test_extra_targeted_row_aborts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp)
            world = self.world
            _offer(
                90011,
                world["providers"]["bbc"],
                world["sources"]["bbc"],
                world["countries"]["gb"],
                "movie",
                777,
                OFFICE_PCID,
            )
            with self.assertRaises(CommandError):
                self.apply(path)

        self.assert_targets_intact()

    def test_changed_protected_mapping_aborts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp)
            ContentMapping.objects.filter(id=128).update(tmdb_id=1234567)
            with self.assertRaises(CommandError):
                self.apply(path)

        self.assert_targets_intact()

    def test_changed_target_row_aborts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp)
            Offer.objects.filter(id=3504).update(last_seen=timezone.now())
            with self.assertRaises(CommandError):
                self.apply(path)

        self.assert_targets_intact()

    def test_post_commit_checks_all_pass(self):
        manifest = build_manifest()
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp, manifest)
            self.apply(path)

        checks = mapping_remediation.post_commit_checks(manifest)
        failed = [check for check in checks if not check["ok"]]
        self.assertEqual(failed, [], f"post-commit failures: {failed}")
        self.assertTrue(
            any(check["name"].startswith("read_path_") for check in checks)
        )

    def test_recomputed_digest_after_a_material_edit_requires_a_new_confirmation(self):
        manifest = build_manifest()
        old_confirmation = (
            "AVAIL-1234@"
            + confirmation_digest_prefix(manifest["digest"])
        )
        manifest["targets"]["offers"][0]["columns"]["offer_type"] = "subscription"
        manifest["digest"] = compute_digest(manifest)
        self.assertNotEqual(
            confirmation_digest_prefix(manifest["digest"]),
            old_confirmation.split("@", 1)[1],
        )
        with self.assertRaises(mapping_remediation.RemediationError):
            validate_confirmation(
                old_confirmation, manifest_digest=manifest["digest"]
            )
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp, manifest)
            with self.assertRaises(CommandError):
                self.apply(path, confirm=old_confirmation)
            self.assert_targets_intact()

    def test_post_commit_failure_is_nonzero_and_does_not_claim_success(self):
        manifest = build_manifest()
        out, err = StringIO(), StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp, manifest)
            with patch.object(
                mapping_remediation,
                "post_commit_checks",
                return_value=[
                    {
                        "name": "post_mapping_399_is_manual_2996",
                        "ok": False,
                        "detail": "forced failure",
                    }
                ],
            ):
                with self.assertRaises(CommandError) as caught:
                    self.apply(path, stdout=out, stderr=err)

        self.assertIn("post-commit validation failed", str(caught.exception))
        self.assertIn("POST-COMMIT VALIDATION FAILED", err.getvalue())
        self.assertIn("post_mapping_399_is_manual_2996", err.getvalue())
        self.assertIn("ALREADY COMMITTED", err.getvalue())
        self.assertIn("rollback is no longer available", err.getvalue())
        self.assertNotIn("post-commit validation passed", out.getvalue())
        # The transaction really committed; the command must not pretend it did not.
        self.assertEqual(ContentMapping.objects.get(id=399).tmdb_id, 2996)

    def test_post_commit_validation_crash_still_reports_committed(self):
        manifest = build_manifest()
        out, err = StringIO(), StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_manifest_file(tmp, manifest)
            with patch.object(
                mapping_remediation,
                "post_commit_checks",
                side_effect=RuntimeError("connection lost during validation"),
            ):
                with self.assertRaises(CommandError) as caught:
                    self.apply(path, stdout=out, stderr=err)

        self.assertIn("could not complete", str(caught.exception))
        self.assertIn("POST-COMMIT VALIDATION COULD NOT COMPLETE", err.getvalue())
        self.assertIn("RuntimeError", err.getvalue())
        self.assertIn("ALREADY COMMITTED", err.getvalue())
        self.assertIn("rollback is no longer available", err.getvalue())
        self.assertIn("Keep worker", err.getvalue())
        self.assertNotIn("post-commit validation passed", out.getvalue())
        # The mutation committed before the validation crash.
        self.assertEqual(ContentMapping.objects.get(id=399).tmdb_id, 2996)


class WriterBarrierTests(TestCase):
    """The shared/exclusive advisory-lock protocol around refresh writes."""

    def test_writer_refuses_when_maintenance_holds_exclusive(self):
        with patch.object(
            write_barrier, "advisory_locks_supported", return_value=True
        ), patch.object(
            write_barrier, "_try_lock", return_value=False
        ), patch.object(write_barrier, "_unlock") as unlock:
            with self.assertRaises(write_barrier.MaintenanceInProgress):
                with write_barrier.writer_barrier("test refresh"):
                    self.fail("the writer body must not run")
        unlock.assert_not_called()

    def test_writer_takes_shared_lock_and_releases_it(self):
        with patch.object(
            write_barrier, "advisory_locks_supported", return_value=True
        ), patch.object(
            write_barrier, "_try_lock", return_value=True
        ) as try_lock, patch.object(
            write_barrier, "_unlock"
        ) as unlock:
            with write_barrier.writer_barrier("test refresh"):
                pass
        try_lock.assert_called_once_with(exclusive=False)
        unlock.assert_called_once_with(exclusive=False)

    def test_writer_barrier_reuses_one_acquisition_when_nested(self):
        with patch.object(
            write_barrier, "advisory_locks_supported", return_value=True
        ), patch.object(
            write_barrier, "_try_lock", return_value=True
        ) as try_lock, patch.object(
            write_barrier, "_unlock"
        ) as unlock:
            with write_barrier.writer_barrier("outer write"):
                with write_barrier.writer_barrier("nested projection delete"):
                    pass

        try_lock.assert_called_once_with(exclusive=False)
        unlock.assert_called_once_with(exclusive=False)

    def test_nested_writer_still_releases_after_inner_error(self):
        with patch.object(
            write_barrier, "advisory_locks_supported", return_value=True
        ), patch.object(
            write_barrier, "_try_lock", return_value=True
        ) as try_lock, patch.object(
            write_barrier, "_unlock"
        ) as unlock:
            with self.assertRaises(RuntimeError):
                with write_barrier.writer_barrier("outer write"):
                    with write_barrier.writer_barrier("nested projection delete"):
                        raise RuntimeError("inner failure")

        try_lock.assert_called_once_with(exclusive=False)
        unlock.assert_called_once_with(exclusive=False)
        self.assertEqual(write_barrier._barrier_state.depth, 0)

    def test_maintenance_refuses_when_a_shared_writer_is_active(self):
        with patch.object(
            write_barrier, "advisory_locks_supported", return_value=True
        ), patch.object(write_barrier, "_try_lock", return_value=False):
            with self.assertRaises(write_barrier.MaintenanceInProgress):
                write_barrier.acquire_maintenance_barrier()

    def test_maintenance_aborts_on_legacy_other_advisory_locks(self):
        with patch.object(
            write_barrier, "advisory_locks_supported", return_value=True
        ), patch.object(
            write_barrier, "_try_lock", return_value=True
        ), patch.object(
            write_barrier, "other_advisory_locks", return_value=2
        ), patch.object(write_barrier, "_unlock") as unlock:
            with self.assertRaises(write_barrier.MaintenanceInProgress) as caught:
                write_barrier.acquire_maintenance_barrier()
        self.assertIn("pre-barrier writer", str(caught.exception))
        unlock.assert_called_once_with(exclusive=True)

    def test_maintenance_is_unverified_without_postgres(self):
        with patch.object(
            write_barrier, "advisory_locks_supported", return_value=False
        ):
            barrier = write_barrier.acquire_maintenance_barrier()
        self.assertFalse(barrier.verified)
        self.assertFalse(barrier.held)
        barrier.release()  # harmless, must not attempt an unlock


@skipUnless(
    connection.vendor == "postgresql",
    "PostgreSQL advisory-lock integration test (run against an isolated test DB)",
)
class PostgresBarrierIntegrationTests(TestCase):
    """Real cross-connection proof of the shared/exclusive lock protocol.

    SQLite cannot test this: the whole point is that a second PostgreSQL
    session blocks or is blocked.  The CI/local test database is an isolated
    ``test_*`` database created and dropped by Django's test runner.
    """

    def _second_connection(self):
        return connections.create_connection("default")

    def test_shared_writer_blocks_maintenance_barrier(self):
        other = self._second_connection()
        try:
            with other.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_try_advisory_lock_shared(%s)",
                    [write_barrier.WRITER_BARRIER_KEY],
                )
                self.assertTrue(cursor.fetchone()[0])
            with self.assertRaises(write_barrier.MaintenanceInProgress):
                write_barrier.acquire_maintenance_barrier()
        finally:
            with other.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_unlock_shared(%s)",
                    [write_barrier.WRITER_BARRIER_KEY],
                )
            other.close()

    def test_exclusive_maintenance_blocks_new_writer(self):
        other = self._second_connection()
        try:
            with other.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_try_advisory_lock(%s)",
                    [write_barrier.WRITER_BARRIER_KEY],
                )
                self.assertTrue(cursor.fetchone()[0])
            with self.assertRaises(write_barrier.MaintenanceInProgress):
                with write_barrier.writer_barrier("integration test refresh"):
                    self.fail("writer must not run while maintenance holds the lock")
        finally:
            with other.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_unlock(%s)",
                    [write_barrier.WRITER_BARRIER_KEY],
                )
            other.close()

    def test_maintenance_barrier_acquires_and_releases(self):
        barrier = write_barrier.acquire_maintenance_barrier()
        self.assertTrue(barrier.verified)
        self.assertTrue(barrier.held)
        barrier.release()
        self.assertFalse(barrier.held)
        # A new writer can proceed once the barrier is released.
        with write_barrier.writer_barrier("after maintenance"):
            pass



