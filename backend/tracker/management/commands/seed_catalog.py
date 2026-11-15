"""Seed Region rows for provider x country combinations (idempotent)."""
from __future__ import annotations

from django.core.management.base import BaseCommand

from ...models import Region

# All 249 ISO 3166-1 alpha-2 country codes (officially assigned).
ALL_ISO_COUNTRIES = [
    "AD", "AE", "AF", "AG", "AI", "AL", "AM", "AO", "AQ", "AR", "AS", "AT",
    "AU", "AW", "AX", "AZ",
    "BA", "BB", "BD", "BE", "BF", "BG", "BH", "BI", "BJ", "BL", "BM", "BN",
    "BO", "BQ", "BR", "BS", "BT", "BV", "BW", "BY", "BZ",
    "CA", "CC", "CD", "CF", "CG", "CH", "CI", "CK", "CL", "CM", "CN", "CO",
    "CR", "CU", "CV", "CW", "CX", "CY", "CZ",
    "DE", "DJ", "DK", "DM", "DO", "DZ",
    "EC", "EE", "EG", "EH", "ER", "ES", "ET",
    "FI", "FJ", "FK", "FM", "FO", "FR",
    "GA", "GB", "GD", "GE", "GF", "GG", "GH", "GI", "GL", "GM", "GN", "GP",
    "GQ", "GR", "GS", "GT", "GU", "GW", "GY",
    "HK", "HM", "HN", "HR", "HT", "HU",
    "ID", "IE", "IL", "IM", "IN", "IO", "IQ", "IR", "IS", "IT",
    "JE", "JM", "JO", "JP",
    "KE", "KG", "KH", "KI", "KM", "KN", "KP", "KR", "KW", "KY", "KZ",
    "LA", "LB", "LC", "LI", "LK", "LR", "LS", "LT", "LU", "LV", "LY",
    "MA", "MC", "MD", "ME", "MF", "MG", "MH", "MK", "ML", "MM", "MN", "MO",
    "MP", "MQ", "MR", "MS", "MT", "MU", "MV", "MW", "MX", "MY", "MZ",
    "NA", "NC", "NE", "NF", "NG", "NI", "NL", "NO", "NP", "NR", "NU", "NZ",
    "OM",
    "PA", "PE", "PF", "PG", "PH", "PK", "PL", "PM", "PN", "PR", "PS", "PT",
    "PW", "PY",
    "QA",
    "RE", "RO", "RS", "RU", "RW",
    "SA", "SB", "SC", "SD", "SE", "SG", "SH", "SI", "SJ", "SK", "SL", "SM",
    "SN", "SO", "SR", "SS", "ST", "SV", "SX", "SY", "SZ",
    "TC", "TD", "TF", "TG", "TH", "TJ", "TK", "TL", "TM", "TN", "TO", "TR",
    "TT", "TV", "TW", "TZ",
    "UA", "UG", "UM", "US", "UY", "UZ",
    "VA", "VC", "VE", "VG", "VI", "VN", "VU",
    "WF", "WS",
    "YE", "YT",
    "ZA", "ZM", "ZW",
]

DEFAULT_PROVIDERS = "netflix,amazon,disney,hbo,hulu,apple,paramount,peacock,mgm,discovery"


class Command(BaseCommand):
    help = "Seed Region rows for provider x country combinations (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--providers",
            type=str,
            default=DEFAULT_PROVIDERS,
            help="Comma-separated list of provider names (default: all 10).",
        )
        parser.add_argument(
            "--countries",
            type=str,
            default=",".join(ALL_ISO_COUNTRIES),
            help="Comma-separated list of ISO-3166 alpha-2 country codes "
            "(default: all 249).",
        )

    def handle(self, *args, **options):
        providers = [
            p.strip() for p in options["providers"].split(",") if p.strip()
        ]
        countries = [
            c.strip().upper() for c in options["countries"].split(",") if c.strip()
        ]

        created_count = 0
        existed_count = 0
        for provider in providers:
            for country in countries:
                _, created = Region.objects.get_or_create(
                    provider=provider, country=country
                )
                if created:
                    created_count += 1
                else:
                    existed_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Created {created_count} new Region rows "
                f"({existed_count} already existed)"
            )
        )
