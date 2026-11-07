"""Management command to plan the impact of a mapping correction.

Inspects the blast radius of correcting a content mapping for a given
provider and provider-content ID to a proposed TMDb ID and content type.

Strictly read-only: never modifies any database rows, never makes external
API calls, and outputs human-readable summaries or inspectable JSON manifests.

Usage:
------
    python manage.py plan_mapping_correction \
        --provider bbc_iplayer \
        --provider-content-id b00jd68z \
        --tmdb-id 2996 \
        --content-type series

    python manage.py plan_mapping_correction \
        --provider bbc_iplayer \
        --provider-content-id b00jd68z \
        --tmdb-id 2996 \
        --content-type series \
        --json

    python manage.py plan_mapping_correction \
        --provider bbc_iplayer \
        --provider-content-id b00jd68z \
        --tmdb-id 2996 \
        --content-type series \
        --write-manifest ./remediation-manifest.json
"""
from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from availability.services.mapping_correction_impact import (
    MappingCorrectionImpactPlanner,
    MappingCorrectionPlannerError,
)


class Command(BaseCommand):
    help = (
        "Evaluate the blast radius and impact of correcting a content mapping "
        "without mutating database records or contacting external APIs."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--provider",
            required=True,
            help="Provider slug (e.g. bbc_iplayer, tubi, cbc_gem).",
        )
        parser.add_argument(
            "--provider-content-id",
            required=True,
            help="Provider-native content ID (e.g. b00jd68z, 608226).",
        )
        parser.add_argument(
            "--tmdb-id",
            type=int,
            required=True,
            help="Proposed TMDb ID (e.g. 2996).",
        )
        parser.add_argument(
            "--content-type",
            required=True,
            help="Proposed content type (movie or series).",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output complete inspectable impact manifest as JSON to stdout.",
        )
        parser.add_argument(
            "--write-manifest",
            default="",
            help="Optional file path to write the JSON impact manifest artifact.",
        )

    def handle(self, *args, **options):
        provider = options["provider"]
        provider_content_id = options["provider_content_id"]
        tmdb_id = options["tmdb_id"]
        content_type = options["content_type"]
        json_output = options["json"]
        write_path = options["write_manifest"]

        try:
            planner = MappingCorrectionImpactPlanner(
                provider=provider,
                provider_content_id=provider_content_id,
                proposed_tmdb_id=tmdb_id,
                proposed_content_type=content_type,
            )
            manifest = planner.plan()
        except MappingCorrectionPlannerError as exc:
            raise CommandError(f"Planner error: {exc}")
        except Exception as exc:
            raise CommandError(f"Unexpected error planning mapping correction: {exc}")

        if write_path:
            out_path = Path(write_path)
            try:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                raise CommandError(f"Could not write manifest to {write_path}: {exc}")
            self.stdout.write(
                self.style.SUCCESS(f"Manifest written to: {out_path.resolve()}")
            )

        if json_output:
            self.stdout.write(json.dumps(manifest, indent=2, sort_keys=True))
            return

        self._render_human_report(manifest)

    def _render_human_report(self, manifest: dict):
        inputs = manifest["inputs"]
        report = manifest["report"]
        db = manifest["database"]

        self.stdout.write("==================================================")
        self.stdout.write("    MAPPING CORRECTION IMPACT PLANNER REPORT      ")
        self.stdout.write("==================================================")
        self.stdout.write(f"Schema               : {manifest['schema']}")
        self.stdout.write(f"Digest               : {manifest['digest']}")
        self.stdout.write(f"Precondition Fingerprint: {manifest['precondition_fingerprint']}")
        self.stdout.write(f"Database             : {db.get('vendor')} / {db.get('name')}")
        self.stdout.write(f"Target Scope         : {inputs['provider']} : {inputs['provider_content_id']}")
        self.stdout.write(f"Proposed Target      : tmdb:{inputs['proposed_tmdb_id']} ({inputs['proposed_content_type']})")
        self.stdout.write("--------------------------------------------------")

        # 1. Existing mapping
        existing = report.get("existing_mapping")
        self.stdout.write("1. Existing Content Mapping:")
        if existing:
            self.stdout.write(f"   Mapping ID         : {existing['id']}")
            self.stdout.write(f"   Current Target     : tmdb:{existing['target_tmdb_id']} ({existing['target_content_type']})")
            self.stdout.write(f"   Match Method       : {existing['match_method']}")
            self.stdout.write(f"   Confidence         : {existing['confidence']}")
            self.stdout.write(f"   Manual Mapping     : {'YES' if existing['is_manual'] else 'NO'}")
            self.stdout.write(f"   Source             : {existing['source']}")
        else:
            self.stdout.write("   (No existing ContentMapping found for this provider-content)")

        # 2. Provider native identity
        native = report.get("provider_native_identity", {})
        self.stdout.write("")
        self.stdout.write("2. Provider-Native Identity:")
        self.stdout.write(f"   Provider Content ID: {native.get('provider_content_id')}")
        self.stdout.write(f"   Catalogue Rows     : {native.get('content_rows_count', 0)}")
        for r in native.get("rows", []):
            self.stdout.write(f"     - [ID {r['id']}] {r['title'] or '(no title)'} ({r['content_type']}) identity={r['identity']}")

        # 3. Targets
        former = report.get("former_tmdb_target")
        proposed = report.get("proposed_tmdb_target")
        self.stdout.write("")
        self.stdout.write("3. TMDb Target Comparison:")
        if former:
            f_str = f"tmdb:{former['tmdb_id']} ({former['content_type']})"
            if former.get("title"):
                f_str += f" - {former['title']} ({former.get('year')})"
            self.stdout.write(f"   Former TMDb        : {f_str}")
        else:
            self.stdout.write("   Former TMDb        : None")

        p_str = f"tmdb:{proposed['tmdb_id']} ({proposed['content_type']})"
        if proposed.get("title"):
            p_str += f" - {proposed['title']} ({proposed.get('year')})"
        self.stdout.write(f"   Proposed TMDb      : {p_str}")

        # 4. Associated records
        offers = report.get("associated_offers", [])
        evidence = report.get("associated_evidence", [])
        prices = report.get("associated_prices", [])
        self.stdout.write("")
        self.stdout.write("4. Associated Records:")
        self.stdout.write(f"   Offers             : {len(offers)}")
        for o in offers:
            self.stdout.write(f"     - [Offer {o['id']}] {o['country']} | {o['offer_type']} | available={o['currently_available']} | {o['identity']}")
        self.stdout.write(f"   OfferEvidence      : {len(evidence)}")
        self.stdout.write(f"   OfferPrice         : {len(prices)}")

        # 5. Corroborating observations
        corrob = report.get("corroborating_observations", {})
        self.stdout.write("")
        self.stdout.write("5. Corroborating Observations from Other Sources:")
        self.stdout.write(f"   Corroborated at Provider : {'YES' if corrob.get('is_corroborated_at_provider') else 'NO'}")
        self.stdout.write(f"   Corroborated Globally    : {'YES' if corrob.get('is_corroborated_globally') else 'NO'}")
        self.stdout.write(f"   Same Provider Other Content: {len(corrob.get('same_provider_other_content_ids', []))}")
        self.stdout.write(f"   Other Providers Offers   : {corrob.get('other_providers_offers_count', 0)}")
        self.stdout.write(f"   Other Sources Evidence   : {corrob.get('other_sources_evidence_count', 0)}")
        self.stdout.write(f"   Other Mappings for Proposed: {len(corrob.get('other_mappings_for_proposed', []))}")

        # 6. Manual mapping conflicts
        conflicts = report.get("manual_mapping_conflicts", {})
        self.stdout.write("")
        self.stdout.write("6. Manual Mapping Conflicts:")
        self.stdout.write(f"   Has Conflicts      : {'YES' if conflicts.get('has_manual_conflicts') else 'NO'}")
        for mc in conflicts.get("manual_conflicts", []):
            self.stdout.write(f"     ! [Conflict: {mc['type']}] {mc['detail']}")
        if not conflicts.get("manual_conflicts"):
            self.stdout.write("     (None)")

        # 7. Potentially invalid availability
        invalid_avail = report.get("potentially_invalid_availability", {})
        self.stdout.write("")
        self.stdout.write("7. Potentially Invalid Availability Analysis:")
        self.stdout.write(f"   Former Active Offers Lost : {invalid_avail.get('former_target_active_offers_lost', 0)}")
        self.stdout.write(f"   Stale Offer Identities    : {invalid_avail.get('stale_offer_identities_count', 0)} / {invalid_avail.get('total_associated_offers', 0)}")
        self.stdout.write(f"   Assessment: {invalid_avail.get('summary')}")
        self.stdout.write("--------------------------------------------------")
        self.stdout.write(self.style.SUCCESS("Read-only inspection completed. No database rows were changed."))
