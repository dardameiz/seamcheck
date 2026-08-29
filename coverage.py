"""Scan Coverage: which tracked files the scan actually reasoned about.

A separate axis from symbol status. `unscoped` means "no extractor's walk ever reached
this file", which is a statement about the scan, not about the code.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CoverageResult:
    modeled: list[str]
    recognized_but_empty: list[str]
    unscoped: list[str]


def compute_coverage(
    tracked_files: list[str],
    reachable_files: set[str],
    symbol_producing_files: set[str],
) -> CoverageResult:
    modeled: list[str] = []
    recognized_but_empty: list[str] = []
    unscoped: list[str] = []

    for file_path in tracked_files:
        if file_path not in reachable_files:
            unscoped.append(file_path)
        elif file_path in symbol_producing_files:
            modeled.append(file_path)
        else:
            recognized_but_empty.append(file_path)

    return CoverageResult(
        modeled=modeled, recognized_but_empty=recognized_but_empty, unscoped=unscoped
    )
