"""Entry point for ``python -m architectural_gate``.

Detects evaluation mode from environment variables and runs the gate.

Two-phase usage
---------------

Phase A — Human PR validation
    Set when ARCH_GATE_CREATOR_DIFF is NOT provided.
    scope_score and blast_ratio are always 1.0 (N/A).
    Only api_surface_score, new_dependencies_count, dead_code_count are enforced.

    Required env vars:
        ARCH_GATE_REPO_INITIAL   Baseline repo tree (base branch checkout)
        ARCH_GATE_REPO_CHANGED   Changed repo tree (PR head checkout)

    Optional:
        ARCH_GATE_CHANGE_DIFF    Unified diff file (auto-built from INITIAL vs CHANGED if omitted)
        ARCH_GATE_TASK_CONFIG    Task YAML path (thresholds; defaults apply if omitted)
        ARCH_GATE_JSON_LOG       Path to write JSON output (also printed to stdout)

    Example (GitHub Actions Phase A job):
        ARCH_GATE_REPO_INITIAL=/tmp/base ARCH_GATE_REPO_CHANGED=/tmp/pr python -m architectural_gate

Phase B — Agent patch evaluation
    Set when ARCH_GATE_CREATOR_DIFF IS provided.
    All five checks are enforced: scope, blast, api, new_deps, dead_code.

    Required env vars:
        ARCH_GATE_CREATOR_DIFF   Path to creator unified diff (frozen from Phase A)
        ARCH_GATE_CHANGE_DIFF    Path to agent unified diff
        ARCH_GATE_REPO_INITIAL   Baseline repo tree
        ARCH_GATE_REPO_CHANGED   Agent's changed repo tree

    Optional:
        ARCH_GATE_TASK_CONFIG    Task YAML path
        ARCH_GATE_JSON_LOG       Path to write JSON output

    Example:
        ARCH_GATE_CREATOR_DIFF=creator.diff \\
        ARCH_GATE_CHANGE_DIFF=agent.diff \\
        ARCH_GATE_REPO_INITIAL=/tmp/base \\
        ARCH_GATE_REPO_CHANGED=/tmp/agent \\
        python -m architectural_gate

Exit codes
----------
    0 — gate_pass is True
    1 — gate_pass is False (one or more checks failed)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from architectural_gate.gate import ArchitecturalGate, result_to_json
from architectural_gate.models import EvaluationMode, RepoSnapshot


def _parse_repo_roots() -> tuple[Path | None, Path | None]:
    """Read ARCH_GATE_REPO_INITIAL and ARCH_GATE_REPO_CHANGED from env.

    Returns (before_path, after_path). Either may be None if the env var is
    unset or points to a non-existent directory.
    """
    initial = os.environ.get("ARCH_GATE_REPO_INITIAL") or os.environ.get(
        "ARCH_GATE_REPO_BEFORE"
    )
    changed = os.environ.get("ARCH_GATE_REPO_CHANGED") or os.environ.get(
        "ARCH_GATE_REPO_ROOT"
    )
    before_p = Path(initial).resolve() if initial else None
    after_p = Path(changed).resolve() if changed else None
    if before_p and not before_p.is_dir():
        before_p = None
    if after_p and not after_p.is_dir():
        after_p = None
    return before_p, after_p


def _repo_snapshot(before_p: Path | None, after_p: Path | None) -> RepoSnapshot:
    """Build a RepoSnapshot from optional before/after directory paths."""
    if before_p and after_p:
        return RepoSnapshot(before_root=before_p, after_root=after_p)
    if after_p:
        return RepoSnapshot(after_root=after_p)
    return RepoSnapshot()


def _unified_diff_from_dir_trees(before: Path, after: Path) -> str:
    """Build a unified diff between two directory trees using ``git diff --no-index``.

    Exit code 1 from git diff means differences were found (not an error).
    Returns the diff text, or "" if git is unavailable or trees are identical.
    """
    proc = subprocess.run(
        [
            "git",
            "diff",
            "--no-index",
            "--minimal",
            str(before.resolve()),
            str(after.resolve()),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout or ""


def main() -> int:
    """Detect evaluation mode and run the architectural gate. Returns exit code."""
    before_p, after_p = _parse_repo_roots()

    change_path = os.environ.get("ARCH_GATE_CHANGE_DIFF") or os.environ.get(
        "ARCH_GATE_AGENT_DIFF"
    )
    creator_path = os.environ.get("ARCH_GATE_CREATOR_DIFF")
    cfg = os.environ.get("ARCH_GATE_TASK_CONFIG")
    log_path = os.environ.get("ARCH_GATE_JSON_LOG")

    # Build agent patch from file or by diffing the two repo trees.
    if change_path:
        agent_patch = Path(change_path).read_text(encoding="utf-8")
    elif before_p and after_p:
        agent_patch = _unified_diff_from_dir_trees(before_p, after_p)
    else:
        agent_patch = ""

    has_work = bool(change_path) or (before_p is not None and after_p is not None)

    # Detect evaluation mode from presence of creator diff.
    #   No ARCH_GATE_CREATOR_DIFF → Phase A (human PR, no agent reference).
    #   ARCH_GATE_CREATOR_DIFF set → Phase B (agent patch vs creator reference).
    if creator_path:
        mode = EvaluationMode.PHASE_B
        creator_patch = Path(creator_path).read_text(encoding="utf-8")
    else:
        mode = EvaluationMode.PHASE_A
        creator_patch = ""  # overridden inside evaluate() for Phase A

    task_config = Path(cfg) if cfg else {}
    repo_root = after_p
    snap = _repo_snapshot(before_p, after_p)

    if has_work:
        r = ArchitecturalGate().evaluate(
            agent_patch,
            creator_patch,
            task_config,
            snap,
            language="python",
            repo_root=repo_root,
            mode=mode,
        )
    else:
        # No diff and no repo trees — emit a neutral Phase A result.
        r = ArchitecturalGate().evaluate(
            "", "", {}, RepoSnapshot(), "python", mode=EvaluationMode.PHASE_A
        )

    payload = result_to_json(r)
    print(payload)
    if log_path:
        Path(log_path).write_text(payload, encoding="utf-8")
    return 0 if r.architectural.get("gate_pass") else 1


def cli_entry_point() -> None:
    """Console script entry point for ``architecture-gate`` (see pyproject [project.scripts])."""
    sys.exit(main())


if __name__ == "__main__":
    cli_entry_point()
