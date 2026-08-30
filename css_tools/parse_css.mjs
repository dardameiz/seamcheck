// Mirrors js_tools/parse_js.mjs: newline-separated paths on stdin, one NDJSON record
// per file on stdout. .mjs for the same reason - the host project's package.json
// decides what a bare .js means, and this must not depend on that.
import { readFileSync } from 'node:fs';
import postcss from 'postcss';

// The comma decides whether this is a question or a statement: `var(--x)` asks for a
// definition, `var(--x, .08em)` supplies its own answer and needs none.
const VAR_USE = /var\(\s*(--[\w-]+)\s*(,)?/g;

let buffer = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
  buffer += chunk;
});
process.stdin.on('end', () => {
  for (const filePath of buffer.split('\n').filter(Boolean)) {
    const record = { path: filePath, selectors: [], tokenDefs: [], tokenUses: [], imports: [] };
    try {
      const root = postcss.parse(readFileSync(filePath, 'utf8'), { from: filePath });
      root.walkRules((rule) => {
        record.selectors.push({ selector: rule.selector, line: rule.source?.start?.line ?? null });
      });
      root.walkDecls((decl) => {
        const line = decl.source?.start?.line ?? null;
        if (decl.prop.startsWith('--')) {
          record.tokenDefs.push({ name: decl.prop, line });
        }
        for (const match of decl.value.matchAll(VAR_USE)) {
          record.tokenUses.push({ name: match[1], line, fallback: Boolean(match[2]) });
        }
      });
      root.walkAtRules('import', (rule) => {
        record.imports.push({ params: rule.params, line: rule.source?.start?.line ?? null });
      });
    } catch (err) {
      record.error = err.message;
    }
    process.stdout.write(JSON.stringify(record) + '\n');
  }
});
