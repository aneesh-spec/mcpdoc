"""Entry point for `python -m architectural_gate` (CI smoke / quick check)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from architectural_gate.gate import ArchitecturalGate, result_to_json
from architectural_gate.models import RepoSnapshot


def _parse_repo_roots() -> tuple[Path | None, Path | None]:
    """Initial (baseline) and changed trees — drives API / deps / dead-code before vs after."""
    initial = os.environ.get("ARCH_GATE_REPO_INITIAL") or os.environ.get("ARCH_GATE_REPO_BEFORE")
    changed = os.environ.get("ARCH_GATE_REPO_CHANGED") or os.environ.get("ARCH_GATE_REPO_ROOT")
    before_p = Path(initial).resolve() if initial else None
    after_p = Path(changed).resolve() if changed else None
    if before_p and not before_p.is_dir():
        before_p = None
    if after_p and not after_p.is_dir():
        after_p = None
    return before_p, after_p


def _repo_snapshot(before_p: Path | None, after_p: Path | None) -> RepoSnapshot:
    if before_p and after_p:
        return RepoSnapshot(before_root=before_p, after_root=after_p)
    if after_p:
        return RepoSnapshot(after_root=after_p)
    return RepoSnapshot()


def _unified_diff_from_dir_trees(before: Path, after: Path) -> str:
    """Unified diff between two directory trees (uses ``git diff --no-index``)."""
    proc = subprocess.run(
        ["git", "diff", "--no-index", "--minimal", str(before.resolve()), str(after.resolve())],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    # Exit 1 = differences; 0 = identical. stderr may contain warnings.
    return proc.stdout or ""


def main() -> int:
    """
    Compare **initial repo state** to **changed** state, evaluate thresholds, print JSON.

    Primary CI inputs (both directories = full before/after snapshot for API, deps, dead code):

      ARCH_GATE_REPO_INITIAL   baseline tree (e.g. merge-base / base branch checkout)
      ARCH_GATE_REPO_CHANGED   tree with edits (e.g. PR head); alias legacy: ARCH_GATE_REPO_ROOT

    If both are set, the change patch is built with ``git diff --no-index`` unless you override
    with a file below.

    Optional unified diff file (overrides auto diff when set):

      ARCH_GATE_CHANGE_DIFF    preferred
      ARCH_GATE_AGENT_DIFF     same role if CHANGE_DIFF unset

    Optional reference patch for creator-relative scope/blast (benchmarks):

      ARCH_GATE_CREATOR_DIFF   if omitted → self-reference mode for scope/blast

    Log file (same JSON as stdout — only ``{"architectural": ...}``):

      ARCH_GATE_JSON_LOG       path to write that JSON

      ARCH_GATE_TASK_CONFIG    task YAML path (optional)
    """
    before_p, after_p = _parse_repo_roots()
    change_path = os.environ.get("ARCH_GATE_CHANGE_DIFF") or os.environ.get("ARCH_GATE_AGENT_DIFF")
    creator_path = os.environ.get("ARCH_GATE_CREATOR_DIFF")
    cfg = os.environ.get("ARCH_GATE_TASK_CONFIG")
    log_path = os.environ.get("ARCH_GATE_JSON_LOG")

    if change_path:
        agent_patch = Path(change_path).read_text(encoding="utf-8")
    elif before_p and after_p:
        agent_patch = _unified_diff_from_dir_trees(before_p, after_p)
    else:
        agent_patch = ""

    has_work = bool(change_path) or (before_p is not None and after_p is not None)

    self_ref = not creator_path
    creator_patch = Path(creator_path).read_text(encoding="utf-8") if creator_path else ""
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
            self_reference=self_ref,
        )
    else:
        r = ArchitecturalGate().evaluate("", "", {}, RepoSnapshot(), "python")

    payload = result_to_json(r)
    print(payload)
    if log_path:
        Path(log_path).write_text(payload, encoding="utf-8")
    return 0 if r.architectural.get("gate_pass") else 1


def cli_entry_point() -> None:
    """Console script entry point for `architecture-gate` (see pyproject [project.scripts])."""
    sys.exit(main())


if __name__ == "__main__":
    cli_entry_point()
