"""Architectural Gate orchestrator: metrics, thresholds, JSON result, audit logging."""

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
from architectural_gate.models import GateResult, GateThresholds, RepoSnapshot
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
    """How API/deps/dead-code snapshot is bound to disk or inline maps."""
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
    self_reference: bool,
    repo_snapshot: RepoSnapshot,
) -> dict[str, Any]:
    """
    Documents how metrics map to the spec: conjunctive checks, initial-vs-PR data flow,
    and when scope/blast use a separate creator reference patch.
    """
    snap_mode = _repo_snapshot_mode(repo_snapshot)
    return {
        "conjunctive_thresholds": True,
        "gate_pass_requires_all_checks": True,
        "repo_snapshot_mode": snap_mode,
        "metrics_basis": {
            "scope": (
                "unified_diff: agent paths vs allowed set from creator reference + adjacency "
                "(dynamic from change patch; repo_root scopes directory expansion)"
            ),
            "blast_ratio": (
                "unified_diff: creator_ref_loc / agent_loc (capped); both from patch text, dynamic per run"
            ),
            "api_surface": (
                "repo snapshot: breaking public API changes comparing before vs after content "
                "(dynamic when initial_vs_changed)"
            ),
            "new_dependencies": (
                "repo snapshot: dependency names in known manifests, after minus before "
                "(dynamic when initial_vs_changed)"
            ),
            "dead_code": (
                "after-tree linter (or before if no after_root): distinct rule codes vs threshold "
                "(dynamic on changed tree)"
            ),
            "import_diff": (
                "repo snapshot + paths touched in scope detail: import delta for touched files"
            ),
        },
        "scope_blast_creator_reference": (
            "change_patch_equals_creator_reference"
            if self_reference
            else "external_creator_patch"
        ),
        "scope_blast_note": (
            "With self-reference, creator and agent patches are the same unified diff (e.g. "
            "initial→PR only). Scope and blast scores are neutral vs that single change; "
            "set a separate creator patch for creator-relative scope/blast (benchmarks)."
            if self_reference
            else "Creator patch differs from agent patch; scope and blast are relative to that reference.",
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
    """Parse YAML; invalid or non-dict input yields {} so evaluate() never crashes."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        logger.warning("task_config YAML parse error: %s", e)
        return {}
    return data if isinstance(data, dict) else {}


def load_task_config(
    task_config: str | Path | Mapping[str, Any] | None,
) -> dict[str, Any]:
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
    """Evaluates unified-diff metrics (scope, blast) and repo-snapshot metrics (API, deps, dead code).

    With ``RepoSnapshot(before_root=..., after_root=...)``, API and dependencies compare **initial
    vs changed** trees dynamically. Scope and blast use the agent/creator patch strings; when only
    an initial→changed diff exists, use ``self_reference=True`` so both sides match that patch, or
    supply a separate creator patch for reference-relative scope/blast.
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
    ) -> GateResult:
        """If ``self_reference`` is True, creator_patch is ignored and set equal to agent_patch
        (scope/blast are vacuously satisfied; API/deps/dead-code still apply). Used when CI only
        has the current change, not a separate reference patch.
        """
        if self_reference:
            creator_patch = agent_patch
        cfg_raw = load_task_config(task_config)
        thresholds, overrides = GateThresholds.from_task_config(cfg_raw)

        exclude_patterns = thresholds.exclude_patterns

        scope_detail = compute_scope_metrics(
            agent_patch,
            creator_patch,
            repo_root,
            policy=self.scope_policy,
            exclude_patterns=exclude_patterns,
        )
        scope_score = float(scope_detail["scope_score"])

        blast_ratio, blast_detail = compute_blast_metrics(
            agent_patch, creator_patch, exclude_patterns=exclude_patterns
        )
        agent_loc = int(blast_detail["agent_loc"])
        creator_ref_loc = int(blast_detail["creator_ref_loc"])

        api_surface_score, _bc, _iface, api_detail = compute_api_surface_detailed(
            repo_snapshot,
            language,
        )

        new_deps, dep_detail = count_new_dependencies(repo_snapshot)
        dead_n, dead_detail = count_dead_code_rule_types(
            repo_snapshot,
            language,
            ruff_config=self.ruff_config,
        )

        # Scope import_diff to agent-touched files only (empty set => []; never use `set() or None`
        # which wrongly widens to full-repo scan).
        agent_files_for_imports = set(scope_detail.get("agent_files") or [])
        import_diff = compute_import_diff(
            repo_snapshot, language, touched_files=agent_files_for_imports
        )

        thr_out = {
            "scope_min": thresholds.scope_min,
            "blast_ratio_min": thresholds.blast_ratio_min,
            "api_surface_min": thresholds.api_surface_min,
            "max_new_dependencies": thresholds.max_new_dependencies,
            "max_dead_code": thresholds.max_dead_code,
        }

        scope_pass = scope_score >= thresholds.scope_min
        blast_pass = blast_ratio >= thresholds.blast_ratio_min
        api_pass = thresholds.allow_api_breaks or (
            api_surface_score >= thresholds.api_surface_min
        )
        dependency_pass = thresholds.allow_new_dependencies or (
            new_deps <= thresholds.max_new_dependencies
        )
        dead_code_pass = dead_n <= thresholds.max_dead_code

        gate_pass = all(
            (scope_pass, blast_pass, api_pass, dependency_pass, dead_code_pass),
        )

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

        # Public JSON schema for `architectural`: fixed keys / order; values are always dynamic.
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
            "reference_mode": "self" if self_reference else "dual",
            "evaluation": _evaluation_block(self_reference, repo_snapshot),
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
            "ArchitecturalGate scope=%s blast=%s api=%s deps=%s dead=%s gate_pass=%s",
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
) -> GateResult:
    """Functional entrypoint."""
    gate = ArchitecturalGate(scope_policy=scope_policy, ruff_config=ruff_config)
    return gate.evaluate(
        agent_patch,
        creator_patch,
        task_config,
        repo_snapshot,
        language=language,
        repo_root=repo_root,
        self_reference=self_reference,
    )


def result_to_json(gr: GateResult) -> str:
    """Serialize public output: top-level ``architectural`` only, stable schema (dynamic values)."""
    return json.dumps({"architectural": gr.architectural}, indent=2)
