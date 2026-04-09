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
    """Compute blast_ratio: how concise the agent's change was relative to the creator's.

    Phase relevance: PHASE_B only.
        In Phase A (human PR, self-reference), gate.py sets creator_patch == agent_patch,
        making blast_ratio always 1.0. The gate then overrides it with a "phase_a_na" note.
        This function's output is not used for pass/fail decisions in Phase A.

    Formula:
        blast_ratio = min(1.0, creator_ref_loc / agent_loc)

        where:
            agent_loc       = non-whitespace added + deleted lines in agent_patch
            creator_ref_loc = non-whitespace added + deleted lines in creator_patch

        Capped at 1.0 so agents that are more concise than the creator are not penalised.

    Examples:
        Creator wrote 100 LOC, agent wrote 80 LOC  → min(1.0, 100/80) = 1.0  (PASS)
        Creator wrote 100 LOC, agent wrote 200 LOC → min(1.0, 100/200) = 0.5 (borderline, threshold=0.5)
        Creator wrote 50 LOC,  agent wrote 300 LOC → min(1.0, 50/300)  = 0.17 (FAIL)

    Edge cases:
        agent_loc == 0 and creator_ref_loc == 0 → blast_ratio = 1.0 (both empty, vacuous pass)
        agent_loc == 0 and creator_ref_loc > 0  → blast_ratio = 0.0 (agent produced nothing)

    Auto-generated files (lock files, minified JS, protobuf, etc.) are excluded from
    LOC counting by default and via extra_patterns.

    Args:
        agent_patch:      Unified diff of the agent's changes.
        creator_patch:    Unified diff of the creator's reference patch.
        exclude_patterns: Extra glob patterns for files to exclude from LOC counting.

    Returns:
        Tuple of (blast_ratio, detail_dict). detail_dict includes agent_loc,
        creator_ref_loc, formula, and any excluded files.
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
    """Legacy alias for compute_blast_metrics. Prefer compute_blast_metrics in new code."""
    return compute_blast_metrics(
        agent_patch, creator_patch, exclude_patterns=exclude_patterns
    )
