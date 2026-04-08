"""Import-line diff between before/after snapshots (supporting data for architectural output)."""

from __future__ import annotations

import re

from architectural_gate.models import RepoSnapshot

_GO_IMPORT = re.compile(r"^\s*(import\s+[\w./\"]+|import\s*\()")


def _lines_for_path(content: str | None, language: str) -> set[str]:
    if not content:
        return set()
    out: set[str] = set()
    lang = (language or "").lower()
    for line in content.splitlines():
        s = line.strip()
        if not s or s.startswith("//") or s.startswith("#"):
            continue
        if lang in ("python", "py"):
            if s.startswith("import ") or s.startswith("from "):
                out.add(s)
        elif lang in (
            "javascript",
            "js",
            "typescript",
            "ts",
            "tsx",
            "jsx",
            "node",
            "nodejs",
        ):
            if (
                s.startswith("import ")
                or s.startswith("export ")
                or s.startswith("import{")
                or "import(" in s
            ):
                out.add(s)
        elif lang in ("go", "golang"):
            if _GO_IMPORT.match(s) or s.startswith("import "):
                out.add(s)
        elif lang in ("rust", "rs"):
            if s.startswith("use "):
                out.add(s)
    return out


def compute_import_diff(
    snapshot: RepoSnapshot,
    language: str,
    touched_files: set[str] | None = None,
) -> list[str]:
    """Sorted unified-style strings: '- ...' removed, '+ ...' added.

    If `touched_files` is provided (including an empty set), only those paths are scanned;
    an empty set yields an empty import diff. This prevents listing imports for files
    outside the agent patch when the gate passes a file list.
    When `touched_files` is None and only inline dicts are provided, all known
    paths are scanned (original behaviour). When roots are provided without
    `touched_files`, scanning is restricted to paths that exist in BOTH before
    and after roots (i.e. changed files), falling back to full-repo scan only
    when no root-based filtering is possible.
    """
    if touched_files is not None:
        paths: set[str] = set(touched_files)
        if not paths:
            return []
    else:
        paths = set()
        if snapshot.before_files:
            paths |= set(snapshot.before_files.keys())
        if snapshot.after_files:
            paths |= set(snapshot.after_files.keys())
        # When roots are given but no touched_files hint, avoid whole-repo scan:
        # only include files explicitly known from inline dicts.
        if not paths and (snapshot.before_root or snapshot.after_root):
            # Fallback: scan after_root (whole repo) — legacy behaviour
            root = snapshot.after_root or snapshot.before_root
            if root and root.is_dir():
                for p in root.rglob("*"):
                    if p.is_file():
                        try:
                            paths.add(p.relative_to(root).as_posix())
                        except ValueError:
                            continue

    diff: list[str] = []
    for rel in sorted(paths):
        b = snapshot.resolve_file(rel, "before")
        a = snapshot.resolve_file(rel, "after")
        if b is None and a is None:
            continue
        bs = _lines_for_path(b, language)
        aset = _lines_for_path(a, language)
        for x in sorted(bs - aset):
            diff.append(f"- {rel}: {x}")
        for x in sorted(aset - bs):
            diff.append(f"+ {rel}: {x}")
    return diff
