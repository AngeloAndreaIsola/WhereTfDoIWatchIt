"""Operator-only CLI command for bounded targeted offer recovery."""
from __future__ import annotations

import json
import logging

from django.core.management.base import BaseCommand, CommandError

from availability.services.offer_recovery import recover_provider_evidence

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Recover canonical offer evidence for an existing, mapped ProviderContent entry "
        "under a strict finite HTTP budget without running a full catalogue walk."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--provider-content-id",
            type=int,
            required=True,
            help="Primary key (ID) of the ProviderContent row to recover.",
        )
        parser.add_argument(
            "--max-http-requests",
            type=int,
            default=None,
            help="Maximum finite HTTP requests permitted for this execution. Required with --apply.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            default=False,
            help="Execute live targeted fetch and apply offer persistence. Without this flag, runs in strictly offline preview mode.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            default=False,
            help="Output machine-readable JSON summary.",
        )

    def handle(self, *args, **options):
        provider_content_pk = options["provider_content_id"]
        max_http_requests = options["max_http_requests"]
        apply = options["apply"]
        as_json = options["json"]

        # Validate arguments
        if provider_content_pk <= 0:
            raise CommandError("--provider-content-id must be a positive integer.")

        if apply:
            if max_http_requests is None or max_http_requests <= 0:
                raise CommandError(
                    "--max-http-requests must be supplied as a positive integer when --apply is set."
                )
        elif max_http_requests is not None:
            # max_http_requests given without --apply
            if max_http_requests <= 0:
                raise CommandError("--max-http-requests must be a positive integer.")

        result = recover_provider_evidence(
            provider_content_pk=provider_content_pk,
            max_http_requests=max_http_requests,
            apply=apply,
        )

        res_dict = result.to_dict()

        if as_json:
            self.stdout.write(json.dumps(res_dict, indent=2))
        else:
            self.stdout.write(
                f"Targeted Offer Recovery Result:\n"
                f"  Outcome:                 {result.outcome}\n"
                f"  Provider Content PK:     {result.provider_content_pk}\n"
                f"  Provider Content ID:     {result.provider_content_id}\n"
                f"  Provider:                {result.provider_slug}\n"
                f"  Source:                  {result.source_slug}\n"
                f"  TMDb ID:                 {result.tmdb_id}\n"
                f"  Content Type:            {result.content_type}\n"
                f"  HTTP Requests Attempted: {result.http_requests_attempted}\n"
                f"  Offers Created:          {result.offers_created}\n"
                f"  Offers Updated:          {result.offers_updated}\n"
                f"  Offer Identities:        {len(result.offer_identities)}\n"
                f"  Reason Code:             {result.reason_code}\n"
                f"  Applied:                 {result.applied}\n"
            )
            if result.error_detail:
                self.stdout.write(self.style.WARNING(f"  Error Detail:            {result.error_detail}"))

            if result.outcome == "observed_offer":
                self.stdout.write(self.style.SUCCESS("Recovery successfully observed fresh offers."))
            elif result.outcome in ("preview", "no_current_evidence"):
                self.stdout.write(self.style.NOTICE(f"Recovery finished with outcome: {result.outcome}"))
            else:
                self.stdout.write(self.style.ERROR(f"Recovery finished with outcome: {result.outcome}"))
