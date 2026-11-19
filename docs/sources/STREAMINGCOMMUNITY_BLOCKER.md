# Historical StreamingCommunity blocker

The original `.boo` URL test returned HTTP 403 with a Cloudflare challenge and
could not provide metadata. That result applied to that domain and path only;
it is not the current scraper status. The integrated scraper tries the bounded
domains and listing paths documented in
[`INTEGRATION_COMPATIBILITY.md`](INTEGRATION_COMPATIBILITY.md).

Cloudflare challenges remain a current operational blocker: the scraper logs
them and tries its configured fallback domain. It does not treat a listing card
as proof of playable access or resolution.
