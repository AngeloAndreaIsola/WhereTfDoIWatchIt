/** @type {import('next').NextConfig} */
const nextConfig = {
  trailingSlash: true,
  // Next's proxy to the backend defaults to 30s (proxy-request.js:
  // `proxyTimeout || 30000`). Bulk imports (Google Interests / Letterboxd) run
  // TMDb lookups per title and can exceed that, which surfaces as
  // "Error: socket hang up / ECONNRESET" in the proxy log.
  experimental: {
    proxyTimeout: 180 * 1000,
  },
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "image.tmdb.org",
        pathname: "/t/p/**",
      },
    ],
  },
  async rewrites() {
    const backend = process.env.BACKEND_URL ?? "http://backend:8000";
    // Django routes all require a trailing slash. Next's `:path*` catch-all drops
    // the incoming trailing slash when it proxies, so `/api/prime/popular/` was
    // being forwarded as `/api/prime/popular` and 404'ing. Re-append it on the
    // destination. Query strings are passed through automatically by Next.
    return [
      {
        source: "/api/:path*",
        destination: `${backend}/api/:path*/`,
      },
    ];
  },
};
export default nextConfig;
