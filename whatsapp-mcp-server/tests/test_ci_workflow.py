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


def test_a_new_push_to_a_pull_request_cancels_the_run_it_replaces_but_pushes_to_branches_do_not():  # CI-01
    text = read()
    assert re.search(r"concurrency:\s*\n\s*group:\s*\$\{\{ github\.workflow \}\}-\$\{\{ github\.event\.pull_request\.number \|\| github\.ref \}\}", text)
    # cancelling is conditional: only for pull_request events, so a merged commit on main or dev is checked to the end
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in text
    assert "cancel-in-progress: true" not in text


def frontend_steps():
    text = read()
    assert re.search(r"^  frontend:\s*$", text, re.M), "the workflow needs a frontend job"
    job = re.split(r"^  frontend:\s*$", text, flags=re.M)[1]
    job = re.split(r"^  \w[\w-]*:\s*$", job, flags=re.M)[0]            # up to the next job
    return job


def test_ci_runs_vitest_for_the_spa():  # MIG-11
    job = frontend_steps()
    assert re.search(r'node-version:\s*"?24"?', job)
    assert re.search(r"run:\s*npm ci\b", job)
    assert re.search(r"run:\s*npx vitest run\b.*--coverage", job)
    assert "working-directory: whatsapp-mcp-server/frontend" in job


def test_ci_runs_playwright_with_no_retries():  # MIG-12
    job = frontend_steps()
    assert re.search(r"run:\s*npx playwright install\b.*chromium", job)
    assert re.search(r"run:\s*npx playwright test\s*$", job, re.M)    # no flag turns retries on
    assert "--retries" not in job
    config = open(os.path.join(os.path.dirname(__file__), "..", "frontend", "playwright.config.ts"), encoding="utf-8").read()
    assert re.search(r"^\s*retries:\s*0,", config, re.M)
    # the pages are built before the browser tests, which serve them from ui/web
    assert job.index("npx vite build") < job.index("npx playwright test")


def test_ci_fails_on_type_lint_or_build_errors():  # MIG-13
    job = frontend_steps()
    for command in ("npx tsc --noEmit", "npx eslint .", "npx vite build"):
        assert re.search(r"run:\s*" + re.escape(command) + r"\s*$", job, re.M), command
    # nothing may swallow a failure: no continue-on-error, no "|| true", no step-level shell override
    assert "continue-on-error" not in job
    assert "|| true" not in job and "|| :" not in job
    assert not re.search(r"^\s*shell:", job, re.M)
