"""Guardrails for the boundaries stated in spec/architecture.md."""

import subprocess
import sys


def test_graph_validation_does_not_load_the_rest_layer():
    # conftest imports the app, so only a fresh interpreter can tell whether
    # validation itself pulled in routing or its framework.
    script = """
import sys
import factoria.services.runcheck

rest_modules = (
    "fastapi", "starlette", "factoria.main", "factoria.infrastructure.http",
)
loaded = sorted(
    name for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in rest_modules)
)
assert not loaded, "Graph validation loaded the REST layer: " + ", ".join(loaded)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        "Graph validation must load without REST routing, FastAPI or Starlette "
        "(spec/architecture.md, The API).\n" + result.stderr
    )


def test_the_domain_imports_nothing_outside_itself():
    # Read, not imported: a lazy import inside a function is a dependency too,
    # and only the source shows one that no test happens to execute.
    import ast
    from pathlib import Path

    package = Path(__file__).resolve().parents[1] / "factoria"
    framework = ("fastapi", "starlette", "uvicorn")
    reached: list[str] = []
    for path in sorted((package / "domain").rglob("*.py")):
        here = ".".join(path.relative_to(package.parent).with_suffix("").parts)
        anchor = here.split(".")[:-1]  # the package a relative import starts from
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level:
                base = anchor[: len(anchor) - (node.level - 1)]
                targets = [".".join(base + ([node.module] if node.module else []))]
            elif isinstance(node, ast.ImportFrom):
                targets = [node.module or ""]
            elif isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            else:
                continue
            for target in targets:
                outside = target.startswith("factoria") and not target.startswith(
                    "factoria.domain"
                )
                if outside or target.split(".")[0] in framework:
                    reached.append(f"{here} imports {target}")
    assert not reached, (
        "The domain must not reach a service, infrastructure or the web framework "
        "(spec/architecture.md, Layout):\n" + "\n".join(reached)
    )
