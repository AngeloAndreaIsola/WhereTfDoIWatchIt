import { access, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const packageDir = path.join(root, 'dist', 'package');
const manifest = JSON.parse(await readFile(path.join(packageDir, 'manifest.json'), 'utf8'));

if (manifest.manifest_version !== 3) throw new Error('Expected a Manifest V3 package');
if (manifest.permissions.includes('<all_urls>')) throw new Error('Broad all-sites permission is forbidden');
if (manifest.permissions.includes('cookies')) throw new Error('Cookie permission is forbidden');
for (const relativePath of [manifest.background.service_worker, manifest.action.default_popup, 'src/popup.js']) {
  await access(path.join(packageDir, relativePath));
}
if (!manifest.optional_host_permissions.includes('http://mock-streaming.localhost/*')) {
  throw new Error('Mock adapter host permission is missing');
}
for (const host of manifest.optional_host_permissions) {
  if (/netflix|primevideo|disneyplus/i.test(host)) throw new Error(`Unsupported adapter advertised: ${host}`);
}
console.log('Package validation passed');
