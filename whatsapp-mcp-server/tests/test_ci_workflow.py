"""The CI workflow pins every action to a commit SHA and runs the checks that guard the project (CI-01)."""
import os
import re

WORKFLOW = os.path.join(os.path.dirname(__file__), "..", "..", ".github", "workflows", "ci.yml")


def read():
    return open(WORKFLOW, encoding="utf-8").read()


def test_every_action_is_pinned_to_a_full_commit_sha():  # CI-01
    uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)", read(), re.M)
    assert uses, "the workflow must use at least one action"
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), f"{ref} is not pinned to a 40-hex commit SHA"


def test_the_workflow_runs_the_bridge_and_server_checks():  # CI-01
    text = read()
    assert "go vet ./..." in text
    assert text.count("go test -race -count=1 ./...") == 2           # encrypted and plain databases
    assert 'WHATSAPP_DB_ENCRYPTION: "off"' in text
    assert "uv sync --locked" in text and "uv run pytest -q" in text
    assert re.search(r"permissions:\s*\n\s*contents:\s*read", text)
