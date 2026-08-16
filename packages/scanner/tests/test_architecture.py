"""Architectural boundary tests for scanner-core module isolation."""

import ast
from pathlib import Path

PROHIBITED_MODULES = frozenset(
    {
        "fastapi",
        "starlette",
        "celery",
        "redis",
        "aioredis",
        "sqlalchemy",
        "asyncpg",
        "psycopg2",
        "psycopg",
        "playwright",
        "django",
        "flask",
        "tortoise",
        "peewee",
        "workers",
        "database",
        "ui",
        "billing",
        "tenant",
        "authentication",
        "auth",
        "email_discovery_api",
        "apps",
    }
)


def extract_imported_modules(file_path: Path) -> set[str]:
    """Parse a Python source file AST and extract all top-level imported module names."""
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    imported_modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_name = alias.name.split(".", 1)[0]
                imported_modules.add(top_name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_name = node.module.split(".", 1)[0]
                imported_modules.add(top_name)

    return imported_modules


def test_scanner_core_architectural_isolation() -> None:
    """Assert scanner-core Python modules import zero infrastructure or framework dependencies."""
    src_dir = Path(__file__).resolve().parent.parent / "src" / "email_scanner"
    py_files = list(src_dir.glob("**/*.py"))

    assert len(py_files) > 0, "No scanner-core Python source files found"

    violations: dict[str, set[str]] = {}

    for py_file in py_files:
        imported = extract_imported_modules(py_file)
        forbidden = imported.intersection(PROHIBITED_MODULES)
        if forbidden:
            violations[py_file.name] = forbidden

    assert not violations, f"Prohibited framework/infrastructure imports found: {violations}"
