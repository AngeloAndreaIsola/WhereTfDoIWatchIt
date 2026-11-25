"""Public fields shared by the availability response and audit export."""

from .normalize import normalize_currency, normalize_justwatch_quality, normalize_price_amount


def public_offer_variant(variant: dict, *, provider: str | None, country: str, offer_class: str) -> dict:
    amount, _ = normalize_price_amount(variant.get("amount"))
    currency, _ = normalize_currency(variant.get("currency"))
    return {
        "provider": provider,
        "country": country,
        "offer_class": offer_class,
        "quality": normalize_justwatch_quality(variant.get("presentation_type")),
        "amount": None if amount is None else str(amount),
        "currency": currency,
    }
