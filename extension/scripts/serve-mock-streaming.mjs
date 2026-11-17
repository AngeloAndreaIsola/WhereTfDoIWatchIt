#!/usr/bin/env node
/**
 * Serve the local mock streaming fixture for extension E2E verification.
 *
 * Usage:
 *   node scripts/serve-mock-streaming.mjs
 *   MOCK_STREAMING_PORT=4177 node scripts/serve-mock-streaming.mjs
 *
 * The fixture is reachable at http://mock-streaming.localhost:4173/ (macOS
 * resolves *.localhost to loopback). Pages: movie.html, episode.html,
 * no-video.html, index.html.
 */
import { createReadStream, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { extname, join, normalize, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = fileURLToPath(new URL('.', import.meta.url));
const root = resolve(scriptDir, '..', 'fixtures', 'mock-streaming');
const port = Number(process.env.MOCK_STREAMING_PORT || 4173);

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.mp4': 'video/mp4',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
};

function send(res, status, body, headers = {}) {
  res.writeHead(status, headers);
  res.end(body);
}

const server = createServer((req, res) => {
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    return send(res, 405, 'Method Not Allowed', { Allow: 'GET, HEAD' });
  }

  let pathname;
  try {
    pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
  } catch {
    return send(res, 400, 'Bad Request');
  }
  if (pathname === '/') pathname = '/index.html';

  const filePath = resolve(join(root, normalize(pathname)));
  if (filePath !== root && !filePath.startsWith(root + sep)) {
    return send(res, 403, 'Forbidden');
  };

  let stats;
  try {
    stats = statSync(filePath);
  } catch {
    return send(res, 404, 'Not Found');
  }
  if (stats.isDirectory()) {
    return send(res, 404, 'Not Found');
  }

  const type = MIME[extname(filePath).toLowerCase()] || 'application/octet-stream';

  // Chrome's media stack issues Range requests and will not build a seekable
  // range from a plain 200 response, so honor them.
  const range = req.headers.range;
  if (range) {
    const match = /^bytes=(\d*)-(\d*)$/.exec(range.trim());
    if (match && (match[1] !== '' || match[2] !== '')) {
      let start = match[1] ? parseInt(match[1], 10) : 0;
      let end = match[2] ? parseInt(match[2], 10) : stats.size - 1;
      if (match[1] === '') {
        start = Math.max(0, stats.size - parseInt(match[2], 10));
        end = stats.size - 1;
      }
      end = Math.min(end, stats.size - 1);
      if (start > end || start >= stats.size) {
        res.writeHead(416, { 'Content-Range': `bytes */${stats.size}` });
        return res.end();
      }
      res.writeHead(206, {
        'Content-Type': type,
        'Content-Length': end - start + 1,
        'Content-Range': `bytes ${start}-${end}/${stats.size}`,
        'Accept-Ranges': 'bytes',
        'Cache-Control': 'no-store',
      });
      if (req.method === 'HEAD') return res.end();
      return createReadStream(filePath, { start, end }).pipe(res);
    }
  }

  res.writeHead(200, {
    'Content-Type': type,
    'Content-Length': stats.size,
    'Accept-Ranges': 'bytes',
    'Cache-Control': 'no-store',
  });
  if (req.method === 'HEAD') return res.end();
  createReadStream(filePath).pipe(res);
});

server.listen(port, () => {
  console.log(`mock-streaming fixture: http://mock-streaming.localhost:${port}/`);
});
