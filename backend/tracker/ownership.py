"""Server-owned selection of the installation's personal data."""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def resolve_personal_owner_id() -> int:
    """Resolve the owner for the current no-login installation.

    A future authenticated rollout must replace this policy at personal entry
    points with an authorized account-to-profile mapping. Missing credentials
    or an unapproved mapping must deny access before any personal query.
    """
    raw_owner = getattr(settings, "WTDW_LEGACY_OWNER_ID", None)
    if raw_owner is None:
        return 1
    if isinstance(raw_owner, str) and raw_owner.isascii() and raw_owner.isdecimal():
        digits = raw_owner.lstrip("0") or "0"
        if len(digits) <= 10:
            raw_owner = int(digits)
    if type(raw_owner) is not int or not 1 <= raw_owner <= 2_147_483_647:
        raise ImproperlyConfigured("WTDW_LEGACY_OWNER_ID must be a positive PostgreSQL integer")
    return raw_owner
