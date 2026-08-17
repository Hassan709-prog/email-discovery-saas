"""Architecture regression test ensuring clean dependency direction between workspace packages.

Enforces:
1. email_discovery_api must NEVER import or depend on email_discovery_crawl_worker.
2. scanner core must remain clean of imports from api or worker packages.
"""

import ast
from pathlib import Path


def get_python_files(root_dir: Path) -> list[Path]:
    """Recursively collect all .py files in directory."""
    return [p for p in root_dir.rglob("*.py") if not p.name.startswith(".")]


def test_api_does_not_import_worker_package() -> None:
    """Verify apps/api/src never imports email_discovery_crawl_worker."""
    api_src = Path(__file__).parents[1] / "src"
    assert api_src.exists(), f"API source directory not found at {api_src}"

    forbidden_imports = {
        "email_discovery_crawl_worker",
        "workers",
    }

    violations: list[str] = []

    for py_file in get_python_files(api_src):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except Exception as err:
            violations.append(f"Failed to parse {py_file}: {err}")
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    pkg = alias.name.split(".")[0]
                    if pkg in forbidden_imports:
                        violations.append(
                            f"{py_file.name}:{node.lineno} imports forbidden package {pkg!r}"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    pkg = node.module.split(".")[0]
                    if pkg in forbidden_imports:
                        violations.append(
                            f"{py_file.name}:{node.lineno} imports from forbidden package {pkg!r}"
                        )

    assert not violations, "Architecture violations detected:\n" + "\n".join(violations)


def test_scanner_does_not_import_api_or_worker() -> None:
    """Verify scanner-core package never imports API or worker packages."""
    scanner_src = Path(__file__).parents[3] / "packages" / "scanner" / "src"
    assert scanner_src.exists(), f"Scanner source directory not found at {scanner_src}"

    forbidden_imports = {
        "email_discovery_api",
        "email_discovery_crawl_worker",
        "fastapi",
        "sqlalchemy",
    }

    violations: list[str] = []

    for py_file in get_python_files(scanner_src):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except Exception as err:
            violations.append(f"Failed to parse {py_file}: {err}")
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    pkg = alias.name.split(".")[0]
                    if pkg in forbidden_imports:
                        violations.append(
                            f"{py_file.name}:{node.lineno} imports forbidden package {pkg!r}"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    pkg = node.module.split(".")[0]
                    if pkg in forbidden_imports:
                        violations.append(
                            f"{py_file.name}:{node.lineno} imports from forbidden package {pkg!r}"
                        )

    assert not violations, "Scanner architecture violations detected:\n" + "\n".join(violations)
