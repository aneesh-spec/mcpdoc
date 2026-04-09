"""Architectural Gate: evaluate agent patches against creator reference patches.

Two-phase evaluation model:
    EvaluationMode.PHASE_A — Human PR validation (api, deps, dead_code only).
    EvaluationMode.PHASE_B — Agent patch evaluation (all five checks).

Quick start:
    from architectural_gate import ArchitecturalGate, EvaluationMode
    from architectural_gate.models import RepoSnapshot

    gate = ArchitecturalGate()

    # Phase A: validate a human PR
    result = gate.evaluate(
        agent_patch=pr_diff,
        creator_patch="",
        task_config=None,
        repo_snapshot=RepoSnapshot(before_root=base_tree, after_root=pr_tree),
        mode=EvaluationMode.PHASE_A,
    )

    # Phase B: evaluate an agent patch against the creator reference
    result = gate.evaluate(
        agent_patch=agent_diff,
        creator_patch=creator_diff,
        task_config=task_yaml_path,
        repo_snapshot=RepoSnapshot(before_root=base_tree, after_root=agent_tree),
        mode=EvaluationMode.PHASE_B,
    )
"""

from architectural_gate.gate import ArchitecturalGate, evaluate_gate, result_to_json
from architectural_gate.models import EvaluationMode

__all__ = [
    "ArchitecturalGate",
    "evaluate_gate",
    "result_to_json",
    "EvaluationMode",
    "__version__",
]

__version__ = "1.0.0"
