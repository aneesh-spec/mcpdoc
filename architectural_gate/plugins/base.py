"""TODO-3: Abstract API surface comparison for multi-language support."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from architectural_gate.models import RepoSnapshot

logger = logging.getLogger(__name__)


class LanguageAPIAdapter(ABC):
    """Pluggable public API extraction and diff for a language."""

    name: str = "base"

    @abstractmethod
    def relevant_paths(self, snapshot_paths: set[str]) -> set[str]:
        """Filter paths this adapter handles (e.g. *.py)."""

    @abstractmethod
    def extract_public_surface(self, rel_path: str, content: str | None) -> dict:
        """
        Return a JSON-serializable description of public API, e.g.
        {"functions": {"foo": {"args": ["a", "b"]}}, "classes": {...}}
        """

    @abstractmethod
    def breaking_changes(self, before: dict, after: dict) -> list[str]:
        """Human-readable list of breaking changes; empty if compatible."""

    def interface_count(self, surface: dict) -> int:
        """Count public interface elements for api_surface_score denominator."""
        if surface.get("error"):
            return 0
        ex = surface.get("exports")
        if isinstance(ex, list):
            return len(ex)
        return 0

    def analyze_repo_pair(
        self,
        paths: set[str],
        snapshot: RepoSnapshot,
    ) -> list[str]:
        """Compare before/after for all relevant paths."""
        all_breaks: list[str] = []
        for rel in sorted(self.relevant_paths(paths)):
            b = snapshot.resolve_file(rel, "before")
            a = snapshot.resolve_file(rel, "after")
            if b is None and a is None:
                continue
            surf_b = self.extract_public_surface(rel, b)
            surf_a = self.extract_public_surface(rel, a)
            all_breaks.extend(self.breaking_changes(surf_b, surf_a))
        return all_breaks


def get_api_adapter(language: str) -> LanguageAPIAdapter | None:
    """Resolve adapter by primary language id (see README: Python, TS/JS, Go, Rust)."""
    from architectural_gate.plugins.go_api import GoAPIAdapter
    from architectural_gate.plugins.javascript_api import JavaScriptAPIAdapter
    from architectural_gate.plugins.python_api import PythonAPIAdapter
    from architectural_gate.plugins.rust_api import RustAPIAdapter

    key = (language or "").strip().lower()
    if key in ("python", "py"):
        return PythonAPIAdapter()
    if key in (
        "javascript",
        "js",
        "typescript",
        "ts",
        "tsx",
        "jsx",
        "node",
        "nodejs",
    ):
        return JavaScriptAPIAdapter()
    if key in ("go", "golang"):
        return GoAPIAdapter()
    if key in ("rust", "rs"):
        return RustAPIAdapter()
    logger.warning(
        "No API adapter for language=%r; API check skipped (neutral).", language
    )
    return None
