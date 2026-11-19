# Capability Matrix — Unofficial Providers

| Capability | StreamingCommunity | CineBlog01 | AltaDefinizione |
|---|:---:|:---:|:---:|
| **Movie search** | ✅ | ✅ (browse+filter) | ✅ (browse+filter) |
| **Movie catalogue** | ✅ | ✅ | ✅ |
| **Series search** | ✅ | ✅ (browse+filter) | ✅ (browse+filter) |
| **Season/episode data** | ❌ | ❌ | ❌ |
| **Audio languages** | ✅ | ✅ | ✅ |
| **Subtitle languages** | ✅ | ✅ | ✅ |
| **Per-title refresh** | ✅ | ✅ | ✅ |
| **Incremental updates** | ❌ | ❌ | ❌ |
| **Domain fallback** | ✅ (2 domains) | ❌ | ❌ |

## Legend
- ✅ = Implemented and tested
- ❌ = Not supported by source site or not implemented

## Language Detection Coverage

| Provider | Audio: IT | Audio: EN | Audio: JA | Subs: IT | Subs: EN | Subs: JA |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| StreamingCommunity | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| CineBlog01 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| AltaDefinizione | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

## Domain Status

| Provider | Verified Domains | Fallback | Status |
|---|---|---|---|
| StreamingCommunity | streaming-community.army | streamingcommunityz.nexus (Cloudflare) | Active |
| CineBlog01 | cineblog001.run | cineblog001.tires (redirect) | Active |
| AltaDefinizione | altadefinizionex.ink | altadefinizionex.fit (redirect) | Active |

## Live Validation Results (2026-11-17)

| Provider | Listing pages | Items extracted | Notes |
|---|---|---|---|
| StreamingCommunity | `/`, `/film/`, `/series/`, `/archivio/` | 143 | Legacy `/en/...` paths now 404; titles in `data-title` |
| CineBlog01 | `/film/`, `/serie-tv/` + `/page/N/` | 151 | Titles in `title`/`img[alt]`; 8 pages fetched |
| AltaDefinizione | `/`, `/film/`, `/serie-tv/`, `/archivio/` | 110 | `traduzione` filter ignored by live site |

**Note**: All scrapers send a browser User-Agent; the default `python-requests` agent is WAF-blocked by StreamingCommunity and AltaDefinizione. Titles come from data attributes, not anchor text (the live tiles are image-only).