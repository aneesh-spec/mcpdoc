"""Shared dataclasses and types for the Architectural Gate."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

# Files that are auto-generated and should be excluded from scope/blast scoring by default.
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
    """Return True if path matches any auto-generated file pattern."""
    name = path.rsplit("/", 1)[-1]  # basename
    for pat in AUTO_GENERATED_PATTERNS + extra_patterns:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(path, pat):
            return True
    return False


@dataclass(frozen=True)
class RepoSnapshot:
    """Before/after repository state for API, dependency, and dead-code checks.

    Provide either filesystem roots or inline path->content maps (relative POSIX paths).
    """

    before_root: Path | None = None
    after_root: Path | None = None
    before_files: Mapping[str, str] | None = None
    after_files: Mapping[str, str] | None = None

    def resolve_file(self, rel_path: str, phase: str) -> str | None:
        """phase is 'before' or 'after'."""
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
    """Thresholds applied (from `architectural.thresholds` or legacy `architectural_gate`)."""

    scope_min: float = 0.7
    blast_ratio_min: float = 0.5
    api_surface_min: float = 1.0
    max_new_dependencies: int = 0
    max_dead_code: int = 2
    allow_new_dependencies: bool = False
    allow_api_breaks: bool = False
    exclude_patterns: tuple[str, ...] = field(
        default_factory=tuple
    )  # extra glob patterns to exclude from scope/blast

    @classmethod
    def from_task_config(
        cls, raw: Mapping[str, Any] | None
    ) -> tuple["GateThresholds", dict[str, Any]]:
        """Returns (thresholds, override_record for logging)."""
        defaults = cls()
        if not raw:
            return defaults, {}

        arch = raw.get("architectural") or {}
        if not isinstance(arch, dict):
            arch = {}
        th = arch.get("thresholds") or {}
        if not isinstance(th, dict):
            th = {}

        leg = raw.get("architectural_gate") or {}
        if not isinstance(leg, dict):
            leg = {}

        overrides: dict[str, Any] = {}

        try:
            scope_min = (
                float(th["scope_min"])
                if "scope_min" in th
                else float(leg.get("scope_threshold", defaults.scope_min))
            )
        except (TypeError, ValueError):
            scope_min = defaults.scope_min
        if "scope_min" in th:
            overrides["scope_min"] = scope_min
        elif "scope_threshold" in leg:
            overrides["scope_threshold"] = leg["scope_threshold"]

        try:
            if "blast_ratio_min" in th:
                blast_ratio_min = float(th["blast_ratio_min"])
                overrides["blast_ratio_min"] = blast_ratio_min
            elif "blast_threshold" in leg:
                bt = float(leg["blast_threshold"])
                blast_ratio_min = (1.0 / bt) if bt > 0 else defaults.blast_ratio_min
                overrides["blast_threshold_legacy"] = bt
            else:
                blast_ratio_min = defaults.blast_ratio_min
        except (TypeError, ValueError):
            blast_ratio_min = defaults.blast_ratio_min

        try:
            api_surface_min = (
                float(th["api_surface_min"])
                if "api_surface_min" in th
                else float(leg.get("api_surface_min", defaults.api_surface_min))
            )
        except (TypeError, ValueError):
            api_surface_min = defaults.api_surface_min
        if "api_surface_min" in th:
            overrides["api_surface_min"] = api_surface_min

        try:
            max_new = (
                int(th["max_new_dependencies"])
                if "max_new_dependencies" in th
                else int(leg.get("max_new_dependencies", defaults.max_new_dependencies))
            )
        except (TypeError, ValueError):
            max_new = defaults.max_new_dependencies
        if "max_new_dependencies" in th:
            overrides["max_new_dependencies"] = max_new

        try:
            max_dead = (
                int(th["max_dead_code"])
                if "max_dead_code" in th
                else int(leg.get("dead_code_threshold", defaults.max_dead_code))
            )
        except (TypeError, ValueError):
            max_dead = defaults.max_dead_code
        if "max_dead_code" in th:
            overrides["max_dead_code"] = max_dead

        allow_new = (
            bool(arch.get("allow_new_dependencies"))
            if "allow_new_dependencies" in arch
            else bool(
                leg.get("allow_new_dependencies", defaults.allow_new_dependencies)
            )
        )
        allow_api = (
            bool(arch.get("allow_api_breaks"))
            if "allow_api_breaks" in arch
            else bool(leg.get("allow_api_breaks", defaults.allow_api_breaks))
        )

        raw_excl = arch.get("exclude_patterns") or leg.get("exclude_patterns") or []
        exclude_patterns: tuple[str, ...] = (
            tuple(raw_excl) if isinstance(raw_excl, list) else ()
        )
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
    """Primary payload is `architectural` (exact evaluation schema)."""

    architectural: dict[str, Any]
    failures: list[str] = field(default_factory=list)
    raw_log: dict[str, Any] = field(default_factory=dict)

    @property
    def result(self) -> str:
        return "PASS" if self.architectural.get("gate_pass") else "FAIL"
