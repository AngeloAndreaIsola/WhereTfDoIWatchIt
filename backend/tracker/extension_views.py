"""Explicit, scoped authorization and ingestion for the local browser extension."""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt, csrf_protect, ensure_csrf_cookie

from .models import ExtensionCapability, ExtensionHandshakeChallenge, ExtensionPlaybackRecord

_EXTENSION_HEADER = "WhereTfDoIWatchIt-Extension"
_ALLOWED_SCOPES = {"title:send"}
_MAX_TOKEN_AGE = timedelta(hours=1)
_CHALLENGE_AGE = timedelta(minutes=5)


def _allowed_origins() -> set[str]:
    configured = getattr(settings, "EXTENSION_ALLOWED_ORIGINS", [])
    if isinstance(configured, str):
        configured = configured.split(",")
    return {value.strip() for value in configured if value.strip()}


def _origin(request: HttpRequest) -> str:
    return request.headers.get("Origin", "")


def _cors(response: HttpResponse, origin: str) -> HttpResponse:
    if origin in _allowed_origins():
        response["Access-Control-Allow-Origin"] = origin
        response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-WTDIW-Extension"
        response["Access-Control-Max-Age"] = "300"
        response["Vary"] = "Origin"
    return response


def _preflight(request: HttpRequest) -> HttpResponse | None:
    if request.method != "OPTIONS":
        return None
    origin = _origin(request)
    return _cors(HttpResponse(status=204), origin) if origin in _allowed_origins() else HttpResponse(status=403)


def _json_body(request: HttpRequest) -> dict:
    if request.content_type != "application/json" or len(request.body) > 16_384:
        raise ValueError("Expected a small application/json request body.")
    value = json.loads(request.body)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    return value


def _extension_error(message: str, status: int, origin: str) -> JsonResponse:
    return _cors(JsonResponse({"error": message}, status=status), origin)


def _valid_extension_request(request: HttpRequest) -> tuple[str, HttpResponse | None]:
    origin = _origin(request)
    if origin not in _allowed_origins() or request.headers.get("X-WTDIW-Extension") != "1":
        return origin, _extension_error("Unauthorized extension origin.", 403, origin)
    return origin, None


@csrf_exempt
def extension_handshake_init(request: HttpRequest) -> HttpResponse:
    preflight = _preflight(request)
    if preflight:
        return preflight
    origin, error = _valid_extension_request(request)
    if error:
        return error
    if request.method != "POST":
        return _extension_error("Method not allowed.", 405, origin)
    try:
        payload = _json_body(request)
    except (ValueError, json.JSONDecodeError):
        return _extension_error("Invalid request body.", 400, origin)
    client_nonce = payload.get("clientNonce")
    if not isinstance(client_nonce, str) or len(client_nonce) != 32 or any(c not in "0123456789abcdef" for c in client_nonce):
        return _extension_error("Invalid client nonce.", 400, origin)

    challenge_id = uuid.uuid4()
    server_nonce = secrets.token_hex(16)
    challenge = ExtensionHandshakeChallenge.objects.create(
        challenge_id=challenge_id,
        extension_origin=origin,
        client_nonce=client_nonce,
        server_nonce=server_nonce,
        expires_at=timezone.now() + _CHALLENGE_AGE,
    )
    del challenge
    authorization_url = request.build_absolute_uri(reverse("extension-authorize", args=[challenge_id]))
    return _cors(JsonResponse({
        "challengeId": str(challenge_id),
        "serverNonce": server_nonce,
        "authorizationUrl": authorization_url,
    }), origin)


@ensure_csrf_cookie
@csrf_protect
def extension_authorize(request: HttpRequest, challenge_id: uuid.UUID) -> HttpResponse:
    challenge = get_object_or_404(ExtensionHandshakeChallenge, challenge_id=challenge_id)
    if challenge.expires_at <= timezone.now() or challenge.consumed_at:
        denied = challenge.denied_at is not None
        return render(request, "tracker/extension_authorize.html", {"expired": not denied, "approved": False, "denied": denied}, status=410)
    if request.method == "POST":
        if request.POST.get("decision") != "allow":
            challenge.denied_at = timezone.now()
            challenge.consumed_at = challenge.denied_at
            challenge.save(update_fields=["denied_at", "consumed_at"])
            return render(request, "tracker/extension_authorize.html", {"expired": False, "approved": False, "denied": True})
        challenge.approved_at = timezone.now()
        challenge.save(update_fields=["approved_at"])
        return render(request, "tracker/extension_authorize.html", {"expired": False, "approved": True})
    return render(request, "tracker/extension_authorize.html", {"expired": False, "approved": False})


