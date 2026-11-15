# StreamingCommunity Blocker

Automated GET to https://streamingcommunityz.boo/it/titles/66345-masterplan returns HTTP 403 with Cloudflare challenge ("Attention Required! | Cloudflare", "Sorry, you have been blocked"). No metadata accessible automatically.

Implemented: URL-targeted manual HTML importer (`StreamingCommunityScraper.parse_html`) that detects Cloudflare challenge and parses OG/meta fields from supplied HTML. Non-authoritative, availability=False.

Blocked: automated fetching, search, catalogue sync. Requires manual HTML supply.
