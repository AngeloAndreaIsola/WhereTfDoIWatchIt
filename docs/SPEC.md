# Streaming Tracker — Spec (v1)

## Problem

Streaming availability sites (JustWatch and similar) answer "where can I watch this?" but not "where can I watch it with the audio/subtitle languages I actually want, using a subscription I already have?" Answering that today means manually checking multiple countries/services by hand.

A second, related problem: a Letterboxd watchlist accumulates movies with no visibility into which ones are actually watchable right now, or which single VPN region would unlock the most of them at once.

## Goal (v1)

A personal (single-user, no public accounts) tool that:

1. Lets you record your streaming subscriptions and language preferences (audio + subtitles).
2. Given a movie title, shows a ranked list of country/service combinations, with the best match highlighted, based on your preferences.
3. Imports your Letterboxd watchlist (via CSV export) and reports which VPN region would unlock the most of it, using availability data alone.

Out of scope for v1: notifications, availability history/prediction, IMDb/Trakt sync, browser extension, multi-user accounts, monetization, personal library sources (Plex/Jellyfin/custom), synchronized "Watch Together" playback and social/chat features. See "Future Directions" at the end of this doc.

## Users

Just you (and optionally a couple of friends later, sharing the same instance without separate logins for now). No auth in v1; the schema keeps a `user_id`-shaped seam so real accounts can be added later without a rewrite.

## Core Features

### 1. Preferences & Subscriptions
- Manually configured: which services you subscribe to (Netflix, Disney+, Max, Prime Video, Apple TV+, etc.), which countries you're willing to consider, and your audio/subtitle language preferences.

### 2. Single-Movie Search
- Input: a movie title.
- Matches the title to a TMDb ID (via TMDb search API).
- Fetches availability from the Streaming Availability API (movieofthenight, via RapidAPI) — cached in Postgres, refreshed if stale.
- **Filters to offers included with a subscription you actually have** (offer type `subscription` or `free`, service matched against your configured `Subscription` list) — rental/purchase offers are excluded, since the point is "free with what I already pay for," not "how could I pay to watch this."
- For each region where it's available for free via your subscriptions, shows audio/subtitle language info if you've manually entered it; otherwise prompts you to check and enter it.
- Ranks results by **strict priority**: audio language match first, then subtitle language match, then quality (4K/HDR), and highlights the top match.

### 3. Letterboxd Watchlist Free-to-Watch Check
- You upload your Letterboxd watchlist CSV export (title + year + Letterboxd URI — no external IDs).
- Each entry is matched to a TMDb ID via TMDb's search API (title + year), with a manual confirm step for ambiguous matches.
- Availability is checked (and cached) for every title, **filtered the same way as single-movie search**: subscription/free offers only, matched against your services.
- Report: **per movie**, which region(s) (if any) have it free via a subscription you already have — not an aggregate "switch here to unlock N more" score. A movie with no subscription-included offer anywhere is flagged as unavailable rather than silently omitted.

## Data Model (conceptual)

- `Movie` — tmdb_id, title, year
- `Region` — a provider+country pair (e.g. "Netflix Japan", "Prime Video Italy"). Streaming catalogs are per-region, not per-country, so this is the actual unit availability attaches to.
- `Availability` — movie, region, last_checked_at, is_available, offer_type (subscription/free/rent/buy — only subscription/free are surfaced), quality (4K/HDR)
- `LanguageInfo` — movie, region, audio_languages[], subtitle_languages[] (manually entered, sparse — only populated for titles you've searched)
- `Subscription` — service (your subscribed services)
- `Preference` — audio_languages[] (priority order), subtitle_languages[] (priority order), candidate countries[]
- `WatchlistItem` — movie, source ("letterboxd"), added_at

## Data Sources

- **Streaming Availability API** (movieofthenight / RapidAPI) — country/service availability. Free tier: 100 requests/day. One request per movie returns availability across all countries (no per-country request needed).
- **TMDb API** — movie metadata + title/year search for ID matching. Free.
- **Letterboxd CSV export** — manual upload, no live sync in v1.

## Ranking Logic

Strict priority order (not weighted scoring):
1. Audio language match
2. Subtitle language match
3. Quality (4K/HDR)

Countries/services are sorted by this priority; ties broken by the next criterion down.

## Constraints & Notes

- Streaming Availability API free tier caps at 100 req/day — cached in Postgres with a background refresh job (~90 stale entries/day) rather than fetched live each time.
- Letterboxd exports contain no TMDb/IMDb ID, only title/year/URI — matching is done via TMDb search, not by scraping Letterboxd.
- No ToS-sensitive scraping or automated regional account access anywhere in v1 — all availability data comes from licensed/public APIs.
- If data from the Streaming Availability API is ever surfaced publicly (not applicable for a personal tool), their terms require attribution.

## Future Directions (not scoped, not planned for v1)

Ideas worth remembering if this tool proves useful and grows beyond a personal project:

- **Personal library sources** — connect Plex/Jellyfin or a custom archive as an additional "region," so the ranking/search also knows what you already own.
- **More watchlist sources** — IMDb and Trakt sync alongside Letterboxd.
- **Notifications** — "movie X is now available on your subscriptions," "leaving in 7 days."
- **Availability prediction** — estimate when a title might arrive/leave a region based on history.
- **Multi-user accounts** — real auth, so this could be shared with friends with separate profiles.
- **Watch Together / social layer** — a much bigger, separate product direction: synchronized playback across services (via a browser extension acting as a per-provider adapter), optional public "movie rooms" with viewer counts, and a live chat pinned to playback timestamps (so comments are tied to a moment in the movie, with spoiler protection hiding comments beyond the viewer's current position). Genuinely interesting, but effectively a different, much larger product — worth revisiting only after v1 is validated.
