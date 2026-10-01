"""Guardrails for the boundaries stated in spec/architecture.md."""

import subprocess
import sys


def test_graph_validation_does_not_load_the_rest_layer():
    # conftest imports the app, so only a fresh interpreter can tell whether
    # validation itself pulled in routing or its framework.
    script = """
import sys
import factoria.runcheck

rest_modules = (
    "fastapi", "starlette", "factoria.api", "factoria.main",
    "factoria.runs_api", "factoria.settings_api", "factoria.skills_api",
    "factoria.knowledge_api",
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
