"""Protect the inward dependency direction of the migrated contexts."""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
MODULES = APP / "modules"

CONTEXTS = ("identity", "membership", "catalog", "analytics", "knowledge")
PROHIBITED = ("app.api", "app.core", "app.models", "app.services", "fastapi", "sqlalchemy")
PROHIBITED += tuple(f"app.modules.{context}.infrastructure" for context in CONTEXTS)


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_contexts_do_not_import_each_other():
    for context in CONTEXTS:
        others = tuple(f"app.modules.{other}" for other in CONTEXTS if other != context)
        files = list((MODULES / context).rglob("*.py"))
        assert files, f"{MODULES / context} holds no modules, so the scan would pass without checking anything"
        for path in files:
            for module in imports(path):
                assert not any(module == prefix or module.startswith(prefix + ".") for prefix in others), (
                    f"{path}: {module} reaches into another context"
                )


def test_domain_and_application_do_not_import_outer_layers():
    for context in CONTEXTS:
        for layer in ("domain", "application"):
            files = list((MODULES / context / layer).glob("*.py"))
            assert files, f"{MODULES / context / layer} holds no modules, so the scan would pass without checking anything"
            for path in files:
                for module in imports(path):
                    assert not any(module == prefix or module.startswith(prefix + ".") for prefix in PROHIBITED), (
                        f"{path}: outward dependency {module}"
                    )
