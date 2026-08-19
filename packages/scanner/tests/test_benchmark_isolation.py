"""Architecture test proving production email_scanner core never imports benchmark fixtures."""

import ast
from pathlib import Path


def test_production_modules_do_not_import_benchmark_fixtures() -> None:
    """Ensure production code modules never import benchmark_fixtures."""
    src_dir = Path("packages/scanner/src/email_scanner")
    assert src_dir.is_dir(), f"Source directory {src_dir} not found"

    allowed_fixture_importers = {
        "benchmarking.py",
        "consistency_audit.py",
        "cli.py",
        "benchmark_fixtures.py",
    }

    for py_file in src_dir.glob("*.py"):
        if py_file.name in allowed_fixture_importers:
            continue

        content = py_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "benchmark_fixtures" not in alias.name, (
                        f"Production module {py_file.name} imports benchmark_fixtures"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert "benchmark_fixtures" not in node.module, (
                        f"Production module {py_file.name} imports from benchmark_fixtures"
                    )
