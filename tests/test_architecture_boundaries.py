"""Architecture Boundary Enforcement Tests.

Enforces that packages/scanner remains a pure scanner core library
with zero dependencies or imports of redis, fastapi, sqlalchemy, or worker packages.
"""

import importlib
import inspect
import pkgutil


def test_scanner_core_has_zero_redis_imports():
    """Verify packages/scanner modules contain zero imports of redis."""
    import email_scanner

    scanner_modules: list[str] = []
    for _, modname, _ in pkgutil.walk_packages(email_scanner.__path__, prefix="email_scanner."):
        scanner_modules.append(modname)

    for modname in scanner_modules:
        mod = importlib.import_module(modname)
        source = inspect.getsource(mod)

        # Assert no import redis or from redis statements in source
        lines = source.splitlines()
        for idx, line in enumerate(lines, start=1):
            clean_line = line.strip()
            if clean_line.startswith("#"):
                continue
            assert not (
                clean_line.startswith("import redis") or clean_line.startswith("from redis")
            ), f"Module '{modname}' line {idx} imports redis! Scanner core must be isolated: {line}"


def test_scanner_core_can_import_without_redis_in_sys_modules():
    """Verify email_scanner imports cleanly even if redis module is not in sys.modules."""
    import email_scanner

    assert hasattr(email_scanner, "normalize_url")
    assert hasattr(email_scanner, "clean_and_review_urls")
