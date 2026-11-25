"""Project a Stage D response into restricted, non-authoritative audit evidence."""

import re

from availability.audit_identity import normalized_title, titles_match
from availability.domain.enums import OfferType
from availability.services.experiment_runner import ExperimentBlocked
from availability.services.normalize import normalize_justwatch_offer_type, normalize_price
from availability.sources.justwatch import OBJECT_TYPE_CONTENT_TYPES
from availability.sources.justwatch_client import _parse_offer, _parse_title
from availability.sources.justwatch_providers import is_addon_package, provider_slug_for_package


def _check_market(country, *records):
    for record in records:
        if any(key in record and record[key] != country for key in ("country", "countryCode", "market")):
            raise ExperimentBlocked("response market differs from NL title scope")


def _token(value):
    if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_+ .-]{1,80}", value):
        return value
    return None


def _tokens(values):
    return [token for value in values if (token := _token(value))]


def _variant(offer):
    price = normalize_price(offer.retail_price_value, offer.currency, offer.retail_price)
    return {
        "presentation": _token(offer.presentation_type),
        "type": _token(offer.offer_type),
        "currency": price.currency,
        "amount": None if price.amount is None else str(price.amount),
        "price_validation_error": price.error,
        "reported_currency": _token(offer.currency),
        "audio_languages": _tokens(offer.audio_languages),
        "subtitle_languages": _tokens(offer.subtitle_languages),
        "video_technology": _tokens(offer.video_technology),
        "audio_technology": _tokens(offer.audio_technology),
    }


def _offers(raw_offers, country):
    variants, unresolved = [], []
    for ordinal, raw in enumerate(raw_offers, start=1):
        if not isinstance(raw, dict):
            unresolved.append({"ordinal": ordinal, "reason": "malformed_offer"})
            continue
        _check_market(country, raw)
        try:
            for field in ("audioLanguages", "subtitleLanguages", "videoTechnology", "audioTechnology"):
                if raw.get(field) is not None and not isinstance(raw[field], list):
                    raise ValueError("offer language or technology is not a list")
            offer = _parse_offer(raw)
            provider = provider_slug_for_package(offer.package)
            offer_class = normalize_justwatch_offer_type(offer.monetization_type)
            if offer_class == OfferType.SUBSCRIPTION and is_addon_package(offer.package):
                offer_class = OfferType.ADDON
        except (AttributeError, TypeError, ValueError):
            unresolved.append({"ordinal": ordinal, "reason": "malformed_offer"})
            continue
        row = {
            "ordinal": ordinal,
            "provider": provider,
            "offer_class": offer_class,
            "package": _token(offer.package.technical_name),
            "source_monetization": _token(offer.monetization_type),
            **_variant(offer),
        }
        variants.append(row)
        if not provider or offer_class == OfferType.UNKNOWN:
            unresolved.append({"ordinal": ordinal, "reason": "unmapped_provider_or_offer_class"})
    presence = sorted({(row["provider"], row["offer_class"]) for row in variants
                       if row["provider"] and row["offer_class"] != OfferType.UNKNOWN})
    return {
        "status": "observed" if presence else "availability_unknown",
        "offer_variants": variants,
        "presence": [{"provider": provider, "offer_class": kind} for provider, kind in presence],
        "unresolved": unresolved,
    }


def _match_title(scope, nodes):
    matches = []
    for node in nodes:
        title = _parse_title({**node, "offers": []})
        media_type = OBJECT_TYPE_CONTENT_TYPES.get(str(title.object_type).strip().upper())
        if media_type == scope["media_type"] and title.tmdb_id_int == scope["tmdb_id"]:
            matches.append((node, title))
    if len(matches) != 1:
        raise ExperimentBlocked("typed title is missing or ambiguous")
    node, title = matches[0]
    if (not isinstance(title.id, str) or not title.id.strip() or title.year != scope["year"]
            or not titles_match(scope, normalized_title(scope["title"]), normalized_title(title.title))):
        raise ExperimentBlocked("typed title has a missing native ID or label/year mismatch")
    return node, title


def observe_title(scope, response):
    """Require the typed title and market before creating any observation."""
    try:
        if "json" not in response.headers.get("Content-Type", "").lower():
            raise ExperimentBlocked("non-JSON response or access challenge")
        body = response.json()
        if not isinstance(body, dict) or body.get("errors"):
            raise ExperimentBlocked("GraphQL rejection or partial error")
        data = body["data"]
        listing = data["popularTitles"]
        _check_market(scope["country"], body, data, listing)
        edges = listing["edges"]
        if not isinstance(edges, list):
            raise ValueError("edges are not a list")
        node, title = _match_title(scope, [edge["node"] for edge in edges])
        _check_market(scope["country"], node, node["content"])
        path = title.full_path
        if path and (not isinstance(path, str) or not path.startswith(f"/{scope['country'].lower()}/")):
            raise ExperimentBlocked("response title path is outside the NL market")
        if not isinstance(node.get("offers"), list):
            raise ValueError("offers are not a list")
        return {
            "scope_id": scope["scope_id"],
            "tmdb_id": scope["tmdb_id"],
            "media_type": scope["media_type"],
            "country": scope["country"],
            "year": scope["year"],
            "provenance": "justwatch_title_search",
            "authoritative": False,
            **_offers(node["offers"], scope["country"]),
        }
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        raise ExperimentBlocked("malformed JustWatch title response") from exc
