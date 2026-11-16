import { cp, mkdir, rm, copyFile, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = path.join(root, 'dist', 'package');
const localAppOrigin = process.env.WTDIW_EXTENSION_LOCAL_APP_ORIGIN || 'http://localhost:8002';
const localAppUrl = new URL(localAppOrigin);
if (
  localAppUrl.protocol !== 'http:' ||
  !['localhost', '127.0.0.1'].includes(localAppUrl.hostname) ||
  !['3000', '3001', '8000', '8001', '8002', '8003'].includes(localAppUrl.port)
) {
  throw new Error('Local app origin must use an approved localhost port and HTTP scheme.');
}

await rm(output, { recursive: true, force: true });
await mkdir(path.join(output, 'src'), { recursive: true });
await copyFile(path.join(root, 'manifest.json'), path.join(output, 'manifest.json'));
await copyFile(path.join(root, 'popup.html'), path.join(output, 'popup.html'));
await cp(path.join(root, 'dist', 'src'), path.join(output, 'src'), { recursive: true });
const configPath = path.join(output, 'src', 'config.js');
const config = await readFile(configPath, 'utf8');
await writeFile(configPath, config.replace('http://localhost:8002', localAppUrl.origin));
console.log(`Packaged extension at ${output}`);
