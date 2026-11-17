"""Conservative URL identity for raw catalogue observations."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


# Add entries only after a provider-side redirect or an explicit provider
# domain statement establishes that both hosts are aliases of the same source.
VERIFIED_HOST_ALIASES: dict[str, dict[str, str]] = {}

_TRACKING_KEYS = {"fbclid", "gclid", "dclid", "mc_cid", "mc_eid"}


def normalize_catalogue_url(provider_slug: str, source_url: str) -> str:
    """Normalize known tracking noise while preserving meaningful URL parts.

    Scheme, path, trailing slash, and all non-tracking query parameters remain
    identity-bearing. Hosts are folded only through the explicit, provider-
    scoped alias map above. Fragments and default ports are not resource IDs.
    """
    parts = urlsplit((source_url or "").strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return source_url or ""

    host = parts.hostname.rstrip(".").lower()
    host = VERIFIED_HOST_ALIASES.get(provider_slug, {}).get(host, host)
    port = parts.port
    if port and not ((parts.scheme.lower() == "https" and port == 443) or (parts.scheme.lower() == "http" and port == 80)):
        host = f"{host}:{port}"
    if parts.username or parts.password:
        # Provider catalogue URLs should not contain credentials; retain the
        # authority verbatim rather than accidentally making two URLs equal.
        host = parts.netloc.lower()

    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in _TRACKING_KEYS or lowered.startswith("utm_"):
            continue
        query.append((key, value))
    return urlunsplit((parts.scheme.lower(), host, parts.path or "/", urlencode(query, doseq=True), ""))
