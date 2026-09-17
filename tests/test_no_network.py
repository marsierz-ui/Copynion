"""The load-bearing privacy test: Copynion cannot phone home.

"Your data stays local" is a claim about code, not about intentions, so it is
tested like any other behaviour. If someone later adds ``import requests`` to
the package, this test fails and the claim in the README stops being a lie.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "src" / "copynion"

#: Modules that can move bytes off this machine. The observer has no business
#: importing any of them.
NETWORK_MODULES = {
    "asyncio",  # not networking per se, but its presence usually means a client
    "ftplib",
    "http",
    "httplib",
    "httpx",
    "imaplib",
    "poplib",
    "requests",
    "smtplib",
    "socket",
    "socketserver",
    "ssl",
    "telnetlib",
    "urllib",
    "urllib3",
    "websocket",
    "websockets",
    "xmlrpc",
    "aiohttp",
    "boto3",
}

SOURCE_FILES = sorted(PACKAGE_ROOT.rglob("*.py"))


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_source_files_exist():
    assert SOURCE_FILES, "no source files found - the test is pointed at the wrong directory"


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: p.name)
def test_no_networking_imports(path: Path):
    offenders = _imported_names(path) & NETWORK_MODULES
    assert not offenders, (
        f"{path.relative_to(PACKAGE_ROOT)} imports {sorted(offenders)}. "
        "Copynion promises that data never leaves the machine; that promise is "
        "only as good as this test."
    )


def test_no_network_capable_modules_loaded_at_import():
    """Importing the package must not pull a socket stack in transitively."""
    for name in list(sys.modules):
        if name.startswith("copynion"):
            del sys.modules[name]
    before = set(sys.modules)
    import copynion  # noqa: F401
    import copynion.cli  # noqa: F401
    import copynion.observer  # noqa: F401
    import copynion.stats  # noqa: F401
    import copynion.storage  # noqa: F401

    newly_loaded = set(sys.modules) - before
    # `ssl`/`socket` can already be present from pytest's own machinery, which is
    # why this checks what *Copynion* caused to load rather than what exists.
    offenders = {m for m in newly_loaded if m.split(".")[0] in {"socket", "ssl", "requests", "httpx", "urllib3"}}
    assert not offenders, f"importing copynion loaded networking modules: {sorted(offenders)}"


def test_no_hardcoded_urls():
    """No endpoints to send anything to."""
    allowed = {"https://github.com/marsierz-ui/copynion"}
    import re

    pattern = re.compile(r"https?://[^\s\"')]+")
    for path in SOURCE_FILES:
        for match in pattern.findall(path.read_text("utf-8")):
            cleaned = match.rstrip(".,;")
            assert cleaned in allowed, f"{path.name} contains a URL: {cleaned}"
