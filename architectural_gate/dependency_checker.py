"""External dependencies: new deps introduced by agent (after vs before)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from architectural_gate.models import RepoSnapshot

logger = logging.getLogger(__name__)

# Minimal PEP 508 name extraction
_REQ_LINE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]|[A-Za-z0-9])(?:\s*[~<>=!]=|\s*==|\s*!=|\s*$|,|\s*;|\s*#)"
)


def _normalize_pkg_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def parse_requirements_txt(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _REQ_LINE.match(line)
        if m:
            names.add(_normalize_pkg_name(m.group(1)))
        else:
            # fallback: first token
            tok = line.split()[0]
            if tok:
                names.add(_normalize_pkg_name(tok.split(";", 1)[0]))
    return names


def parse_package_json(text: str) -> set[str]:
    names: set[str] = set()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return names
    for key in ("dependencies", "optionalDependencies", "peerDependencies"):
        block = data.get(key)
        if isinstance(block, dict):
            for k in block:
                names.add(_normalize_pkg_name(k))
    return names


def parse_go_mod(text: str) -> set[str]:
    """Extract required module paths (first path token per require line)."""
    names: set[str] = set()
    in_require_block = False
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        if s.startswith("require ("):
            in_require_block = True
            continue
        if in_require_block:
            if s.startswith(")"):
                in_require_block = False
                continue
            tok = s.split()[0] if s else ""
            if tok and tok != "require":
                names.add(tok.lower())
            continue
        if s.startswith("require "):
            rest = s[len("require ") :].strip()
            if rest.startswith("("):
                continue
            parts = rest.split()
            if parts:
                names.add(parts[0].lower())
    return names


def parse_cargo_toml_deps(text: str) -> set[str]:
    """[dependencies] and [dev-dependencies] crate names."""
    names: set[str] = set()
    section: str | None = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            sec = s[1:-1].strip().lower()
            if sec in ("dependencies", "dev-dependencies", "build-dependencies"):
                section = sec
            else:
                section = None
            continue
        if section and "=" in line and not s.startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key and not key.startswith("["):
                names.add(_normalize_pkg_name(key))
    return names


def parse_pyproject_toml_deps(text: str) -> set[str]:
    """Very small parser: [project] dependencies = [...] lines."""
    names: set[str] = set()
    in_project = False
    in_deps = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            in_project = s.lower() == "[project]"
            in_deps = False
            continue
        if in_project and re.match(r"^dependencies\s*=", s, re.I):
            in_deps = True
            continue
        if in_project and in_deps:
            if s.startswith("]"):
                in_deps = False
                continue
            # "foo>=1", 'bar'
            m = re.match(r'^["\']?([A-Za-z0-9][A-Za-z0-9._-]*)', s)
            if m:
                names.add(_normalize_pkg_name(m.group(1)))
    return names


DEP_FILES = [
    ("requirements.txt", parse_requirements_txt),
    ("requirements-dev.txt", parse_requirements_txt),
    ("package.json", parse_package_json),
    ("pyproject.toml", parse_pyproject_toml_deps),
    ("go.mod", parse_go_mod),
    ("Cargo.toml", parse_cargo_toml_deps),
]


def collect_dependency_set(snapshot: RepoSnapshot, phase: str) -> set[str]:
    """phase 'before' or 'after' — read each known file from that phase only."""
    names: set[str] = set()
    for rel, parser in DEP_FILES:
        content: str | None = None
        if phase == "before":
            if snapshot.before_files is not None:
                content = snapshot.before_files.get(rel)
            elif snapshot.before_root:
                p = snapshot.before_root / rel
                if p.is_file():
                    content = p.read_text(encoding="utf-8", errors="replace")
        else:
            if snapshot.after_files is not None:
                content = snapshot.after_files.get(rel)
            elif snapshot.after_root:
                p = snapshot.after_root / rel
                if p.is_file():
                    content = p.read_text(encoding="utf-8", errors="replace")
        if content:
            names |= parser(content)
    return names


def count_new_dependencies(snapshot: RepoSnapshot) -> tuple[int, dict]:
    before = collect_dependency_set(snapshot, "before")
    after = collect_dependency_set(snapshot, "after")
    new = after - before
    detail = {
        "before_count": len(before),
        "after_count": len(after),
        "new_dependency_names": sorted(new),
    }
    logger.debug("new dependencies count=%s", len(new))
    return len(new), detail
