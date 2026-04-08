"""A_scope: scope ratio — overlap of agent files vs allowed set (creator + policy)."""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from architectural_gate.diff_utils import (
    collect_paths_under_repo,
    filter_auto_generated_files,
    list_files_from_unified_diff,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _finalize_scope_detail(detail: dict) -> dict:
    """Clamp scope_score to [0, 1]; replace NaN/inf with 0."""
    v = float(detail.get("scope_score", 1.0))
    if math.isnan(v) or math.isinf(v):
        v = 0.0
    detail["scope_score"] = max(0.0, min(1.0, v))
    return detail


class ScopeExpansionPolicy(ABC):
    """TODO-1: Pluggable adjacency / allowed-file expansion beyond creator-touched paths."""

    @abstractmethod
    def allowed_files(
        self,
        creator_modified: set[str],
        repo_root: Path | None,
    ) -> set[str]:
        """Return the expanded set of in-scope paths (relative POSIX)."""


class CreatorOnlyPolicy(ScopeExpansionPolicy):
    """Strict: only files explicitly modified in the creator patch."""

    def allowed_files(
        self, creator_modified: set[str], repo_root: Path | None
    ) -> set[str]:
        return set(creator_modified)


class SameDirectoryAdjacencyPolicy(ScopeExpansionPolicy):
    """Creator files + all files in the same directories (reasonable adjacency)."""

    def allowed_files(
        self, creator_modified: set[str], repo_root: Path | None
    ) -> set[str]:
        allowed = set(creator_modified)
        dirs: set[str] = set()
        for f in creator_modified:
            if "/" in f:
                dirs.add(f.rsplit("/", 1)[0])
            else:
                dirs.add(".")
        if repo_root:
            allowed |= collect_paths_under_repo(repo_root, dirs)
        return allowed


def compute_scope_metrics(
    agent_patch: str,
    creator_patch: str,
    repo_root: Path | None,
    policy: ScopeExpansionPolicy | None = None,
    exclude_patterns: tuple[str, ...] = (),
) -> dict:
    """
    scope_score = 1 − files_extra / files_modified (files_extra = agent files outside allowed set).
    files_modified: agent-touched paths; files_outside_scope: agent ∩ ¬allowed.
    Auto-generated files are excluded from scoring.
    """
    policy = policy or SameDirectoryAdjacencyPolicy()
    agent_files_raw = list_files_from_unified_diff(agent_patch)
    creator_files_raw = list_files_from_unified_diff(creator_patch)

    # Filter auto-generated files before scoring
    agent_files = filter_auto_generated_files(agent_files_raw, exclude_patterns)
    creator_files = filter_auto_generated_files(creator_files_raw, exclude_patterns)
    excluded_agent = sorted(agent_files_raw - agent_files)
    excluded_creator = sorted(creator_files_raw - creator_files)
    files_modified = sorted(agent_files)

    detail: dict = {
        "agent_files": list(files_modified),
        "creator_files": sorted(creator_files),
        "policy": policy.__class__.__name__,
        "files_modified": list(files_modified),
        "files_outside_scope": [],
        "scope_score": 1.0,
    }
    if excluded_agent:
        detail["excluded_agent_files"] = excluded_agent
    if excluded_creator:
        detail["excluded_creator_files"] = excluded_creator

    if not creator_files:
        if not agent_files:
            detail["note"] = "empty_creator_and_agent"
            return _finalize_scope_detail(detail)
        outside = set(agent_files)
        detail["note"] = "empty_creator_patch"
        detail["files_outside_scope"] = sorted(outside)
        detail["scope_score"] = (
            1.0 - (len(outside) / len(agent_files)) if agent_files else 1.0
        )
        return _finalize_scope_detail(detail)

    allowed = policy.allowed_files(creator_files, repo_root)
    detail["allowed_files_count"] = len(allowed)
    detail["allowed_sample"] = sorted(allowed)[:50]

    if not agent_files:
        detail["note"] = "agent_empty"
        return _finalize_scope_detail(detail)

    outside = agent_files - allowed
    detail["files_outside_scope"] = sorted(outside)
    detail["intersection"] = sorted(agent_files & allowed)
    fm = len(agent_files)
    detail["scope_score"] = 1.0 - (len(outside) / fm)
    detail["formula"] = "1 - |agent \\ allowed| / |agent|"
    logger.debug(
        "scope_score=%s files_modified=%s outside=%s",
        detail["scope_score"],
        fm,
        len(outside),
    )
    return _finalize_scope_detail(detail)


def compute_scope_ratio(
    agent_patch: str,
    creator_patch: str,
    repo_root: Path | None,
    policy: ScopeExpansionPolicy | None = None,
    exclude_patterns: tuple[str, ...] = (),
) -> tuple[float, dict]:
    """Returns (scope_score, detail) for backward compatibility."""
    detail = compute_scope_metrics(
        agent_patch,
        creator_patch,
        repo_root,
        policy=policy,
        exclude_patterns=exclude_patterns,
    )
    return float(detail["scope_score"]), detail
