"""Dead code: distinct linter rule codes (TODO-2: pinned tool config)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from architectural_gate.models import RepoSnapshot

logger = logging.getLogger(__name__)

# TODO-2: Pin tool selection relative to this package (ruff for Python).
_PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_RUFF_CONFIG = _PACKAGE_DIR / "config" / "ruff_dead_code.toml"
DEFAULT_CLIPPY_CONFIG = _PACKAGE_DIR / "config" / "clippy_dead_code.toml"


def _run_ruff_json(cwd: Path, config: Path | None) -> tuple[list[dict], str | None]:
    cmd = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        ".",
        "--output-format=json",
        "--exit-zero",
    ]
    if config and config.is_file():
        cmd.extend(["--config", str(config)])
    env = os.environ.copy()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        return [], str(e)
    if proc.returncode not in (0, 1) and not proc.stdout.strip():
        return [], proc.stderr or f"ruff exit {proc.returncode}"
    try:
        data = json.loads(proc.stdout or "[]")
        if isinstance(data, list):
            return data, None
    except json.JSONDecodeError as e:
        return [], str(e)
    return [], proc.stderr


def _run_staticcheck_json(cwd: Path) -> tuple[list[dict], str | None]:
    """staticcheck -f json ./... (Go); requires go.mod and staticcheck on PATH."""
    cmd = ["staticcheck", "-f", "json", "./..."]
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=180,
            env=os.environ.copy(),
        )
    except (FileNotFoundError, OSError) as e:
        return [], str(e)
    if proc.returncode not in (0, 1) and not (proc.stdout or "").strip():
        return [], proc.stderr or f"staticcheck exit {proc.returncode}"
    try:
        data = json.loads(proc.stdout or "[]")
        if isinstance(data, list):
            return data, None
    except json.JSONDecodeError as e:
        return [], str(e)
    return [], proc.stderr


def _run_cargo_clippy_messages(cwd: Path) -> tuple[list[dict], str | None]:
    """cargo clippy --message-format=json (Rust); uses project's Cargo.toml."""
    cmd = [
        "cargo",
        "clippy",
        "--message-format=json",
        "--quiet",
        "--",
        "-W",
        "clippy::all",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=300,
            env=os.environ.copy(),
        )
    except (FileNotFoundError, OSError) as e:
        return [], str(e)
    messages: list[dict] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        msg = obj.get("message")
        if isinstance(msg, dict) and msg.get("code") is not None:
            messages.append(obj)
    err = (
        None
        if proc.returncode in (0, 1) or messages
        else (proc.stderr or f"exit {proc.returncode}")
    )
    return messages, err


def _unique_codes_from_clippy(messages: list[dict]) -> set[str]:
    codes: set[str] = set()
    for obj in messages:
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        code = msg.get("code")
        if isinstance(code, dict):
            c = code.get("code")
            if c:
                codes.add(str(c))
        elif isinstance(code, str) and code:
            codes.add(code)
    return codes


def count_dead_code_rule_types(
    snapshot: RepoSnapshot,
    language: str,
    ruff_config: Path | None = None,
) -> tuple[int, dict]:
    """
    Each unique linter rule code counts as one instance toward the threshold.
    """
    detail: dict = {"language": language, "tool": None}
    lang = (language or "").lower()
    root = snapshot.after_root or snapshot.before_root

    if lang in ("python", "py"):
        if not root or not root.is_dir():
            detail["note"] = "python_dead_code_skipped_no_filesystem_root"
            detail["hint"] = (
                "Use RepoSnapshot(after_root=...) or before_root for Ruff; inline-only maps cannot run workspace lint."
            )
            return 0, detail
        detail["tool"] = "ruff"
        cfg = (
            ruff_config
            if ruff_config and ruff_config.is_file()
            else DEFAULT_RUFF_CONFIG
        )
        detail["config_path"] = str(cfg)
        findings, err = _run_ruff_json(root, cfg)
        if err:
            detail["error"] = err
            detail["note"] = "ruff_unavailable_or_failed"
            logger.warning("ruff failed: %s", err)
            return 0, detail
        codes: set[str] = set()
        for item in findings:
            code = item.get("code")
            if code:
                codes.add(str(code))
        detail["unique_rule_codes"] = sorted(codes)
        detail["raw_findings_count"] = len(findings)
        return len(codes), detail

    if lang in ("go", "golang") and root and root.is_dir():
        gomod = root / "go.mod"
        if not gomod.is_file():
            detail["note"] = "go_dead_code_skipped_no_go_mod"
            return 0, detail
        detail["tool"] = "staticcheck"
        detail["config_note"] = (
            "Pin staticcheck version in CI; optional golangci-lint overlay"
        )
        findings, err = _run_staticcheck_json(root)
        if err:
            detail["error"] = err
            detail["note"] = "staticcheck_unavailable_or_failed"
            logger.warning("staticcheck failed: %s", err)
            return 0, detail
        codes = {str(x.get("code", "")) for x in findings if x.get("code")}
        codes.discard("")
        detail["unique_rule_codes"] = sorted(codes)
        detail["raw_findings_count"] = len(findings)
        return len(codes), detail

    if lang in ("rust", "rs") and root and root.is_dir():
        cargo = root / "Cargo.toml"
        if not cargo.is_file():
            detail["note"] = "rust_dead_code_skipped_no_cargo_toml"
            return 0, detail
        detail["tool"] = "cargo_clippy"
        if DEFAULT_CLIPPY_CONFIG.is_file():
            detail["pinned_config_path"] = str(DEFAULT_CLIPPY_CONFIG)
        messages, err = _run_cargo_clippy_messages(root)
        if err and not messages:
            detail["error"] = err
            detail["note"] = "cargo_clippy_unavailable_or_failed"
            logger.warning("cargo clippy failed: %s", err)
            return 0, detail
        codes = _unique_codes_from_clippy(messages)
        detail["unique_rule_codes"] = sorted(codes)
        detail["raw_findings_count"] = len(messages)
        return len(codes), detail

    if lang in ("javascript", "js", "typescript", "ts", "tsx", "jsx", "node", "nodejs"):
        detail["note"] = (
            "js_ts_dead_code_optional_eslint: pin ESLint in CI and wire a future checker; metric 0"
        )
        return 0, detail

    detail["note"] = "dead_code_check_skipped_no_pinned_tool_for_language"
    return 0, detail
