"""Class names defined in Sass, SCSS or Less, which compile to CSS nobody commits.

Measured on NetBox: 23 `.scss` files, 7 `.css` files, and none of the real stylesheets in
the repository at all - they are built during deployment. Seamcheck read only `.css`,
concluded the project had no local styles, and correctly declined to judge 4,687 of its
5,367 symbols. Coverage: 12%. The tool was not wrong, it was blind, and being honestly
blind on a mainstream Django layout is still useless.

**This is evidence, never an inventory - and the distinction is the whole design.** A
preprocessor source cannot be enumerated the way a stylesheet can:

    .card { &__title { } &--wide { } }      // defines card__title and card--wide
    @each $n in primary, danger {
      .btn-#{$n} { }                        // defines btn-primary and btn-danger
    }

The second form is a loop over a variable, and there is no honest way to read it without
running Sass. So what comes out of here may say "this class IS defined" and may never say
"this class is NOT defined". Concretely: these names let a class in a template resolve, and
they never make a rule `unused`. Over-collecting therefore costs a missed finding, which
this project has always preferred to a false one.

Same rail as the Tailwind build set, for the same reason: a class the build emits is real
whether or not any file in the repository spells it out.
"""

from __future__ import annotations

import pathlib
import re

SUFFIXES = (".scss", ".sass", ".less")

# `//` line comments (Sass) and `/* */` blocks. Stripped first so a class name mentioned in
# a comment does not count as a definition.
_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)

# A class in selector position: `.name` not preceded by a word character, so `x.name` (a
# property value or an interpolation fragment) does not match.
_CLASS = re.compile(r"(?<![\w&#$-])\.(-?[A-Za-z_][\w-]*)")

# BEM concatenation onto the parent: `&__title`, `&--wide`, `&-inner`.
_AMPERSAND = re.compile(r"&(-{1,2}|__|_)([A-Za-z_][\w-]*)")

# A name built from a variable - `.btn-#{$name}`. The literal half is a stem, not a class,
# and it is what stops the compiled `btn-primary` from being called dead elsewhere.
_INTERPOLATED = re.compile(r"(?<![\w&#$-])\.(-?[A-Za-z_][\w-]*?)-?#\{")


def preprocessor_files(roots: list[str] | None) -> list[str]:
    """Every Sass/SCSS/Less source under the given roots."""
    found: list[str] = []
    for root in roots or []:
        base = pathlib.Path(root)
        if not base.is_dir():
            continue
        for suffix in SUFFIXES:
            found += [
                str(path) for path in base.rglob(f"*{suffix}")
                # node_modules is somebody else's Bootstrap, and it is enormous.
                if "node_modules" not in path.parts
            ]
    return sorted(dict.fromkeys(found))


def preprocessor_classes(paths: list[str]) -> tuple[set[str], set[str]]:
    """(class names defined, name stems that a loop or variable completes).

    The stems matter as much as the names. `.btn-#{$variant}` yields the stem `btn`, and a
    template's `btn-danger` is then unprovable rather than dead - which is the truth.
    """
    classes: set[str] = set()
    stems: set[str] = set()
    for path in paths:
        try:
            source = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        source = _COMMENTS.sub(" ", source)
        here = set(_CLASS.findall(source))
        classes |= here
        stems |= set(_INTERPOLATED.findall(source))
        # `&__title` inside `.card` is `card__title`. Which parent applies needs a real
        # parser; within one file the candidates are few, and a wrong guess here can only
        # withhold a finding, never invent one.
        joins = _AMPERSAND.findall(source)
        if joins and here:
            for parent in here:
                for separator, tail in joins:
                    classes.add(f"{parent}{separator}{tail}")
    return classes, stems