@csrf_exempt
def extension_handshake_exchange(request: HttpRequest) -> HttpResponse:
    preflight = _preflight(request)
    if preflight:
        return preflight
    origin, error = _valid_extension_request(request)
    if error:
        return error
    if request.method != "POST":
        return _extension_error("Method not allowed.", 405, origin)
    try:
        payload = _json_body(request)
        challenge_id = uuid.UUID(str(payload.get("challengeId")))
    except (ValueError, TypeError, json.JSONDecodeError):
        return _extension_error("Invalid request body.", 400, origin)
    scopes = payload.get("requestedScopes")
    if not isinstance(scopes, list) or not scopes or any(scope not in _ALLOWED_SCOPES for scope in scopes):
        return _extension_error("Requested capability scope is not allowed.", 400, origin)
    with transaction.atomic():
        try:
            challenge = ExtensionHandshakeChallenge.objects.select_for_update().get(challenge_id=challenge_id)
        except ExtensionHandshakeChallenge.DoesNotExist:
            return _extension_error("Authorization challenge not found.", 404, origin)
        now = timezone.now()
        if challenge.extension_origin != origin:
            return _extension_error("Authorization challenge belongs to another extension.", 403, origin)
        if challenge.expires_at <= now:
            return _extension_error("Authorization challenge expired.", 410, origin)
        if challenge.consumed_at:
            message = "Authorization challenge was denied." if challenge.denied_at else "Authorization challenge already used."
            return _extension_error(message, 403 if challenge.denied_at else 409, origin)
        if not challenge.approved_at:
            return _extension_error("Approve access in the local app before finishing the connection.", 409, origin)
        if payload.get("clientNonce") != challenge.client_nonce or payload.get("serverNonce") != challenge.server_nonce:
            return _extension_error("Authorization challenge did not match.", 403, origin)

        raw_token = secrets.token_urlsafe(32)
        expires_at = now + _MAX_TOKEN_AGE
        ExtensionCapability.objects.create(
            token_digest=hashlib.sha256(raw_token.encode()).hexdigest(),
            extension_origin=origin,
            scopes=scopes,
            expires_at=expires_at,
        )
        challenge.consumed_at = now
        challenge.save(update_fields=["consumed_at"])
    return _cors(JsonResponse({
        "state": "granted",
        "capabilityToken": {
            "token": raw_token,
            "issuedAt": int(now.timestamp() * 1000),
            "expiresAt": int(expires_at.timestamp() * 1000),
            "scopes": scopes,
            "origin": origin,
        },
    }), origin)


def _authorized_capability(request: HttpRequest, *, require_scope: str) -> tuple[ExtensionCapability | None, HttpResponse | None, str]:
    origin, error = _valid_extension_request(request)
    if error:
        return None, error, origin
    authorization = request.headers.get("Authorization", "")
    scheme, _, raw_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not raw_token:
        return None, _extension_error("Bearer token required.", 401, origin), origin
    digest = hashlib.sha256(raw_token.encode()).hexdigest()
    try:
        capability = ExtensionCapability.objects.get(token_digest=digest)
    except ExtensionCapability.DoesNotExist:
        return None, _extension_error("Invalid capability token.", 401, origin), origin
    now = timezone.now()
    if capability.extension_origin != origin:
        return None, _extension_error("Capability token origin mismatch.", 403, origin), origin
    if capability.revoked_at:
        return None, _extension_error("Capability token has been revoked.", 401, origin), origin
    if capability.expires_at <= now:
        return None, _extension_error("Capability token has expired.", 401, origin), origin
    if require_scope not in capability.scopes:
        return None, _extension_error("Capability token lacks the required scope.", 403, origin), origin
    return capability, None, origin


@csrf_exempt
def extension_revoke(request: HttpRequest) -> HttpResponse:
    preflight = _preflight(request)
    if preflight:
        return preflight
    capability, error, origin = _authorized_capability(request, require_scope="title:send")
    if error:
        return error
    if request.method != "POST":
        return _extension_error("Method not allowed.", 405, origin)
    capability.revoked_at = timezone.now()
    capability.save(update_fields=["revoked_at"])
    return _cors(JsonResponse({"success": True, "message": "Extension access revoked."}), origin)


@csrf_exempt
def extension_titles_ingest(request: HttpRequest) -> HttpResponse:
    preflight = _preflight(request)
    if preflight:
        return preflight
    capability, error, origin = _authorized_capability(request, require_scope="title:send")
    if error:
        return error
    if request.method != "POST":
        return _extension_error("Method not allowed.", 405, origin)
    try:
        payload = _json_body(request)
    except (ValueError, json.JSONDecodeError):
        return _extension_error("Invalid request body.", 400, origin)
    title = payload.get("title")
    source_url = payload.get("sourceUrl")
    if not isinstance(title, str) or not title.strip() or len(title) > 255:
        return _extension_error("A title of at most 255 characters is required.", 400, origin)
    if payload.get("siteAdapterId") != "mock-streaming-adapter" or not isinstance(source_url, str):
        return _extension_error("Unsupported site adapter.", 400, origin)
    from urllib.parse import urlparse
    parsed_source = urlparse(source_url)
    if parsed_source.scheme != "http" or parsed_source.hostname != "mock-streaming.localhost":
        return _extension_error("Unsupported metadata source URL.", 400, origin)
    content_type = payload.get("contentType")
    if content_type not in {"movie", "tv", "unknown"}:
        return _extension_error("Invalid content type.", 400, origin)
    progress = payload.get("playbackProgress")
    if progress is not None:
        if not isinstance(progress, dict):
            return _extension_error("Invalid playback progress.", 400, origin)
        try:
            current, duration, percent = (progress[name] for name in ("currentTime", "duration", "progressPercent"))
            if not (0 <= current <= duration and duration > 0 and 0 <= percent <= 100):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            return _extension_error("Invalid playback progress.", 400, origin)
    detected_at = parse_datetime(payload.get("detectedAt", ""))
    if detected_at is None:
        return _extension_error("Invalid detection timestamp.", 400, origin)

    record = ExtensionPlaybackRecord.objects.create(
        title=title.strip(),
        content_type=content_type,
        year=payload.get("year") if isinstance(payload.get("year"), int) else None,
        season=payload.get("season") if isinstance(payload.get("season"), int) else None,
        episode=payload.get("episode") if isinstance(payload.get("episode"), int) else None,
        episode_title=payload.get("episodeTitle") if isinstance(payload.get("episodeTitle"), str) else None,
        playback_progress=progress,
        source_url=source_url,
        site_adapter_id=payload["siteAdapterId"],
        detected_at=detected_at,
    )
    return _cors(JsonResponse({"success": True, "message": "Playback metadata saved.", "id": record.pk}), origin)
