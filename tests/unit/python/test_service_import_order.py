"""Static check of sys.path + import ordering in source services.

Every source service (``sources/<name>/service.py``) runs as a standalone
script under systemd, so its only initial ``sys.path[0]`` is the script's
own directory.  The service's top-level code then does two things:

    sys.path.insert(0, <services/>)                 # make ``lib`` importable
    sys.path.insert(0, <this directory>)            # make siblings importable
    from <sibling>_auth import ...                  # needs both
    from lib.config import cfg                      # needs services/

The bug this test catches: if the ``services/`` insert happens *after*
the sibling import, and that sibling transitively imports from ``lib``
(which is what happened when ``*_tokens.py`` moved to the shared
``lib.token_store``), the service fails at startup with
``ModuleNotFoundError: No module named 'lib'``.

We can't easily reproduce this at runtime from pytest because
``conftest.py`` pre-populates ``services/`` on ``sys.path``, hiding
the ordering bug.  Static analysis of each service.py catches it
with zero deploy risk.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SERVICES = Path(__file__).resolve().parents[3] / "services"


def _service_files():
    """All source service entry-point files we need to check."""
    for sub in ("spotify", "plex", "apple_music", "tidal", "usb", "radio"):
        p = SERVICES / "sources" / sub / "service.py"
        if p.exists():
            yield p


def _first_services_path_insert(tree: ast.Module) -> int | None:
    """Line number of the first ``sys.path.insert`` that adds services/.

    We detect it structurally: a ``sys.path.insert(0, ...)`` whose second
    argument contains three nested ``os.path.dirname`` calls — that's
    always exactly ``services/`` relative to a ``services/sources/X/``
    file.  A bare insert of ``dirname(__file__)`` is the sibling insert,
    not services/.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = ast.unparse(node.func)
        if func != "sys.path.insert":
            continue
        if len(node.args) < 2:
            continue
        arg_src = ast.unparse(node.args[1])
        # Three dirname() calls from a services/sources/X/Y.py file =
        # services/.  Two dirname() calls = sources/.  One = sources/X/.
        if arg_src.count("dirname(") >= 3:
            return node.lineno
    return None


def _first_sibling_import_line(tree: ast.Module, tokens_name: str) -> int | None:
    """Line number of ``from <tokens_name> import ...``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == tokens_name:
            return node.lineno
    return None


@pytest.mark.parametrize("service_path", list(_service_files()),
                         ids=lambda p: p.parent.name)
def test_services_path_insert_precedes_sibling_imports(service_path):
    """``services/`` must be on sys.path before any sibling _tokens or
    _auth module is imported — otherwise the service crashes at startup
    with ``ModuleNotFoundError: No module named 'lib'``.

    Regression guard for a real bug introduced during the
    ``lib.token_store`` refactor that broke office deploy.
    """
    tree = ast.parse(service_path.read_text())
    services_line = _first_services_path_insert(tree)

    sibling = service_path.parent.name  # e.g. "spotify"
    sibling_imports = [
        f"{sibling}_tokens",
        f"{sibling}_auth",
    ]

    for mod in sibling_imports:
        import_line = _first_sibling_import_line(tree, mod)
        if import_line is None:
            continue  # this sibling doesn't exist for this service
        assert services_line is not None, (
            f"{service_path.relative_to(SERVICES)} imports {mod} but "
            f"never adds services/ to sys.path — ``lib.*`` imports from "
            f"{mod} will fail at startup."
        )
        assert services_line < import_line, (
            f"{service_path.relative_to(SERVICES)}: services/ is added "
            f"to sys.path at line {services_line}, but {mod} is imported "
            f"at line {import_line}.  Any ``from lib.*`` in {mod} will "
            f"fail at startup.  Move the ``sys.path.insert(0, "
            f"services/)`` above the sibling imports."
        )
