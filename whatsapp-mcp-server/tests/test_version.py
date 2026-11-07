"""The server reports the version declared in pyproject.toml, in MAJOR.MINOR.PATCH form."""
import os
import re
import sys
import tomllib

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402

PYPROJECT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pyproject.toml")


def declared_version():
    with open(PYPROJECT, "rb") as f:
        return tomllib.load(f)["project"]["version"]


def test_the_declared_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", declared_version())


def test_the_server_reports_the_declared_version():
    assert main.mcp.version == declared_version() == main.VERSION
