"""Shared dataclasses and types for the Architectural Gate.

Two-phase evaluation model
--------------------------
Phase A — Human PR validation
    The human's PR diff is the only patch available. scope_score and blast_ratio
    are always 1.0 (N/A) because there is no separate creator reference to compare
    against. Only api_surface_score, new_dependencies_count, and dead_code_count
    are enforced.

Phase B — Agent patch evaluation
    The agent's diff is evaluated against the frozen creator patch from Phase A.
    All five metrics are enforced: scope_score, blast_ratio, api_surface_score,
    new_dependencies_count, dead_code_count.
"""

from __future__ import annotations

import enum
import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


class EvaluationMode(enum.Enum):
    """Which phase of the two-phase evaluation pipeline is running.

    PHASE_A:
        Human PR is being validated. The PR diff IS the creator patch — there is
        no agent yet and no separate reference patch. scope_score and blast_ratio
        are always 1.0 (N/A); only api_surface_score, new_dependencies_count, and
        dead_code_count are meaningful.

        Example: Annotator opens a PR with a bug fix + F2P tests. The gate checks
        that no public API was broken, no new dependency was added, and no dead
        code was introduced — but does not check scope or blast (nothing to compare
        against).

    PHASE_B:
        Agent output is being evaluated against the frozen creator patch from Phase A.
        All five metrics are enforced.

        Example: An AI agent attempts the same bug fix task. Its patch is compared
        against the creator's reference patch: did it stay in scope? Did it write
        proportionally as much code? Did it break any API?
    """

    PHASE_A = "phase_a"
    PHASE_B = "phase_b"


# Files that are auto-generated and should be excluded from scope/blast scoring.
AUTO_GENERATED_PATTERNS: tuple[str, ...] = (
    "*.lock",  # uv.lock, package-lock.json, Cargo.lock, yarn.lock, Gemfile.lock
    "uv.lock",
    "package-lock.json",
    "yarn.lock",
    "Cargo.lock",
    "Gemfile.lock",
    "poetry.lock",
    "composer.lock",
    "Pipfile.lock",
    "*.min.js",
    "*.min.css",
    "*.pb.go",  # protobuf generated
    "*.pb.py",
    "*_pb2.py",
    "*.generated.*",
    "__pycache__/*",
    "*.pyc",
    "dist/*",
    "build/*",
    ".gradle/*",
)


def is_auto_generated(path: str, extra_patterns: tuple[str, ...] = ()) -> bool:
    """Return True if path matches any auto-generated file pattern.

    Args:
        path: Relative POSIX file path to check.
        extra_patterns: Additional glob patterns beyond AUTO_GENERATED_PATTERNS.
    """
    name = path.rsplit("/", 1)[-1]  # basename
    for pat in AUTO_GENERATED_PATTERNS + extra_patterns:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(path, pat):
            return True
    return False


@dataclass(frozen=True)
class RepoSnapshot:
    """Before/after repository state for API, dependency, and dead-code checks.

    Provide either filesystem roots or inline path->content maps (relative POSIX paths).
    Both before and after are needed for meaningful api_surface and new_dependencies
    checks. If only after_root is provided, before/after comparisons treat the before
    state as empty.

    Attributes:
        before_root: Filesystem path to the baseline repo tree (e.g. base branch checkout).
        after_root:  Filesystem path to the changed repo tree (e.g. PR head checkout).
        before_files: Inline map of relative path → file content for the baseline state.
        after_files:  Inline map of relative path → file content for the changed state.
    """

    before_root: Path | None = None
    after_root: Path | None = None
    before_files: Mapping[str, str] | None = None
    after_files: Mapping[str, str] | None = None

    def resolve_file(self, rel_path: str, phase: str) -> str | None:
        """Return file content for a relative path at the given phase.

        Args:
            rel_path: Relative POSIX path (e.g. "src/foo.py").
            phase: "before" or "after".

        Returns:
            File content as a string, or None if not found.
        """
        if phase == "before":
            if self.before_files is not None:
                return self.before_files.get(rel_path)
            if self.before_root is not None:
                p = self.before_root / rel_path
                if p.is_file():
                    return p.read_text(encoding="utf-8", errors="replace")
        else:
            if self.after_files is not None:
                return self.after_files.get(rel_path)
            if self.after_root is not None:
                p = self.after_root / rel_path
                if p.is_file():
                    return p.read_text(encoding="utf-8", errors="replace")
        return None


