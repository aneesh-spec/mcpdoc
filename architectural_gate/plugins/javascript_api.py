"""JavaScript/TypeScript lightweight export surface (heuristic, deterministic)."""

from __future__ import annotations

import re
from typing import Any

from architectural_gate.plugins.base import LanguageAPIAdapter

_RE_EXPORT_NAMED = re.compile(r"export\s+(?:async\s+)?function\s+(\w+)")
_RE_EXPORT_CLASS = re.compile(r"export\s+class\s+(\w+)")
_RE_EXPORT_CONST = re.compile(r"export\s+const\s+(\w+)")
_RE_EXPORT_BRACE = re.compile(r"export\s*\{([^}]+)\}")


def _parse_export_list(chunk: str) -> list[str]:
    names: list[str] = []
    for part in chunk.split(","):
        part = part.strip()
        if not part:
            continue
        # `foo as bar` -> take exported name `bar`? use `bar`
        if " as " in part:
            names.append(part.split(" as ")[-1].strip())
        else:
            names.append(part.split()[0].strip())
    return names


class JavaScriptAPIAdapter(LanguageAPIAdapter):
    name = "javascript"

    def relevant_paths(self, snapshot_paths: set[str]) -> set[str]:
        return {
            p
            for p in snapshot_paths
            if p.endswith(
                (".js", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".jsx")
            )
        }

    def extract_public_surface(
        self, rel_path: str, content: str | None
    ) -> dict[str, Any]:
        out: dict[str, Any] = {"path": rel_path, "exports": []}
        if not content:
            return out
        exports: set[str] = set()
        for m in _RE_EXPORT_NAMED.finditer(content):
            exports.add(m.group(1))
        for m in _RE_EXPORT_CLASS.finditer(content):
            exports.add(m.group(1))
        for m in _RE_EXPORT_CONST.finditer(content):
            exports.add(m.group(1))
        for m in _RE_EXPORT_BRACE.finditer(content):
            exports.update(_parse_export_list(m.group(1)))
        # default export: unstable name — record marker
        if re.search(r"export\s+default\s+", content):
            exports.add("__default__")
        out["exports"] = sorted(exports)
        return out

    def breaking_changes(self, before: dict, after: dict) -> list[str]:
        breaks: list[str] = []
        path = before.get("path") or after.get("path") or "?"
        b = set(before.get("exports") or [])
        a = set(after.get("exports") or [])
        for name in b:
            if name not in a:
                breaks.append(f"{path}: removed export `{name}`")
        return breaks
