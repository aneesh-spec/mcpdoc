"""Architectural Gate orchestrator: metrics, thresholds, JSON result, audit logging.

Two-phase evaluation model
--------------------------
Phase A — Human PR validation (EvaluationMode.PHASE_A)
    Called when a human annotator opens a PR with a creator patch + F2P tests.
    No agent patch exists yet. The PR diff IS the only patch available.

    Meaningful checks: api_surface_score, new_dependencies_count, dead_code_count.
    N/A checks:        scope_score = 1.0, blast_ratio = 1.0 (no reference to compare).

    Example invocation:
        gate.evaluate(
            agent_patch=pr_diff,   # the human's PR diff
            creator_patch="",      # ignored in Phase A
            task_config=task_yaml,
            repo_snapshot=RepoSnapshot(before_root=base_tree, after_root=pr_tree),
            mode=EvaluationMode.PHASE_A,
        )

Phase B — Agent patch evaluation (EvaluationMode.PHASE_B)
    Called when an AI agent produces a patch for a frozen task from Phase A.
    The creator patch from Phase A is the reference.

    Meaningful checks: all five (scope, blast, api, new_deps, dead_code).

    Example invocation:
        gate.evaluate(
            agent_patch=agent_diff,
            creator_patch=creator_diff,   # frozen from Phase A
            task_config=task_yaml,
            repo_snapshot=RepoSnapshot(before_root=base_tree, after_root=agent_tree),
            mode=EvaluationMode.PHASE_B,
        )

Failure tags
------------
    arch_scope_violation      — scope_score below threshold (Phase B only)
    arch_blast_violation      — blast_ratio below threshold (Phase B only)
    arch_api_violation        — api_surface_score below threshold
    arch_dependency_violation — new_dependencies_count above threshold
    arch_dead_code_violation  — dead_code_count above threshold
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Mapping

import yaml

from architectural_gate.api_checker import compute_api_surface_detailed
from architectural_gate.blast_analyzer import compute_blast_metrics
from architectural_gate.dead_code_checker import count_dead_code_rule_types
from architectural_gate.dependency_checker import count_new_dependencies
from architectural_gate.import_diff import compute_import_diff
from architectural_gate.models import (
    EvaluationMode,
    GateResult,
    GateThresholds,
    RepoSnapshot,
)
from architectural_gate.scope_analyzer import (
    SameDirectoryAdjacencyPolicy,
    compute_scope_metrics,
)

logger = logging.getLogger(__name__)

FAIL_SCOPE = "arch_scope_violation"
FAIL_BLAST = "arch_blast_violation"
FAIL_API = "arch_api_violation"
FAIL_DEP = "arch_dependency_violation"
FAIL_DEAD = "arch_dead_code_violation"


def _repo_snapshot_mode(snapshot: RepoSnapshot) -> str:
    """Return a string tag describing which before/after repo roots are available.

    Used in the evaluation block log for audit purposes. Does not affect metric
    computation. Possible values:
        "initial_vs_changed" — both before and after trees present (full comparison)
        "changed_only"       — only after tree present (before state treated as empty)
        "initial_only"       — only before tree present
        "inline_file_maps"   — inline path->content maps provided instead of roots
        "empty"              — no snapshot data at all
    """
    if snapshot.before_root and snapshot.after_root:
        return "initial_vs_changed"
    if snapshot.after_root:
        return "changed_only"
    if snapshot.before_root:
        return "initial_only"
    if snapshot.before_files is not None or snapshot.after_files is not None:
        return "inline_file_maps"
    return "empty"


def _evaluation_block(
    mode: EvaluationMode,
    repo_snapshot: RepoSnapshot,
) -> dict[str, Any]:
    """Build the evaluation metadata block embedded in raw_log.

    Documents the evaluation mode, metric data flows, and failure tag names.
    This block is for human review and audit — it does not affect gate_pass.

    Args:
        mode: PHASE_A or PHASE_B (determines scope/blast N/A note).
        repo_snapshot: Used to determine which snapshot mode is active.
    """
    snap_mode = _repo_snapshot_mode(repo_snapshot)
    is_phase_a = mode == EvaluationMode.PHASE_A
    return {
        "evaluation_mode": mode.value,
        "conjunctive_thresholds": True,
        "gate_pass_requires_all_checks": True,
        "repo_snapshot_mode": snap_mode,
        "metrics_basis": {
            "scope": (
                "N/A in Phase A — scope_score forced to 1.0 (no creator reference). "
                "Phase B: agent file paths vs allowed set from creator reference + adjacency policy."
                if is_phase_a
                else
                "Phase B: agent file paths vs allowed set from creator reference + adjacency policy."
            ),
            "blast_ratio": (
                "N/A in Phase A — blast_ratio forced to 1.0 (no creator reference). "
                "Phase B: min(1.0, creator_ref_loc / agent_loc)."
                if is_phase_a
                else
                "Phase B: min(1.0, creator_ref_loc / agent_loc) from patch LOC counts."
            ),
            "api_surface": (
                "Repo snapshot: breaking public API changes comparing before vs after content."
            ),
            "new_dependencies": (
                "Repo snapshot: dependency names in known manifests, after minus before set."
            ),
            "dead_code": (
                "After-tree linter: distinct linter rule codes vs threshold."
            ),
            "import_diff": (
                "Repo snapshot + agent-touched files: import delta scoped to changed files."
            ),
        },
        "scope_blast_note": (
            "Phase A: creator_patch is set equal to agent_patch internally. "
            "scope_score and blast_ratio are always 1.0 and do not contribute to gate_pass."
            if is_phase_a
            else
            "Phase B: creator_patch is the frozen human reference from Phase A. "
            "scope and blast failures may indicate approach divergence — review before "
            "treating as hard failures."
        ),
        "failure_tags": {
            "scope": FAIL_SCOPE,
            "blast": FAIL_BLAST,
            "api": FAIL_API,
            "dependencies": FAIL_DEP,
            "dead_code": FAIL_DEAD,
        },
    }


def _safe_yaml_dict(text: str) -> dict[str, Any]:
    """Parse YAML text into a dict. Returns {} on any parse error or non-dict result.

    Safe to call on untrusted input — never raises. Logs a warning on parse errors.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        logger.warning("task_config YAML parse error: %s", e)
        return {}
    return data if isinstance(data, dict) else {}


