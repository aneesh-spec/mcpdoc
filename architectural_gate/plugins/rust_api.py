"""Rust public API: `pub` items (heuristic, deterministic)."""

from __future__ import annotations

import re
from typing import Any

from architectural_gate.plugins.base import LanguageAPIAdapter

# pub fn name / pub async fn name / pub struct Name / pub enum / pub trait / pub type / pub const
_RE_PUB_FN = re.compile(
    r"^pub(?:\([^)]*\))?\s+(?:async\s+)?fn\s+([a-zA-Z_][a-zA-Z0-9_]*)",
    re.MULTILINE,
)
_RE_PUB_TYPE = re.compile(
    r"^pub\s+(?:struct|enum|trait|type)\s+([A-Z][a-zA-Z0-9_]*)",
    re.MULTILINE,
)
_RE_PUB_CONST = re.compile(r"^pub\s+const\s+([A-Z][a-zA-Z0-9_]*)", re.MULTILINE)
_RE_PUB_USE = re.compile(r"^pub\s+use\s+([^;]+);", re.MULTILINE)


def _names_from_pub_use(line: str) -> list[str]:
    """Extract last segment of `path::Foo` or `Foo as Bar` -> Bar."""
    out: list[str] = []
    # split by comma for grouped use
    for part in line.split(","):
        part = part.strip()
        if not part:
            continue
        if " as " in part:
            out.append(part.split(" as ")[-1].strip().split()[0])
        else:
            seg = part.split("::")[-1].strip()
            seg = seg.split()[0] if seg else ""
            if seg and (seg[0].isupper() or seg.startswith("_")):
                out.append(seg)
            elif seg:
                out.append(seg)
    return out


class RustAPIAdapter(LanguageAPIAdapter):
    name = "rust"

    def relevant_paths(self, snapshot_paths: set[str]) -> set[str]:
        return {p for p in snapshot_paths if p.endswith(".rs")}

    def extract_public_surface(self, rel_path: str, content: str | None) -> dict[str, Any]:
        out: dict[str, Any] = {"path": rel_path, "exports": []}
        if not content:
            return out
        names: set[str] = set()
        for pattern in (_RE_PUB_FN, _RE_PUB_TYPE, _RE_PUB_CONST):
            for m in pattern.finditer(content):
                names.add(m.group(1))
        for m in _RE_PUB_USE.finditer(content):
            names.update(_names_from_pub_use(m.group(1)))
        out["exports"] = sorted(names)
        return out

    def breaking_changes(self, before: dict, after: dict) -> list[str]:
        breaks: list[str] = []
        path = before.get("path") or after.get("path") or "?"
        b = set(before.get("exports") or [])
        a = set(after.get("exports") or [])
        for name in b:
            if name not in a:
                breaks.append(f"{path}: removed public Rust item `{name}`")
        return breaks
