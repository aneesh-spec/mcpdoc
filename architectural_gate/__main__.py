"""Entry point for `python -m architectural_gate` (CI smoke / quick check)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from architectural_gate.gate import ArchitecturalGate, result_to_json
from architectural_gate.models import RepoSnapshot


def main() -> int:
    """
    Default: run a minimal in-process smoke (empty patches → gate_pass True).

    Optional env (for real PR-style runs from CI):
      ARCH_GATE_AGENT_DIFF   path to agent unified diff file
      ARCH_GATE_CREATOR_DIFF path to creator unified diff file
      ARCH_GATE_REPO_ROOT    path to repo root for snapshot (after state)
      ARCH_GATE_TASK_CONFIG  path to task YAML (optional)
    """
    agent = os.environ.get("ARCH_GATE_AGENT_DIFF")
    creator = os.environ.get("ARCH_GATE_CREATOR_DIFF")
    root = os.environ.get("ARCH_GATE_REPO_ROOT")
    cfg = os.environ.get("ARCH_GATE_TASK_CONFIG")

    if agent and creator:
        agent_patch = Path(agent).read_text(encoding="utf-8")
        creator_patch = Path(creator).read_text(encoding="utf-8")
        task_config = Path(cfg) if cfg else {}
        repo_root = Path(root).resolve() if root else None
        snap = RepoSnapshot(after_root=repo_root) if repo_root and repo_root.is_dir() else RepoSnapshot()
        r = ArchitecturalGate().evaluate(
            agent_patch,
            creator_patch,
            task_config,
            snap,
            language="python",
            repo_root=repo_root,
        )
    else:
        r = ArchitecturalGate().evaluate("", "", {}, RepoSnapshot(), "python")

    print(result_to_json(r))
    if os.environ.get("ARCH_GATE_JSON_LOG"):
        Path(os.environ["ARCH_GATE_JSON_LOG"]).write_text(
            json.dumps({"raw_log": r.raw_log}, indent=2, default=str),
            encoding="utf-8",
        )
    return 0 if r.architectural.get("gate_pass") else 1


if __name__ == "__main__":
    sys.exit(main())
