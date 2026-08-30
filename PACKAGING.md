# Extracting Signal Map into its own repository

Signal Map is developed as a self-contained Django app inside a host project, so it is
always exercised against a real codebase. Publishing it is a move, not a rewrite:

```bash
mkdir signal-map
git -C signal-map init
mv signal_map signal-map/
cd signal-map
mv signal_map/{pyproject.toml,LICENSE,README.md,CONTRIBUTING.md,CODE_OF_CONDUCT.md,SECURITY.md,PACKAGING.md} .
mv signal_map/.github .
python -m build            # pyproject.toml's paths are correct at this point
```

The resulting layout is what `pyproject.toml` already describes:

```
signal-map/
  pyproject.toml      packages = ["signal_map"]
  LICENSE  README.md  CONTRIBUTING.md  CODE_OF_CONDUCT.md  SECURITY.md
  .github/            issue + PR templates, dependabot, workflows
  signal_map/         the package
```

Nothing in `signal_map/` imports from the host project, and every project-specific path
comes from `SIGNAL_MAP_CONFIG` in the host's settings — so no source change is needed.

Before publishing, rebuild the parser bundles and commit them:

```bash
signal_map/build_parsers.sh
```

They carry `acorn` and `postcss` inlined. A `pip install` has no `node_modules`, so an
unbundled parser exits `ERR_MODULE_NOT_FOUND` and the scan loses every JavaScript and CSS
symbol - about half the graph. Node itself still has to be on PATH; without it the scan
says so and returns the Django half rather than failing.

Two things do need doing at publish time:

1. Copy `.github/workflows/signal-map.yml` from the host repo (it lives at the host root
   so it can actually run there) into the new repo's `.github/workflows/`, and drop the
   `paths:` filter, which exists only to keep it off unrelated host-repo pushes.
2. Enable GitHub's free scanning: Dependabot (config already present), CodeQL, secret
   scanning and push protection. All are instant and meaningless before the repo is public.
