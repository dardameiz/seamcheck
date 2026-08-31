// Reads newline-separated file paths on stdin, writes one NDJSON record per file:
// {"path": "..."} plus either "ast" or "error".
//
// One process for the whole run: spawning node per file costs ~60ms each, which is
// minutes across a real front-end tree.
//
// .mjs, not .js: a host project with "type": "module" in package.json makes .js an ES
// module (no `require`), and one without makes it CommonJS (no top-level `import`).
// The explicit extension is unambiguous either way.
//
// @babel/parser in ESTree mode, not acorn. Acorn cannot read TypeScript, JSX or
// decorators, and on a NestJS project that is not an edge case - `@Controller()` on line
// three means the whole file is lost. The alternative considered was acorn plus a
// transpiler, and this is better for one decisive reason: **nothing is transformed**, so
// every node's line number is the line in the file a reader will open, by construction
// rather than by a transpiler's promise. It also keeps type-only and unused imports,
// which a TypeScript transform elides - and an import graph is exactly what this tool
// walks.
//
// The `estree` plugin matters as much as the syntax ones: it makes Babel emit the same
// node shapes acorn did (`Literal`, `Property`), so every extractor downstream is
// untouched.
import { readFileSync } from 'node:fs';
import { parse } from '@babel/parser';

// TypeScript and JSX cannot both be enabled for a .ts file: `<T>(x)` is a type assertion
// there and an element in .tsx, and Babel refuses to guess. So the plugin set follows the
// extension, which is the same rule tsc itself uses.
// `decorators-legacy` rather than the stage-3 `decorators`, because NestJS and Angular
// decorate PARAMETERS - `create(@Body() dto: Dto)` - and the modern proposal forbids that
// outright. Legacy is what `experimentalDecorators` in tsconfig means, and it is what the
// frameworks that actually use decorators compile with. A file using the newest syntax
// instead is retried below with the modern plugin.
const LEGACY = 'decorators-legacy';
const TS = ['typescript', LEGACY];
const JS = ['jsx', LEGACY];
const PLUGINS = {
  '.ts': TS, '.mts': TS, '.cts': TS,
  '.tsx': ['typescript', 'jsx', LEGACY],
  '.js': JS, '.mjs': JS, '.cjs': JS, '.jsx': JS,
};

function pluginsFor(filePath, modern) {
  const dot = filePath.lastIndexOf('.');
  const base = PLUGINS[dot === -1 ? '' : filePath.slice(dot).toLowerCase()] || JS;
  // An ambient declaration file has no initialisers and no bodies; without dts mode Babel
  // rejects `declare const x: T;` outright.
  const typescript = filePath.toLowerCase().endsWith('.d.ts')
    ? ['typescript', { dts: true }]
    : 'typescript';
  return base.map((plugin) => {
    if (plugin === 'typescript') return typescript;
    if (plugin === LEGACY && modern) return ['decorators', { decoratorsBeforeExport: true }];
    return plugin;
  });
}

function options(filePath, sourceType, modern = false) {
  return {
    sourceType,
    plugins: ['estree', ...pluginsFor(filePath, modern)],
    // Real files do things a spec-strict parser rejects: a `return` at module scope in a
    // CommonJS guard, a top-level `await` in an ESM entry, an export of a name declared
    // by an ambient type. None of them stop a reader from seeing the fetch calls.
    allowReturnOutsideFunction: true,
    allowAwaitOutsideFunction: true,
    allowUndeclaredExports: true,
    allowSuperOutsideMethod: true,
    ranges: false,
    tokens: false,
  };
}

// Babel puts a BigInt literal's value in the AST as an actual BigInt, which
// JSON.stringify refuses outright. Acorn stored a string, so nothing downstream ever had
// to care - and one `123n` anywhere in a 6,378-file repository threw during serialisation
// and killed the process, losing every OTHER file's AST with it. A replacer fixes the
// value; the try/catch below fixes the blast radius, which is the part that matters.
function safe(key, value) {
  return typeof value === 'bigint' ? value.toString() : value;
}

let buffer = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
  buffer += chunk;
});
process.stdin.on('end', () => {
  for (const filePath of buffer.split('\n').filter(Boolean)) {
    let record;
    try {
      const source = readFileSync(filePath, 'utf8');
      let ast = null;
      // Three attempts, cheapest first, because the alternative to each is losing the
      // whole file: legacy decorators as a module, then the stage-3 decorator syntax,
      // then as a script for a file using `with` or an HTML-comment directive.
      let firstError;
      for (const [sourceType, modern] of [['module', false], ['module', true], ['script', false]]) {
        try {
          ast = parse(source, options(filePath, sourceType, modern));
          break;
        } catch (err) {
          firstError = firstError || err;
        }
      }
      if (!ast) throw firstError;
      record = { path: filePath, ast: ast.program };
    } catch (err) {
      record = { path: filePath, error: err.message };
    }
    try {
      process.stdout.write(JSON.stringify(record, safe) + '\n');
    } catch (err) {
      // One unserialisable file must never cost the run. It is reported as a failure like
      // any other, and the reporter on the Python side names it.
      process.stdout.write(
        JSON.stringify({ path: filePath, error: `could not serialise AST: ${err.message}` }) + '\n'
      );
    }
  }
});