@dataclass
class GateThresholds:
    """Thresholds applied during gate evaluation, loaded from task YAML config.

    All thresholds are conjunctive — failing any one fails the gate.

    Config format (architectural.thresholds in task YAML):

        architectural:
          thresholds:
            scope_min: 0.7            # Phase B only — default 0.7
            blast_ratio_min: 0.5      # Phase B only — default 0.5
            api_surface_min: 1.0      # Both phases  — default 1.0
            max_new_dependencies: 0   # Both phases  — default 0
            max_dead_code: 2          # Both phases  — default 2
          allow_new_dependencies: false
          allow_api_breaks: false

    Phase relevance:
        scope_min and blast_ratio_min are only meaningful in Phase B. In Phase A
        they are always 1.0 regardless of threshold value — the gate overrides them.

    Attributes:
        scope_min: Minimum scope_score to pass (Phase B only).
        blast_ratio_min: Minimum blast_ratio to pass (Phase B only).
        api_surface_min: Minimum api_surface_score to pass (both phases).
        max_new_dependencies: Maximum new external dependencies allowed (both phases).
        max_dead_code: Maximum distinct dead-code linter rule codes allowed (both phases).
        allow_new_dependencies: If True, new_dependencies check always passes.
        allow_api_breaks: If True, api_surface check always passes.
        exclude_patterns: Extra glob patterns excluded from scope/blast LOC counting.
    """

    scope_min: float = 0.7
    blast_ratio_min: float = 0.5
    api_surface_min: float = 1.0
    max_new_dependencies: int = 0
    max_dead_code: int = 2
    allow_new_dependencies: bool = False
    allow_api_breaks: bool = False
    exclude_patterns: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_task_config(
        cls, raw: Mapping[str, Any] | None
    ) -> tuple["GateThresholds", dict[str, Any]]:
        """Parse thresholds from a task config dict. Returns (thresholds, overrides_log).

        Reads from the ``architectural.thresholds`` key. Any threshold that differs
        from its default is recorded in overrides_log for audit purposes.

        Args:
            raw: Parsed task YAML as a dict, or None to use all defaults.

        Returns:
            Tuple of (GateThresholds, dict of overridden keys and their values).
        """
        defaults = cls()
        if not raw:
            return defaults, {}

        arch = raw.get("architectural") or {}
        if not isinstance(arch, dict):
            arch = {}
        th = arch.get("thresholds") or {}
        if not isinstance(th, dict):
            th = {}

        overrides: dict[str, Any] = {}

        def _float(key: str, default: float) -> float:
            try:
                return float(th[key]) if key in th else default
            except (TypeError, ValueError):
                return default

        def _int(key: str, default: int) -> int:
            try:
                return int(th[key]) if key in th else default
            except (TypeError, ValueError):
                return default

        scope_min = _float("scope_min", defaults.scope_min)
        blast_ratio_min = _float("blast_ratio_min", defaults.blast_ratio_min)
        api_surface_min = _float("api_surface_min", defaults.api_surface_min)
        max_new = _int("max_new_dependencies", defaults.max_new_dependencies)
        max_dead = _int("max_dead_code", defaults.max_dead_code)

        allow_new = bool(
            arch.get("allow_new_dependencies", defaults.allow_new_dependencies)
        )
        allow_api = bool(arch.get("allow_api_breaks", defaults.allow_api_breaks))

        raw_excl = arch.get("exclude_patterns") or []
        exclude_patterns: tuple[str, ...] = (
            tuple(raw_excl) if isinstance(raw_excl, list) else ()
        )

        # Record only values that differ from defaults
        if scope_min != defaults.scope_min:
            overrides["scope_min"] = scope_min
        if blast_ratio_min != defaults.blast_ratio_min:
            overrides["blast_ratio_min"] = blast_ratio_min
        if api_surface_min != defaults.api_surface_min:
            overrides["api_surface_min"] = api_surface_min
        if max_new != defaults.max_new_dependencies:
            overrides["max_new_dependencies"] = max_new
        if max_dead != defaults.max_dead_code:
            overrides["max_dead_code"] = max_dead
        if allow_new != defaults.allow_new_dependencies:
            overrides["allow_new_dependencies"] = allow_new
        if allow_api != defaults.allow_api_breaks:
            overrides["allow_api_breaks"] = allow_api
        if exclude_patterns:
            overrides["exclude_patterns"] = list(exclude_patterns)

        return (
            cls(
                scope_min=scope_min,
                blast_ratio_min=blast_ratio_min,
                api_surface_min=api_surface_min,
                max_new_dependencies=max_new,
                max_dead_code=max_dead,
                allow_new_dependencies=allow_new,
                allow_api_breaks=allow_api,
                exclude_patterns=exclude_patterns,
            ),
            overrides,
        )


@dataclass
class GateResult:
    """Primary output of the architectural gate.

    Attributes:
        architectural: Public JSON schema dict. Always produced regardless of pass/fail.
            Keys: scope_score, blast_ratio, api_surface_score, new_dependencies_count,
            dead_code_count, agent_loc, creator_ref_loc, files_modified,
            files_outside_scope, import_diff, thresholds, scope_pass, blast_pass,
            api_pass, dependency_pass, dead_code_pass, gate_pass.
        failures: List of failure tag strings for failing checks (e.g. "arch_scope_violation").
        raw_log: Full audit log including intermediate values per metric. Not part of the
            public schema; use for debugging and human review only.
    """

    architectural: dict[str, Any]
    failures: list[str] = field(default_factory=list)
    raw_log: dict[str, Any] = field(default_factory=dict)

    @property
    def result(self) -> str:
        """Returns 'PASS' or 'FAIL'."""
        return "PASS" if self.architectural.get("gate_pass") else "FAIL"
