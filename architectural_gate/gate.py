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
from architectural_gate.scope_analyzer import SameDirectoryAdjacencyPolicy, compute_scope_metrics

logger = logging.getLogger(__name__)

FAIL_SCOPE = "arch_scope_violation"
FAIL_BLAST = "arch_blast_violation"
FAIL_API = "arch_api_violation"
FAIL_DEP = "arch_dependency_violation"
FAIL_DEAD = "arch_dead_code_violation"


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
    if raw.startswith("{") or raw.startswith("architectural") or raw.startswith("architectural_gate:"):
        return _safe_yaml_dict(raw)
    return {}


class ArchitecturalGate:
    """Evaluates agent_patch vs creator_patch with repo snapshot and language context."""

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
    ) -> GateResult:
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
        api_pass = thresholds.allow_api_breaks or (api_surface_score >= thresholds.api_surface_min)
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

        return GateResult(architectural=architectural, failures=failures, raw_log=raw_log)


def evaluate_gate(
    agent_patch: str,
    creator_patch: str,
    task_config: str | Path | Mapping[str, Any] | None,
    repo_snapshot: RepoSnapshot,
    language: str = "python",
    repo_root: Path | None = None,
    scope_policy: Any | None = None,
    ruff_config: Path | None = None,
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
    )


def result_to_json(gr: GateResult) -> str:
    """Serialize to exact `architectural` output schema."""
    return json.dumps({"architectural": gr.architectural}, indent=2)
