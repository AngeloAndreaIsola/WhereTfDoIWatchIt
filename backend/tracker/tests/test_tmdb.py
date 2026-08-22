"""Unit tests for TMDbClient credential handling.

TMDb hands out two credential shapes and they authenticate differently; these
guard that we route each to the right place. No network — we only inspect the
params/headers the client would send.
"""
from __future__ import annotations

from django.test import SimpleTestCase

from tracker.services.tmdb import TMDbClient


class TMDbAuthTests(SimpleTestCase):
    def test_v3_key_uses_query_param(self):
        key = "0123456789abcdef0123456789abcdef"
        params, headers = TMDbClient(api_key=key)._auth()
        self.assertEqual(params, {"api_key": key})
        self.assertEqual(headers, {})

    def test_v4_read_token_uses_bearer_header(self):
        # v4 tokens are JWTs — three base64 segments starting with "eyJ".
        token = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJ4In0.signature"
        params, headers = TMDbClient(api_key=token)._auth()
        self.assertEqual(params, {})
        self.assertEqual(headers, {"Authorization": f"Bearer {token}"})
