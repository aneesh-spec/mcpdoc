"""Blast: creator_ref_loc / agent_loc (capped at 1.0) — inverse of raw change ratio."""

from __future__ import annotations

import logging

from architectural_gate.diff_utils import total_changed_loc_filtered

logger = logging.getLogger(__name__)


def compute_blast_metrics(
    agent_patch: str,
    creator_patch: str,
    exclude_patterns: tuple[str, ...] = (),
) -> tuple[float, dict]:
    """
    blast_ratio = min(1.0, creator_ref_loc / agent_loc) when agent_loc > 0.
    If agent_loc == 0 and creator_ref_loc == 0: blast_ratio 1.0 (vacuous).
    If agent_loc == 0 and creator_ref_loc > 0: blast_ratio 0.0 (cannot meet creator-relative
    blast budget with no agent line changes — fails default threshold).
    Auto-generated files are excluded from LOC counting when exclude_patterns are set or
    by default via AUTO_GENERATED_PATTERNS.
    """
    agent_loc, agent_excluded = total_changed_loc_filtered(
        agent_patch, exclude_patterns
    )
    creator_ref_loc, creator_excluded = total_changed_loc_filtered(
        creator_patch, exclude_patterns
    )

    detail: dict = {
        "agent_loc": agent_loc,
        "creator_ref_loc": creator_ref_loc,
        "formula": "min(1.0, creator_ref_loc / agent_loc)",
    }
    if agent_excluded:
        detail["excluded_agent_files"] = agent_excluded
    if creator_excluded:
        detail["excluded_creator_files"] = creator_excluded

    if agent_loc == 0:
        if creator_ref_loc == 0:
            detail["note"] = "both_loc_zero"
            blast_ratio = 1.0
        else:
            detail["note"] = "agent_loc_zero_creator_has_loc"
            blast_ratio = 0.0
        return blast_ratio, detail
    raw = creator_ref_loc / agent_loc
    blast_ratio = min(1.0, raw)
    detail["uncapped_ratio"] = raw
    logger.debug("blast_ratio (capped)=%s", blast_ratio)
    return blast_ratio, detail


def compute_blast_ratio(
    agent_patch: str,
    creator_patch: str,
    exclude_patterns: tuple[str, ...] = (),
) -> tuple[float, dict]:
    """Legacy name: same as compute_blast_metrics (new semantic)."""
    return compute_blast_metrics(
        agent_patch, creator_patch, exclude_patterns=exclude_patterns
    )
