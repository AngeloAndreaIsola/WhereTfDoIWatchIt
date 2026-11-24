import hashlib
import json
import re
import uuid
from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tracker.models import (
    ExtensionCapability,
    ExtensionHandshakeChallenge,
    ExtensionPlaybackRecord,
)


EXTENSION_ORIGIN = "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
NONCE = "0123456789abcdef0123456789abcdef"


@override_settings(EXTENSION_ALLOWED_ORIGINS=[EXTENSION_ORIGIN])
class ExtensionApiTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.headers = {
            "HTTP_ORIGIN": EXTENSION_ORIGIN,
            "HTTP_X_WTDIW_EXTENSION": "1",
        }

    def _start_challenge(self):
        response = self.client.post(
            reverse("extension-handshake-init"),
            data=json.dumps({"clientNonce": NONCE}),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Access-Control-Allow-Origin"], EXTENSION_ORIGIN)
        return response.json()

    def _approve(self, challenge):
        path = reverse("extension-authorize", args=[challenge["challengeId"]])
        page = self.client.get(path)
        self.assertEqual(page.status_code, 200)
        match = re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"', page.content)
        self.assertIsNotNone(match)
        csrf_token = match.group(1).decode()
        denied = self.client.post(path, {"decision": "allow"})
        self.assertEqual(denied.status_code, 403, "approval form must reject a missing CSRF token")
        response = self.client.post(
            path,
            {"decision": "allow", "csrfmiddlewaretoken": csrf_token},
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(response.status_code, 200)

    def _exchange(self, challenge):
        return self.client.post(
            reverse("extension-handshake-exchange"),
            data=json.dumps({
                "challengeId": challenge["challengeId"],
                "clientNonce": NONCE,
                "serverNonce": challenge["serverNonce"],
                "requestedScopes": ["title:send"],
            }),
            content_type="application/json",
            **self.headers,
        )

    def _metadata(self):
        return {
            "title": "The Test Film",
            "contentType": "movie",
            "year": 2024,
            "season": None,
            "episode": None,
            "episodeTitle": None,
            "playbackProgress": {"currentTime": 120, "duration": 600, "progressPercent": 20},
            "sourceUrl": "http://mock-streaming.localhost:4173/movie/test-film",
            "siteAdapterId": "mock-streaming-adapter",
            "detectedAt": timezone.now().isoformat(),
        }

    def _ingest(self, token, **extra_metadata):
        return self.client.post(
            reverse("extension-titles-ingest"),
            data=json.dumps({**self._metadata(), **extra_metadata}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
            **self.headers,
        )

    @override_settings(WTDW_LEGACY_OWNER_ID=97)
    def test_csrf_approval_scoped_exchange_and_persisted_ingestion(self):
        challenge = self._start_challenge()
        self._approve(challenge)
        response = self._exchange(challenge)
        self.assertEqual(response.status_code, 200, response.content)
        raw_token = response.json()["capabilityToken"]["token"]
        self.assertEqual(response.json()["capabilityToken"]["scopes"], ["title:send"])
        self.assertEqual(
            ExtensionCapability.objects.get().token_digest,
            hashlib.sha256(raw_token.encode()).hexdigest(),
        )
        ingest = self._ingest(raw_token, user_id=1)
        self.assertEqual(ingest.status_code, 200, ingest.content)
        record = ExtensionPlaybackRecord.objects.get(pk=ingest.json()["id"])
        self.assertEqual(record.user_id, 97)
        self.assertEqual(record.title, "The Test Film")
        self.assertEqual(record.playback_progress["progressPercent"], 20)

    def test_unapproved_challenge_cannot_issue_token_and_denial_is_final(self):
        challenge = self._start_challenge()
        response = self._exchange(challenge)
        self.assertEqual(response.status_code, 409)
        path = reverse("extension-authorize", args=[challenge["challengeId"]])
        page = self.client.get(path)
        csrf = re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"', page.content).group(1).decode()
        denied = self.client.post(
            path,
            {"decision": "deny", "csrfmiddlewaretoken": csrf},
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(denied.status_code, 200)
        self.assertEqual(self._exchange(challenge).status_code, 403)

    def test_token_expiry_and_revocation_reject_ingestion(self):
        capability = ExtensionCapability.objects.create(
            token_digest=hashlib.sha256(b"expired-or-revoked").hexdigest(),
            extension_origin=EXTENSION_ORIGIN,
            scopes=["title:send"],
            expires_at=timezone.now() + timedelta(minutes=1),
        )
        capability.expires_at = timezone.now() - timedelta(seconds=1)
        capability.save(update_fields=["expires_at"])
        self.assertEqual(self._ingest("expired-or-revoked").status_code, 401)
        capability.expires_at = timezone.now() + timedelta(minutes=1)
        capability.revoked_at = timezone.now()
        capability.save(update_fields=["expires_at", "revoked_at"])
        self.assertEqual(self._ingest("expired-or-revoked").status_code, 401)

    def test_unauthorized_origin_and_missing_bearer_are_rejected(self):
        response = self.client.post(
            reverse("extension-handshake-init"),
            data=json.dumps({"clientNonce": NONCE}),
            content_type="application/json",
            HTTP_ORIGIN="chrome-extension://bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            HTTP_X_WTDIW_EXTENSION="1",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post(reverse("extension-titles-ingest"), **self.headers).status_code, 401)

    def test_challenge_is_bound_to_extension_origin_and_nonce_and_expires(self):
        challenge = self._start_challenge()
        row = ExtensionHandshakeChallenge.objects.get(challenge_id=uuid.UUID(challenge["challengeId"]))
        row.approved_at = timezone.now()
        row.save(update_fields=["approved_at"])
        wrong_nonce = self.client.post(
            reverse("extension-handshake-exchange"),
            data=json.dumps({
                "challengeId": challenge["challengeId"],
                "clientNonce": "f" * 32,
                "serverNonce": challenge["serverNonce"],
                "requestedScopes": ["title:send"],
            }),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(wrong_nonce.status_code, 403)
        row.expires_at = timezone.now() - timedelta(seconds=1)
        row.save(update_fields=["expires_at"])
        self.assertEqual(self._exchange(challenge).status_code, 410)
