// Checks the app without an Atlassian account: the manifest against Forge's own schema,
// and that the resolver and both UI Kit screens bundle (imports exist, JSX parses).
// `forge lint` and `forge deploy` check more, but need `forge login`.
import { validate } from '@forge/manifest';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
process.chdir(root);

const result = await validate(false, path.join(root, 'manifest.yml'));
if (!result.success) {
  for (const e of result.errors) console.error(`manifest.yml:${e.line}:${e.column} ${e.message}`);
  process.exit(1);
}
console.log('manifest.yml: valid');

for (const entry of ['src/index.js', 'src/frontend/panel.jsx', 'src/frontend/admin.jsx']) {
  await build({ entryPoints: [entry], bundle: true, write: false, platform: 'node', jsx: 'automatic', logLevel: 'error' });
  console.log(`${entry}: bundles`);
}
