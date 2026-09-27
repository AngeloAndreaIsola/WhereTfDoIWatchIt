"""Prime Video gap-fill source backed by TMDb watch/providers.

The primary source (Streaming Availability) does not carry every Prime Video
licence. The Office (2005) is on primevideo.com in NL, but upstream has no
``prime`` subscription offer for it in any of its 65 covered countries — only
a CA ``addon`` and AT ``buy`` rows — so the detail page correctly reports
nothing and the user is left asking why.

TMDb is already integrated (same ``TMDB_API_KEY``, same ``TMDbClient``) and
publishes per-region monetization types: ``flatrate`` = included with a
subscription, ``free`` = ad supported. Those are exactly the two offer types
this app accepts, so they become ordinary ``prime`` offers that then flow
through the normal subscription filter and ranking — nothing about ranking,
presentation or persistence changes.

Design constraints:
- Read-time only: nothing is written to ``Availability``, so the primary
  source's freshness (``last_checked_at``) semantics are untouched.
- Gap-fill only: a country that already has a ``prime`` row from the primary
  source is skipped, so upstream stays authoritative where it has data.
- Never fatal: any failure (network, payload, unconfigured key) degrades to
  "no extra offers" instead of breaking the detail page.
"""
from __future__ import annotations

import logging

from .offers import Offer
from .tmdb import TMDbClient

logger = logging.getLogger(__name__)

# TMDb ids for the direct Prime Video storefront. 9 is the legacy id, 119 the
# current one, 2100 the ad-supported "Amazon Prime Video with Ads" tier. Prime
# Video *Channels* (subscribed through Amazon but billed by a third party) are
# separate providers and are deliberately excluded — they are not "Prime".
PRIME_PROVIDER_IDS = {9, 119, 2100}

# TMDb monetization bucket -> this app's offer type. Both are accepted by
# ``_qualify_offers``; ``buy``/``rent`` are absent on purpose.
OFFER_TYPES = (("flatrate", "subscription"), ("free", "free"))


def _is_prime_provider(provider: dict) -> bool:
    """True for the Prime Video storefront, False for channels/marketplaces."""
    if provider.get("provider_id") in PRIME_PROVIDER_IDS:
        return True
    name = (provider.get("provider_name") or "").lower()
    return "amazon prime" in name and "channel" not in name


def prime_offers(movie, existing: list[Offer] | None = None) -> list[Offer]:
    """Prime Video offers for *movie* from TMDb's per-region data.

    ``existing`` is the primary source's offer list; countries that already
    carry a ``prime`` row there are skipped. Returns ``[]`` when TMDb is
    unconfigured, the fetch fails, or nothing extra is found.
    """
    if not TMDbClient.is_configured():
        return []

    try:
        regions = TMDbClient().watch_providers(
            movie.tmdb_id, content_type=movie.content_type
        )
    except Exception as exc:
        logger.warning("TMDb watch/providers gap-fill failed for %s: %s", movie.pk, exc)
        return []

    if not isinstance(regions, dict):
        return []

    covered = {
        (o.country or "").upper()
        for o in (existing or [])
        if (o.service or "").lower() == "prime" and o.country
    }

    offers: list[Offer] = []
    for country, entry in regions.items():
        cc = (country or "").upper().strip()
        if len(cc) != 2 or not isinstance(entry, dict):
            continue
        if cc in covered:
            continue

        for bucket, offer_type in OFFER_TYPES:
            providers = entry.get(bucket) or []
            if not any(_is_prime_provider(p) for p in providers if isinstance(p, dict)):
                continue
            # One offer per country: a subscription row wins over a free one.
            offers.append(
                Offer(
                    country=cc,
                    service="prime",
                    offer_type=offer_type,
                    url=entry.get("link") or "",
                )
            )
            break

    return offers
