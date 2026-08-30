#!/usr/bin/env bash
# Rebuild the Node parsers with their dependencies inlined.
#
# The bundles are committed, because a `pip install` has no npm and no node_modules to
# resolve `acorn` or `postcss` against. Re-run this after touching either .mjs source, or
# to pick up a dependency update, and commit the result.
#
#   seamcheck/build_parsers.sh
#
# createRequire is needed because postcss is CommonJS and calls require('path') at
# runtime; without it the ESM bundle dies with "Dynamic require of path is not supported".
set -euo pipefail
cd "$(dirname "$0")"

# acorn and postcss have to be resolvable for esbuild to inline them. The published wheel
# needs none of this - it carries the finished bundles - so they are devDependencies of
# this repo, not runtime dependencies of the package.
[ -d node_modules ] || npm ci --silent || npm install --silent
BANNER="import { createRequire as __cr } from 'node:module'; const require = __cr(import.meta.url);"

for pair in "js_tools/parse_js" "css_tools/parse_css"; do
  ./node_modules/.bin/esbuild "seamcheck/${pair}.mjs" \
    --bundle --platform=node --format=esm \
    --banner:js="$BANNER" \
    --outfile="seamcheck/${pair}.bundle.mjs"
done
