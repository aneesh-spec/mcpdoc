"""Python public API surface via ast."""

from __future__ import annotations

import ast
import logging
from typing import Any

from architectural_gate.plugins.base import LanguageAPIAdapter

logger = logging.getLogger(__name__)


def _is_public(name: str) -> bool:
    return not name.startswith("_") or name in ("__init__",)


def _func_sig(node: ast.AsyncFunctionDef | ast.FunctionDef) -> dict[str, Any]:
    args: ast.arguments = node.args
    parts: list[str] = []
    for a in args.posonlyargs + args.args:
        parts.append(a.arg)
    if args.vararg:
        parts.append("*" + args.vararg.arg)
    for a in args.kwonlyargs:
        parts.append(a.arg)
    if args.kwarg:
        parts.append("**" + args.kwarg.arg)
    return {"args": parts, "lineno": getattr(node, "lineno", 0)}


def _class_public_methods(cls: ast.ClassDef) -> dict[str, Any]:
    methods: dict[str, Any] = {}
    for item in cls.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_public(item.name):
            methods[item.name] = _func_sig(item)
    return methods


class PythonAPIAdapter(LanguageAPIAdapter):
    name = "python"

    def interface_count(self, surface: dict) -> int:
        if surface.get("error"):
            return 0
        n = len(surface.get("functions") or {})
        for c in (surface.get("classes") or {}).values():
            n += len((c.get("methods") or {}) if isinstance(c, dict) else {})
        return n

    def relevant_paths(self, snapshot_paths: set[str]) -> set[str]:
        return {p for p in snapshot_paths if p.endswith(".py")}

    def extract_public_surface(self, rel_path: str, content: str | None) -> dict:
        out: dict[str, Any] = {"path": rel_path, "functions": {}, "classes": {}}
        if not content or not content.strip():
            return out
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            logger.debug("python parse error %s: %s", rel_path, e)
            return {"path": rel_path, "error": str(e), "functions": {}, "classes": {}}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_public(node.name):
                out["functions"][node.name] = _func_sig(node)
            elif isinstance(node, ast.ClassDef) and _is_public(node.name):
                out["classes"][node.name] = {
                    "methods": _class_public_methods(node),
                    "lineno": node.lineno,
                }
        return out

    def breaking_changes(self, before: dict, after: dict) -> list[str]:
        breaks: list[str] = []
        path = before.get("path") or after.get("path") or "?"
        if before.get("error") or after.get("error"):
            return [f"{path}: syntax error prevents API comparison"]
        b_funcs = before.get("functions") or {}
        a_funcs = after.get("functions") or {}
        b_cls = before.get("classes") or {}
        a_cls = after.get("classes") or {}
        for name in b_funcs:
            if name not in a_funcs:
                breaks.append(f"{path}: removed public function `{name}`")
            else:
                if (b_funcs[name].get("args") or []) != (a_funcs[name].get("args") or []):
                    breaks.append(f"{path}: signature change on `{name}`")
        for name in b_cls:
            if name not in a_cls:
                breaks.append(f"{path}: removed public class `{name}`")
            else:
                bm = (b_cls[name].get("methods") or {})
                am = (a_cls[name].get("methods") or {})
                for m in bm:
                    if m not in am:
                        breaks.append(f"{path}: removed public method `{name}.{m}`")
                    else:
                        if (bm[m].get("args") or []) != (am[m].get("args") or []):
                            breaks.append(f"{path}: signature change on `{name}.{m}`")
        return breaks
