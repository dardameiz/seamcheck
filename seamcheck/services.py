"""Which service a file belongs to, in a repository that holds more than one.

Seamcheck already detects several backends in one repository - cal.com serves a Next.js
front end and a NestJS API, immich pairs a NestJS server with a FastAPI machine-learning
service - and then merges everything into ONE graph. So a monorepo reads as a single
confused application: a route in the API called only by the web app has no caller inside
its own service and lands in `uncertain`, which is true of the service and false of the
repository.

Naming the services fixes the classification and produces the thing nobody has: an
architecture diagram drawn from source rather than from memory. "Called by web, across the
network" is a different and far more useful answer than "no caller found".

Nothing here is inferred from code style or guessed from a directory name. A service is
declared - by a workspace manifest, a Python project file, a Dockerfile or a compose
service - and where nothing is declared, the answer is one service, which is the truth for
most repositories.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
from dataclasses import dataclass, field

from seamcheck.adapters.discovery import SKIP_DIRS

# A workspace manifest names where the packages are. Globs, because that is how every one
# of these formats spells it: `apps/*`, `packages/**`.
_WORKSPACE_FILES = ("pnpm-workspace.yaml", "package.json", "lerna.json")
_PNPM_PACKAGES_RE = re.compile(r"^\s*-\s*['\"]?([^'\"\n#]+?)['\"]?\s*$", re.M)
# Compose services, read only for the names and the build contexts they point at.
_COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
_COMPOSE_DIRS = (".", "docker", "deploy", "infra", ".devcontainer")

# How far to look for manifests. A monorepo puts its packages two or three levels down;
# walking the whole tree finds every node_modules manifest instead, which is not a service.
_MAX_DEPTH = 4


@dataclass
class Service:
    """One deployable unit, and the evidence that it is one."""

    name: str
    # Repo-relative, always with forward slashes, never with a trailing separator.
    root: str
    language: str = ""
    # What said so: "package.json", "pyproject.toml", "Dockerfile", "compose".
    evidence: list[str] = field(default_factory=list)
    # A workspace package is not a service. cal.com declares 113 packages and deploys a
    # handful of them; the rest are libraries - a tailwind config, an icon set, a shared
    # types package. Calling those services would turn the architecture diagram into a
    # dependency graph, which is a different picture and one people already have.
    deployable: bool = False

    def contains(self, repo_relative_file: str) -> bool:
        if not self.root:
            return True
        return repo_relative_file == self.root or repo_relative_file.startswith(self.root + "/")


def _read_json(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


def _globs_from_workspaces(root: pathlib.Path) -> list[str]:
    """The package globs a workspace manifest declares, in whichever dialect it uses."""
    globs: list[str] = []
    package_json = _read_json(root / "package.json")
    workspaces = package_json.get("workspaces")
    if isinstance(workspaces, list):
        globs += [g for g in workspaces if isinstance(g, str)]
    elif isinstance(workspaces, dict):
        globs += [g for g in (workspaces.get("packages") or []) if isinstance(g, str)]

    pnpm = root / "pnpm-workspace.yaml"
    if pnpm.is_file():
        try:
            text = pnpm.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        # Deliberately not a YAML parser: this is one list of strings, and a dependency
        # for it would be the only one this package has.
        if "packages:" in text:
            after = text.split("packages:", 1)[1]
            for match in _PNPM_PACKAGES_RE.finditer(after):
                value = match.group(1).strip()
                if not value or value.endswith(":"):
                    break
                globs.append(value)

    lerna = _read_json(root / "lerna.json")
    globs += [g for g in (lerna.get("packages") or []) if isinstance(g, str)]
    return [g for g in dict.fromkeys(globs) if not g.startswith("!")]


# Scripts whose command starts a long-running process. A package with one of these is
# something you deploy; a package with only `build` and `lint` is something you import.
_SERVER_HINTS = (
    "next start", "next dev", "nest start", "node dist", "node server", "node ./server",
    "uvicorn", "gunicorn", "fastapi run", "flask run", "django", "manage.py runserver",
    "nodemon", "ts-node", "tsx watch", "remix-serve", "vite preview", "astro dev",
    "nuxt start", "nuxt dev", "serve", "start-server",
)
# Directories a repository puts its deployables in, by near-universal convention.
_APP_DIRS = ("apps", "services", "cmd", "backend", "frontend", "server", "web")


def _is_deployable(directory: pathlib.Path, rel: str, evidence: list[str]) -> bool:
    """Whether this package is something you run, rather than something you import."""
    if "Dockerfile" in evidence:
        return True
    head = rel.split("/", 1)[0] if "/" in rel else ""
    if head in _APP_DIRS:
        return True
    package = _read_json(directory / "package.json")
    scripts = package.get("scripts") or {}
    if isinstance(scripts, dict):
        for name in ("start", "dev", "serve", "start:prod"):
            command = scripts.get(name)
            if isinstance(command, str) and any(h in command for h in _SERVER_HINTS):
                return True
    # A Python or Go project in its own directory is nearly always a deployable: neither
    # ecosystem uses a nested project file for a shared library inside a monorepo the way
    # JavaScript does.
    return bool(
        (directory / "pyproject.toml").is_file() and (directory / "package.json").is_file() is False
    ) or (directory / "go.mod").is_file()


def _language_of(directory: pathlib.Path) -> str:
    if (directory / "manage.py").is_file():
        return "Python"
    if (directory / "package.json").is_file():
        return "JavaScript"
    if any((directory / n).is_file() for n in ("pyproject.toml", "setup.py", "requirements.txt")):
        return "Python"
    if (directory / "go.mod").is_file():
        return "Go"
    if (directory / "Cargo.toml").is_file():
        return "Rust"
    if any(directory.glob("*.csproj")):
        return "C#"
    if (directory / "pom.xml").is_file() or (directory / "build.gradle").is_file():
        return "Java"
    return ""


def _walk_manifests(root: pathlib.Path) -> dict[str, list[str]]:
    """Directory -> the manifest files in it, for every directory worth considering."""
    found: dict[str, list[str]] = {}
    root_str = str(root)
    for current, directories, files in os.walk(root):
        depth = current[len(root_str):].count(os.sep)
        if depth >= _MAX_DEPTH:
            directories[:] = []
            continue
        directories[:] = [
            d for d in directories if d not in SKIP_DIRS and not d.startswith(".")
        ]
        names = [
            f for f in files
            if f in ("package.json", "pyproject.toml", "go.mod", "Cargo.toml", "pom.xml",
                     # A Django service in a monorepo often carries no packaging manifest
                     # at all - the dependencies live at the repository root. manage.py is
                     # the marker it always has, and without it the Python half of a
                     # polyglot repo is invisible: every one of its files was being
                     # attributed to the Node service beside it.
                     "manage.py")
            or f.startswith("Dockerfile")
        ]
        if names:
            found[current] = names
    return found


def _compose_services(root: pathlib.Path) -> set[str]:
    """Service names declared in a compose file, as corroboration rather than as roots.

    A compose file names the RUNTIME topology, which is what an architect draws, but its
    services point at build contexts and images rather than at source directories - so it
    is read to confirm that a repository is multi-service, never to invent a root.
    """
    names: set[str] = set()
    for directory in _COMPOSE_DIRS:
        for filename in _COMPOSE_NAMES:
            path = root / directory / filename
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "services:" not in text:
                continue
            body = text.split("services:", 1)[1]
            for line in body.splitlines():
                if line and not line[0].isspace():
                    break  # a new top-level key ended the services block
                match = re.match(r"^\s{2}([A-Za-z0-9][\w.-]*):\s*$", line)
                if match:
                    names.add(match.group(1))
    return names


def detect_services(repo_root: str) -> list[Service]:
    """Every service this repository declares, outermost first.

    Returns a single root service when nothing says otherwise, so callers never need a
    special case for the ordinary repository.
    """
    root = pathlib.Path(repo_root).resolve()
    globs = _globs_from_workspaces(root)
    manifests = _walk_manifests(root)
    compose = _compose_services(root)

    services: dict[str, Service] = {}

    def _relative(directory: str) -> str:
        rel = os.path.relpath(directory, root).replace(os.sep, "/")
        return "" if rel == "." else rel

    # 1. Workspace globs are the strongest signal: the repository itself says these
    #    directories are separate packages.
    for pattern in globs:
        for match in sorted(root.glob(pattern)):
            if not match.is_dir() or not (match / "package.json").is_file():
                continue
            rel = _relative(str(match))
            parts = pathlib.PurePath(rel).parts
            if not rel or any(p in SKIP_DIRS or p.startswith(".") for p in parts):
                continue
            package = _read_json(match / "package.json")
            evidence = ["workspace"]
            if any(match.glob("Dockerfile*")):
                evidence.append("Dockerfile")
            services[rel] = Service(
                name=package.get("name") or match.name,
                root=rel,
                language=_language_of(match),
                evidence=evidence,
                deployable=_is_deployable(match, rel, evidence),
            )

    # 2. A Python project or a Dockerfile in a subdirectory is its own deployable, and
    #    neither appears in a JavaScript workspace list - which is exactly how the
    #    Python half of a polyglot repository went missing.
    for directory, names in manifests.items():
        rel = _relative(directory)
        if not rel:
            continue
        has_project = any(
            # manage.py counts: a Django service inside a monorepo frequently declares
            # its dependencies at the repository root and carries no manifest of its own,
            # and without this the entire Python half of a polyglot repository is not a
            # service at all - it is unattributed files sitting beside the Node one.
            n in ("pyproject.toml", "go.mod", "Cargo.toml", "pom.xml", "manage.py")
            for n in names
        )
        has_docker = any(n.startswith("Dockerfile") for n in names)
        if not (has_project or has_docker):
            continue
        if rel in services:
            services[rel].evidence.append("Dockerfile" if has_docker else "project file")
            continue
        # A Dockerfile alone, with no manifest and no source beneath it, is a build recipe
        # rather than a service - the repository root's own Dockerfile is the usual case.
        language = _language_of(pathlib.Path(directory))
        if not has_project and not language:
            continue
        evidence = ["project file" if has_project else "Dockerfile"]
        if has_docker:
            evidence.append("Dockerfile")
        services[rel] = Service(
            name=pathlib.PurePath(rel).name,
            root=rel,
            language=language,
            evidence=sorted(set(evidence)),
            deployable=_is_deployable(pathlib.Path(directory), rel, evidence),
        )

    if not services:
        return [Service(name=root.name, root="", language=_language_of(root),
                        evidence=["single service"], deployable=True)]

    # Nested services: `apps/api` inside `apps` would otherwise swallow it. Sorting by
    # depth and keeping the most specific match is done at lookup time, so both stay.
    ordered = sorted(services.values(), key=lambda s: (s.root.count("/"), s.root))
    if compose:
        for service in ordered:
            if service.name in compose or pathlib.PurePath(service.root).name in compose:
                service.evidence.append("compose")
    return ordered


class ServiceMap:
    """Answers 'which service owns this file', longest root first."""

    def __init__(self, services: list[Service]) -> None:
        # Deepest root wins, so `apps/api/worker` is not answered with `apps/api`.
        self.services = sorted(services, key=lambda s: -len(s.root))
        self.single = len(services) <= 1

    def of(self, repo_relative_file: str) -> str:
        if not self.services:
            return ""
        # One service rooted at "" IS the repository, and every file belongs to it. One
        # service rooted at `services/api` does NOT own `services/web` - answering with
        # its name labelled a whole Django application as the Node service next to it,
        # which is worse than admitting the file belongs to no detected service.
        if self.single and not self.services[0].root:
            return self.services[0].name
        if not repo_relative_file:
            return ""
        path = repo_relative_file.replace(os.sep, "/")
        for service in self.services:
            if service.contains(path):
                return service.name
        return ""

    def crossings(self, symbols, edges) -> list[tuple[str, str, int]]:
        """(from service, to service, how many edges) for every edge that leaves a service.

        This is the architecture diagram: each row is one service calling another, counted.
        An edge inside a service is ordinary code and is not reported.
        """
        where = {symbol.id: self.of(symbol.file or "") for symbol in symbols}
        counted: dict[tuple[str, str], int] = {}
        for edge in edges:
            source, target = where.get(edge.from_id), where.get(edge.to_id)
            if not source or not target or source == target:
                continue
            counted[(source, target)] = counted.get((source, target), 0) + 1
        return sorted(
            ((a, b, n) for (a, b), n in counted.items()), key=lambda row: -row[2]
        )
