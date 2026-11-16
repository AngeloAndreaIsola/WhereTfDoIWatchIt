import { cp, mkdir, rm, copyFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = path.join(root, 'dist', 'package');

await rm(output, { recursive: true, force: true });
await mkdir(path.join(output, 'src'), { recursive: true });
await copyFile(path.join(root, 'manifest.json'), path.join(output, 'manifest.json'));
await copyFile(path.join(root, 'popup.html'), path.join(output, 'popup.html'));
await cp(path.join(root, 'dist', 'src'), path.join(output, 'src'), { recursive: true });
console.log(`Packaged extension at ${output}`);
