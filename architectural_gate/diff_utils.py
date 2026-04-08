"""Unified diff parsing: file paths and non-whitespace line change counts."""

from __future__ import annotations

import re
from pathlib import Path

from architectural_gate.models import is_auto_generated


_DIFF_HEADER_NEW = re.compile(r"^\+\+\+ (.+)$")
_DIFF_HEADER_OLD = re.compile(r"^--- (.+)$")


def normalize_path_from_diff_header(header: str) -> str:
    """Extract path from --- a/foo or +++ b/foo (strip a/ b/ prefixes)."""
    h = header.strip()
    for prefix in ("a/", "b/"):
        if h.startswith(prefix):
            h = h[len(prefix) :]
            break
    # Strip timestamp suffixes like \t2024-01-01 — take first token
    h = h.split("\t", 1)[0].strip()
    return h.replace("\\", "/")


def list_files_from_unified_diff(diff_text: str) -> set[str]:
    """Collect normalized file paths touched by a unified diff."""
    files: set[str] = set()
    if not diff_text or not diff_text.strip():
        return files
    for line in diff_text.splitlines():
        if line.startswith("--- "):
            m = _DIFF_HEADER_OLD.match(line)
            if m:
                p = normalize_path_from_diff_header(m.group(1))
                if p != "/dev/null":
                    files.add(p)
        elif line.startswith("+++ "):
            m = _DIFF_HEADER_NEW.match(line)
            if m:
                p = normalize_path_from_diff_header(m.group(1))
                if p != "/dev/null":
                    files.add(p)
    return files


def _is_hunk_meta(line: str) -> bool:
    return line.startswith("@@") or line.startswith("diff ")


def count_loc_changes_unified(diff_text: str) -> tuple[int, int]:
    """Return (additions, deletions) excluding diff headers and whitespace-only lines."""
    additions = 0
    deletions = 0
    if not diff_text:
        return 0, 0
    for line in diff_text.splitlines():
        if line.startswith("diff "):
            continue
        if line.startswith("+++ ") or line.startswith("--- "):
            continue
        if _is_hunk_meta(line):
            continue
        if line.startswith("+"):
            body = line[1:]
            if body.strip() == "":
                continue
            additions += 1
        elif line.startswith("-"):
            body = line[1:]
            if body.strip() == "":
                continue
            deletions += 1
    return additions, deletions


def total_changed_loc(diff_text: str) -> int:
    """Additions + deletions (non-whitespace), per spec."""
    a, d = count_loc_changes_unified(diff_text)
    return a + d


def filter_auto_generated_files(
    files: set[str], extra_patterns: tuple[str, ...] = ()
) -> set[str]:
    """Return only files that are NOT auto-generated."""
    return {f for f in files if not is_auto_generated(f, extra_patterns)}


def total_changed_loc_filtered(
    diff_text: str, extra_patterns: tuple[str, ...] = ()
) -> tuple[int, list[str]]:
    """LOC count excluding auto-generated files. Returns (loc, list_of_excluded_files)."""
    if not diff_text:
        return 0, []

    # Parse per-file hunks so we can skip auto-generated ones
    excluded: list[str] = []
    additions = 0
    deletions = 0
    current_file: str | None = None
    skip_current = False

    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            m = _DIFF_HEADER_NEW.match(line)
            if m:
                current_file = normalize_path_from_diff_header(m.group(1))
                skip_current = current_file != "/dev/null" and is_auto_generated(
                    current_file, extra_patterns
                )
                if skip_current and current_file not in excluded:
                    excluded.append(current_file)
            continue
        if line.startswith("--- ") or line.startswith("diff ") or _is_hunk_meta(line):
            continue
        if skip_current:
            continue
        if line.startswith("+"):
            body = line[1:]
            if body.strip():
                additions += 1
        elif line.startswith("-"):
            body = line[1:]
            if body.strip():
                deletions += 1

    return additions + deletions, excluded


def collect_paths_under_repo(repo_root: Path | None, known_dirs: set[str]) -> set[str]:
    """All relative POSIX paths for files under directories that appear in known_dirs."""
    if repo_root is None or not repo_root.is_dir():
        return set()
    out: set[str] = set()
    for d in known_dirs:
        dir_path = repo_root.joinpath(*d.split("/")) if d != "." else repo_root
        if not dir_path.is_dir():
            continue
        for p in dir_path.rglob("*"):
            if p.is_file():
                try:
                    rel = p.relative_to(repo_root).as_posix()
                except ValueError:
                    continue
                out.add(rel)
    return out