def load_task_config(
    task_config: str | Path | Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Load task configuration from a file path, raw YAML string, or pre-parsed mapping.

    Accepts three formats:
        - Path or path string pointing to a YAML file on disk.
        - Raw YAML string starting with "{", "architectural", or "architectural_gate:".
        - A pre-parsed dict/Mapping (returned as-is).
        - None (returns empty dict, all defaults apply).

    Never raises — returns {} on any error so gate evaluation always proceeds.

    Args:
        task_config: YAML file path, raw YAML text, pre-parsed mapping, or None.

    Returns:
        Parsed configuration dict, or {} if input is invalid/missing.
    """
    if task_config is None:
        return {}
    if isinstance(task_config, Mapping):
        return dict(task_config)
    path = Path(task_config)
    if path.is_file():
        return _safe_yaml_dict(path.read_text(encoding="utf-8"))
    raw = str(task_config).strip()
    if (
        raw.startswith("{")
        or raw.startswith("architectural")
        or raw.startswith("architectural_gate:")
    ):
        return _safe_yaml_dict(raw)
    return {}


class ArchitecturalGate:
    """Evaluates patches and repo snapshots against architectural quality thresholds.

    Supports two evaluation modes (see EvaluationMode):
        PHASE_A — Human PR validation. Only api, deps, dead_code checks are enforced.
                  scope and blast are always 1.0 (N/A).
        PHASE_B — Agent evaluation. All five checks are enforced.

    Metrics computed
    ----------------
    scope_score (Phase B only):
        Fraction of agent-touched files that fall within the allowed set derived from
        the creator patch + adjacency policy.
        Formula: 1 - |agent_files outside allowed| / |agent_files|
        Threshold: >= scope_min (default 0.7)

    blast_ratio (Phase B only):
        How concise the agent's change was relative to the creator's reference.
        Formula: min(1.0, creator_ref_loc / agent_loc)
        Threshold: >= blast_ratio_min (default 0.5)
        Example: creator wrote 100 LOC, agent wrote 300 LOC → 100/300 = 0.33 → FAIL

    api_surface_score (both phases):
        Fraction of public API interfaces that were not broken.
        Formula: 1 - breaking_changes / total_interfaces
        Threshold: >= api_surface_min (default 1.0, i.e. zero breaking changes)

    new_dependencies_count (both phases):
        Count of new external packages added vs baseline.
        Threshold: <= max_new_dependencies (default 0)

    dead_code_count (both phases):
        Count of distinct linter rule codes triggered in the after-state tree.
        Threshold: <= max_dead_code (default 2)

    Args:
        scope_policy: Pluggable adjacency policy for allowed-file expansion.
                      Defaults to SameDirectoryAdjacencyPolicy (creator dirs + all files within).
        ruff_config:  Optional path to a ruff config file for Python dead-code checks.
    """

    def __init__(
        self,
        scope_policy: Any | None = None,
        ruff_config: Path | None = None,
    ) -> None:
        self.scope_policy = scope_policy or SameDirectoryAdjacencyPolicy()
        self.ruff_config = ruff_config

    def evaluate(
        self,
        agent_patch: str,
        creator_patch: str,
        task_config: str | Path | Mapping[str, Any] | None,
        repo_snapshot: RepoSnapshot,
        language: str = "python",
        repo_root: Path | None = None,
        self_reference: bool = False,
        mode: EvaluationMode | None = None,
    ) -> GateResult:
        """Run all architectural checks and return a GateResult.

        Args:
            agent_patch:    Unified diff of the agent's changes (Phase B) or the
                            human PR diff (Phase A).
            creator_patch:  Unified diff of the creator's reference patch (Phase B only).
                            Ignored in Phase A — pass "" or the same patch; it will be
                            overridden internally.
            task_config:    Task YAML with threshold overrides, or None for defaults.
                            See load_task_config() for accepted formats.
            repo_snapshot:  Before/after repo state for api, deps, dead-code checks.
                            Provide before_root + after_root for full before/after comparison.
            language:       Programming language for API and dead-code checks.
                            Supported: "python", "javascript", "go", "rust".
            repo_root:      Repo root used by SameDirectoryAdjacencyPolicy to expand
                            allowed directories to all files within them.
            self_reference: Deprecated. Pass mode=EvaluationMode.PHASE_A instead.
                            If True and mode is None, treated as PHASE_A.
            mode:           EvaluationMode.PHASE_A or PHASE_B. Takes precedence over
                            self_reference when set.

        Returns:
            GateResult with:
                .architectural — public JSON schema dict (always produced)
                .failures      — list of failure tag strings
                .raw_log       — full audit log for human review
        """
        # Resolve evaluation mode
        if mode is None:
            mode = EvaluationMode.PHASE_A if self_reference else EvaluationMode.PHASE_B
        is_phase_a = mode == EvaluationMode.PHASE_A

        # Phase A: force creator_patch == agent_patch so scope/blast formulas
        # produce deterministic 1.0 values. The results are then overridden below
        # to make the N/A intent explicit.
        if is_phase_a:
            creator_patch = agent_patch

        cfg_raw = load_task_config(task_config)
        thresholds, overrides = GateThresholds.from_task_config(cfg_raw)
        exclude_patterns = thresholds.exclude_patterns

        # --- Scope (Phase B meaningful; Phase A always 1.0) ---
        scope_detail = compute_scope_metrics(
            agent_patch,
            creator_patch,
            repo_root,
            policy=self.scope_policy,
            exclude_patterns=exclude_patterns,
        )

        # --- Blast (Phase B meaningful; Phase A always 1.0) ---
        blast_ratio, blast_detail = compute_blast_metrics(
            agent_patch, creator_patch, exclude_patterns=exclude_patterns
        )
        agent_loc = int(blast_detail["agent_loc"])
        creator_ref_loc = int(blast_detail["creator_ref_loc"])

        # Phase A override: scope and blast are N/A — force to 1.0 regardless
        # of what the analyzers computed (they ran on identical patches, so they
        # would have returned 1.0 anyway, but we make this explicit).
        if is_phase_a:
            scope_score = 1.0
            blast_ratio = 1.0
            scope_detail["scope_score"] = 1.0
            scope_detail["note"] = "phase_a_na"
            blast_detail["note"] = "phase_a_na"
        else:
            scope_score = float(scope_detail["scope_score"])

        # --- API surface (both phases) ---
        api_surface_score, _bc, _iface, api_detail = compute_api_surface_detailed(
            repo_snapshot,
            language,
        )

        # --- New dependencies (both phases) ---
        new_deps, dep_detail = count_new_dependencies(repo_snapshot)

        # --- Dead code (both phases) ---
        dead_n, dead_detail = count_dead_code_rule_types(
            repo_snapshot,
            language,
            ruff_config=self.ruff_config,
        )

        # --- Import diff (scoped to agent-touched files) ---
        agent_files_for_imports = set(scope_detail.get("agent_files") or [])
        import_diff = compute_import_diff(
            repo_snapshot, language, touched_files=agent_files_for_imports
        )

        # --- Threshold evaluation ---
        thr_out = {
            "scope_min": thresholds.scope_min,
            "blast_ratio_min": thresholds.blast_ratio_min,
            "api_surface_min": thresholds.api_surface_min,
            "max_new_dependencies": thresholds.max_new_dependencies,
            "max_dead_code": thresholds.max_dead_code,
        }

        # In Phase A, scope and blast always pass — thresholds are irrelevant for them.
        scope_pass = True if is_phase_a else (scope_score >= thresholds.scope_min)
        blast_pass = True if is_phase_a else (blast_ratio >= thresholds.blast_ratio_min)
        api_pass = thresholds.allow_api_breaks or (
            api_surface_score >= thresholds.api_surface_min
        )
        dependency_pass = thresholds.allow_new_dependencies or (
            new_deps <= thresholds.max_new_dependencies
        )
        dead_code_pass = dead_n <= thresholds.max_dead_code

        gate_pass = all((scope_pass, blast_pass, api_pass, dependency_pass, dead_code_pass))

        failures: list[str] = []
        if not scope_pass:
            failures.append(FAIL_SCOPE)
        if not blast_pass:
            failures.append(FAIL_BLAST)
        if not api_pass:
            failures.append(FAIL_API)
        if not dependency_pass:
            failures.append(FAIL_DEP)
        if not dead_code_pass:
            failures.append(FAIL_DEAD)

        # Public JSON schema — fixed keys and order; values are always dynamic.
        architectural: dict[str, Any] = {
            "scope_score": scope_score,
            "blast_ratio": float(blast_ratio),
            "api_surface_score": float(api_surface_score),
            "new_dependencies_count": int(new_deps),
            "dead_code_count": int(dead_n),
            "agent_loc": agent_loc,
            "creator_ref_loc": creator_ref_loc,
            "files_modified": list(scope_detail.get("files_modified") or []),
            "files_outside_scope": list(scope_detail.get("files_outside_scope") or []),
            "import_diff": import_diff,
            "thresholds": thr_out,
            "scope_pass": scope_pass,
            "blast_pass": blast_pass,
            "api_pass": api_pass,
            "dependency_pass": dependency_pass,
            "dead_code_pass": dead_code_pass,
            "gate_pass": gate_pass,
        }

        raw_log: dict[str, Any] = {
            "evaluation_mode": mode.value,
            "evaluation": _evaluation_block(mode, repo_snapshot),
            "thresholds_default": {
                "scope_min": 0.7,
                "blast_ratio_min": 0.5,
                "api_surface_min": 1.0,
                "max_new_dependencies": 0,
                "max_dead_code": 2,
            },
            "thresholds_overrides": overrides,
            "thresholds_effective": thr_out,
            "allow_new_dependencies": thresholds.allow_new_dependencies,
            "allow_api_breaks": thresholds.allow_api_breaks,
            "language": language,
            "repo_root": str(repo_root) if repo_root else None,
            "scope_detail": scope_detail,
            "blast_detail": blast_detail,
            "api_detail": api_detail,
            "dependency_detail": dep_detail,
            "dead_code_detail": dead_detail,
            "failures": failures,
        }

        logger.info(
            "ArchitecturalGate mode=%s scope=%s blast=%s api=%s deps=%s dead=%s gate_pass=%s",
            mode.value,
            scope_score,
            blast_ratio,
            api_surface_score,
            new_deps,
            dead_n,
            gate_pass,
        )

        return GateResult(
            architectural=architectural, failures=failures, raw_log=raw_log
        )


def evaluate_gate(
    agent_patch: str,
    creator_patch: str,
    task_config: str | Path | Mapping[str, Any] | None,
    repo_snapshot: RepoSnapshot,
    language: str = "python",
    repo_root: Path | None = None,
    scope_policy: Any | None = None,
    ruff_config: Path | None = None,
    self_reference: bool = False,
    mode: EvaluationMode | None = None,
) -> GateResult:
    """Functional entrypoint for the architectural gate. Equivalent to ArchitecturalGate().evaluate().

    Prefer this for simple call sites that don't need to share a gate instance.
    See ArchitecturalGate.evaluate() for full parameter documentation.
    """
    gate = ArchitecturalGate(scope_policy=scope_policy, ruff_config=ruff_config)
    return gate.evaluate(
        agent_patch,
        creator_patch,
        task_config,
        repo_snapshot,
        language=language,
        repo_root=repo_root,
        self_reference=self_reference,
        mode=mode,
    )


def result_to_json(gr: GateResult) -> str:
    """Serialize the public architectural output to JSON.

    Only the ``architectural`` dict is included — raw_log and failures are
    intentionally excluded from this output (they are for internal audit only).
    The schema is stable: all keys are always present regardless of pass/fail.
    """
    return json.dumps({"architectural": gr.architectural}, indent=2)
