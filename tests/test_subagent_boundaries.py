"""Boundary tests for subagent public API usage."""

from __future__ import annotations

import ast
from pathlib import Path


FORBIDDEN_IMPORT_PREFIXES = (
    "agent_runtime.core",
    "agent_runtime.memory",
    "agent_runtime.storage",
    "agent_runtime.registry",
    "agent_runtime.common",
)


def test_subagents_do_not_import_runtime_internals() -> None:
    violations: list[str] = []
    for path in sorted(Path("subagents").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_forbidden(alias.name):
                        violations.append(f"{path}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if _is_forbidden(module):
                    violations.append(f"{path}:{node.lineno} from {module} import ...")

    assert violations == []


def test_subagents_do_not_contain_environment_assets() -> None:
    violations: list[str] = []
    for path in sorted(Path("subagents").glob("*")):
        if not path.is_dir() or path.name.startswith("__"):
            continue
        for disallowed in ("ENVIRONMENT.md", "data", "prepare"):
            candidate = path / disallowed
            if candidate.exists():
                violations.append(str(candidate))

    assert violations == []


def _is_forbidden(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in FORBIDDEN_IMPORT_PREFIXES
    )
