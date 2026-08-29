// Reads newline-separated file paths on stdin, writes one NDJSON record per file:
// {"path": "..."} plus either "ast" or "error".
//
// One process for the whole run: spawning node per file costs ~60ms each, which is
// minutes across a real front-end tree.
//
// .mjs, not .js: a host project with "type": "module" in package.json makes .js an ES
// module (no `require`), and one without makes it CommonJS (no top-level `import`).
// The explicit extension is unambiguous either way.
import { readFileSync } from 'node:fs';
import * as acorn from 'acorn';

let buffer = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
  buffer += chunk;
});
process.stdin.on('end', () => {
  for (const filePath of buffer.split('\n').filter(Boolean)) {
    let record;
    try {
      const ast = acorn.parse(readFileSync(filePath, 'utf8'), {
        ecmaVersion: 'latest',
        sourceType: 'module',
        locations: true,
        allowHashBang: true,
      });
      record = { path: filePath, ast };
    } catch (err) {
      record = { path: filePath, error: err.message };
    }
    process.stdout.write(JSON.stringify(record) + '\n');
  }
});
