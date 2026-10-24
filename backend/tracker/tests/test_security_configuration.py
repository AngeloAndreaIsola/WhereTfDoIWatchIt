"""P3 deployment-boundary regression tests.

v1 intentionally has no identity model. These tests make the resulting
boundary explicit: local Compose is loopback-only, while a public deployment
must add an authentication design rather than assuming DRF/CSRF protects an
anonymous single-user API.
"""
from django.test import SimpleTestCase

from config.settings import validate_security_settings


class ProductionSettingsValidationTests(SimpleTestCase):
    def test_placeholder_keys_are_rejected_when_debug_is_off(self):
        for key in ("", "dev-insecure-secret", "change-me-in-production"):
            with self.assertRaises(RuntimeError):
                validate_security_settings(secret_key=key, debug=False)

    def test_explicit_debug_allows_the_local_development_placeholder(self):
        validate_security_settings(secret_key="dev-insecure-secret", debug=True)

    def test_non_placeholder_production_key_is_accepted(self):
        validate_security_settings(secret_key="test-only-strong-secret", debug=False)
