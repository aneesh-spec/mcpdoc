"""Go public API: exported identifiers (capitalized names at package level)."""

from __future__ import annotations

import re
from typing import Any

from architectural_gate.plugins.base import LanguageAPIAdapter

# Package-level exported func: func Name( or func (recv) Name(
_RE_FUNC_EXPORT = re.compile(
    r"^func\s+(?:\([^)]*\)\s+)?([A-Z][a-zA-Z0-9_]*)",
    re.MULTILINE,
)
_RE_TYPE_EXPORT = re.compile(r"^type\s+([A-Z][a-zA-Z0-9_]*)", re.MULTILINE)
# const/var single: const Foo = or var Bar
_RE_CONST = re.compile(r"^const\s+([A-Z][a-zA-Z0-9_]*)", re.MULTILINE)
_RE_VAR = re.compile(r"^var\s+([A-Z][a-zA-Z0-9_]*)", re.MULTILINE)


class GoAPIAdapter(LanguageAPIAdapter):
    name = "go"

    def relevant_paths(self, snapshot_paths: set[str]) -> set[str]:
        return {p for p in snapshot_paths if p.endswith(".go")}

    def extract_public_surface(self, rel_path: str, content: str | None) -> dict[str, Any]:
        out: dict[str, Any] = {"path": rel_path, "exports": []}
        if not content:
            return out
        names: set[str] = set()
        for pattern in (_RE_FUNC_EXPORT, _RE_TYPE_EXPORT, _RE_CONST, _RE_VAR):
            for m in pattern.finditer(content):
                names.add(m.group(1))
        out["exports"] = sorted(names)
        return out

    def breaking_changes(self, before: dict, after: dict) -> list[str]:
        breaks: list[str] = []
        path = before.get("path") or after.get("path") or "?"
        b = set(before.get("exports") or [])
        a = set(after.get("exports") or [])
        for name in b:
            if name not in a:
                breaks.append(f"{path}: removed exported Go symbol `{name}`")
        return breaks
