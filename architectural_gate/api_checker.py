"""A_api: public API surface stability (language adapters)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from architectural_gate.plugins.base import get_api_adapter

if TYPE_CHECKING:
    from architectural_gate.models import RepoSnapshot

logger = logging.getLogger(__name__)


def collect_snapshot_paths(snapshot: RepoSnapshot) -> set[str]:
    paths: set[str] = set()
    if snapshot.before_files:
        paths |= set(snapshot.before_files.keys())
    if snapshot.after_files:
        paths |= set(snapshot.after_files.keys())
    if snapshot.before_root and snapshot.before_root.is_dir():
        for p in snapshot.before_root.rglob("*"):
            if p.is_file():
                try:
                    paths.add(p.relative_to(snapshot.before_root).as_posix())
                except ValueError:
                    continue
    if snapshot.after_root and snapshot.after_root.is_dir():
        for p in snapshot.after_root.rglob("*"):
            if p.is_file():
                try:
                    paths.add(p.relative_to(snapshot.after_root).as_posix())
                except ValueError:
                    continue
    return paths


def _total_interfaces(adapter, paths: set[str], snapshot: RepoSnapshot) -> int:
    n = 0
    for rel in sorted(adapter.relevant_paths(paths)):
        b = snapshot.resolve_file(rel, "before")
        a = snapshot.resolve_file(rel, "after")
        if b is not None:
            n += adapter.interface_count(adapter.extract_public_surface(rel, b))
        elif a is not None:
            n += adapter.interface_count(adapter.extract_public_surface(rel, a))
    return n


def compute_api_surface_detailed(
    snapshot: RepoSnapshot,
    language: str,
) -> tuple[float, int, int, dict]:
    """
    api_surface_score = 1 - breaking_changes_count / interfaces (clamped [0,1]);
    if interfaces == 0: score 1.0 iff no breaks, else 0.0.
    Returns (score, breaking_changes_count, interfaces, detail).
    """
    adapter = get_api_adapter(language)
    detail: dict = {"language": language}
    paths = collect_snapshot_paths(snapshot)
    detail["files_considered"] = len(paths)
    if adapter is None:
        detail["note"] = "no_adapter"
        return 1.0, 0, 0, detail

    breaks = adapter.analyze_repo_pair(paths, snapshot)
    bc = len(breaks)
    iface = _total_interfaces(adapter, paths, snapshot)
    detail["breaking_changes"] = breaks
    detail["breaking_changes_count"] = bc
    detail["interfaces"] = iface

    if iface == 0:
        score = 1.0 if bc == 0 else 0.0
    else:
        score = max(0.0, min(1.0, 1.0 - bc / iface))
    detail["api_surface_score"] = score
    logger.debug("api_surface_score=%s bc=%s iface=%s", score, bc, iface)
    return score, bc, iface, detail


def compute_api_surface_score(
    snapshot: RepoSnapshot,
    language: str,
) -> tuple[float, dict]:
    """Backward-compatible: returns (score, detail)."""
    score, _bc, _iface, detail = compute_api_surface_detailed(snapshot, language)
    detail["score"] = score
    return score, detail
