"""The launcher (UI-01, UI-04, UI-05, UI-06): where the interface listens, what it refuses, how it ends."""
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest

from ui import launcher
from ui.api.services import Settings

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_main(environ, probe=lambda *_: None):
    """main() with a runner that records the settings instead of serving, and no real port check."""
    seen = []
    code = launcher.main(environ=environ, runner=seen.append, probe=probe)
    return code, seen


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    if port < 1024:
        pytest.skip("ephemeral port below 1024")
    return port


def test_port_and_remote_bind_rules():
    refused_ports = ["abc", "0", "80", "1023", "65536", "-1", "8765.5"]
    for port in refused_ports:
        with pytest.raises(launcher.LaunchError, match="WHATSAPP_UI_PORT"):
            launcher.resolve({"WHATSAPP_UI_PORT": port})
    for port, expected in (("1024", 1024), ("65535", 65535), (" 9000 ", 9000)):
        assert launcher.resolve({"WHATSAPP_UI_PORT": port}).port == expected

    for host in ("0.0.0.0", "::", "192.168.1.20", "example.com", "10.0.0.5"):
        with pytest.raises(launcher.LaunchError) as raised:
            launcher.resolve({"WHATSAPP_UI_HOST": host})
        assert "WHATSAPP_UI_ALLOW_REMOTE=true" in str(raised.value) and host in str(raised.value)
        for flag in ("false", "1", "yes", "", "TRUE-ish"):
            with pytest.raises(launcher.LaunchError):
                launcher.resolve({"WHATSAPP_UI_HOST": host, "WHATSAPP_UI_ALLOW_REMOTE": flag})
    allowed = launcher.resolve({"WHATSAPP_UI_HOST": "0.0.0.0", "WHATSAPP_UI_ALLOW_REMOTE": "True"})
    assert allowed.host == "0.0.0.0" and allowed.remote is True
    assert "TLS" in launcher.remote_warning(allowed)
    for host in ("127.0.0.1", "127.0.0.2", "::1", "localhost"):
        assert launcher.resolve({"WHATSAPP_UI_HOST": host}).host == host


def test_the_default_bind_is_loopback_on_8765():
    settings = launcher.resolve({})

    assert (settings.host, settings.port, settings.remote) == ("127.0.0.1", 8765, False)
    assert settings.url == "http://127.0.0.1:8765"


@pytest.mark.parametrize("blank", ["", "  "])
def test_a_blank_port_or_host_means_the_default(blank):
    settings = launcher.resolve({"WHATSAPP_UI_PORT": blank, "WHATSAPP_UI_HOST": blank})

    assert (settings.host, settings.port) == (launcher.DEFAULT_HOST, launcher.DEFAULT_PORT)


def test_the_url_of_an_ipv6_host_has_brackets():
    assert launcher.resolve({"WHATSAPP_UI_HOST": "::1"}).url == "http://[::1]:8765"


def test_a_refused_port_exits_1_with_the_reason_and_serves_nothing(capsys):
    code, seen = run_main({"WHATSAPP_UI_PORT": "80"})

    assert code == 1 and seen == []
    assert "WHATSAPP_UI_PORT" in capsys.readouterr().err


def test_a_remote_host_without_permission_exits_1_with_the_reason(capsys):
    code, seen = run_main({"WHATSAPP_UI_HOST": "0.0.0.0"})

    assert code == 1 and seen == []
    assert "WHATSAPP_UI_ALLOW_REMOTE" in capsys.readouterr().err


def test_a_busy_port_exits_1_with_the_message(capsys):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        if port < 1024:
            pytest.skip("ephemeral port below 1024")

        code, seen = run_main({"WHATSAPP_UI_PORT": str(port)}, probe=launcher.check_port_free)

    err = capsys.readouterr().err
    assert code == 1 and seen == []
    assert str(port) in err and "already in use" in err and "WHATSAPP_UI_PORT" in err


def test_a_free_port_starts_the_runner_with_the_settings_and_prints_the_url(capsys):
    port = free_port()

    code, seen = run_main({"WHATSAPP_UI_PORT": str(port)})

    assert code == 0 and [(s.host, s.port) for s in seen] == [("127.0.0.1", port)]
    assert f"http://127.0.0.1:{port}" in capsys.readouterr().out


def test_a_remote_bind_with_permission_warns_about_the_missing_tls(capsys):
    code, seen = run_main({"WHATSAPP_UI_HOST": "0.0.0.0", "WHATSAPP_UI_ALLOW_REMOTE": "true"})

    assert code == 0 and seen[0].remote is True
    assert "TLS" in capsys.readouterr().err


def test_whatsapp_ui_variables_keep_their_meaning(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    saved = {"env": {"WHATSAPP_UI_PORT": "9123", "WHATSAPP_UI_HOST": "localhost", "WHATSAPP_UI_ALLOW_REMOTE": "true"}}
    (config / "ui_settings.json").write_text(json.dumps(saved))
    os.chmod(config / "ui_settings.json", 0o600)

    from_file = run_main({})[1][0]
    env_wins = run_main({"WHATSAPP_UI_PORT": "9456"})[1][0]
    remote_not_from_file = run_main({"WHATSAPP_UI_HOST_FILE_CANNOT_OPEN": "x", "WHATSAPP_UI_HOST": "0.0.0.0"})

    assert (from_file.host, from_file.port) == ("localhost", 9123)
    assert (env_wins.host, env_wins.port) == ("localhost", 9456)
    assert remote_not_from_file[0] == 1 and remote_not_from_file[1] == []   # the opt-in is never read from the file
    assert launcher.resolve({"WHATSAPP_UI_PORT": "9000"}).port == 9000
    assert launcher.resolve({"WHATSAPP_UI_HOST": "10.1.2.3", "WHATSAPP_UI_ALLOW_REMOTE": "true"}).remote is True
    assert run_main({"WHATSAPP_UI_PORT": "9500"})[1][0].startup_environ == {"WHATSAPP_UI_PORT": "9500"}


# -- running ---------------------------------------------------------------------------------------
def test_run_app_serves_the_app_with_uvicorn_and_the_agreed_options(monkeypatch, capsys):
    import uvicorn
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))
    settings = Settings(host="127.0.0.1", port=9777, startup_environ={"WHATSAPP_TIMEZONE": "UTC"})

    launcher.run_app(settings)

    (app, kwargs), = calls
    assert kwargs == {"host": "127.0.0.1", "port": 9777, "log_level": "warning", "server_header": False,
                      "proxy_headers": False, "access_log": False}
    from starlette.testclient import TestClient
    body = TestClient(app, base_url="http://127.0.0.1:9777").get("/api/session").json()
    assert body["state"] == "setup"
    assert re.search(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", capsys.readouterr().out)    # the setup code is in the log


def test_run_app_prints_no_code_when_a_password_exists(monkeypatch, capsys):
    import uvicorn
    from ui_api_rig import install_password
    install_password()
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)

    launcher.run_app(Settings(host="127.0.0.1", port=9777))

    assert "setup code" not in capsys.readouterr().out


def spawn(port, extra_env=None):
    env = {**os.environ, "WHATSAPP_UI_PORT": str(port), **(extra_env or {})}
    env.pop("WHATSAPP_UI_HOST", None)
    return subprocess.Popen([sys.executable, "-m", "ui"], cwd=SERVER_DIR, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)


def wait_until_up(port, process, seconds=30):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"the process ended early: {process.stderr.read()}")
        try:
            return httpx.get(f"http://127.0.0.1:{port}/api/session", timeout=1)
        except httpx.HTTPError:
            time.sleep(0.1)
    raise AssertionError("the interface did not come up")


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM is not delivered to a child like this on Windows")
def test_the_module_serves_on_the_port_and_sigterm_ends_it_cleanly():
    port = free_port()
    process = spawn(port)
    try:
        answer = wait_until_up(port, process)
        assert answer.status_code == 200 and answer.json()["state"] == "setup"
        process.send_signal(signal.SIGTERM)
        code = process.wait(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    out = process.stdout.read()
    assert code == 0
    assert f"http://127.0.0.1:{port}" in out and "setup code" in out


def test_the_module_exits_1_with_a_message_when_the_port_is_busy():
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        if port < 1024:
            pytest.skip("ephemeral port below 1024")
        process = spawn(port)
        _, err = process.communicate(timeout=30)

    assert process.returncode == 1 and "already in use" in err


def test_the_warning_for_a_wildcard_bind_says_which_host_names_are_served():
    wildcard = launcher.Settings(host="0.0.0.0", port=8765, remote=True, startup_environ={"WHATSAPP_UI_ALLOW_REMOTE": "true"})
    assert "WHATSAPP_UI_ALLOWED_HOSTS" in launcher.remote_warning(wildcard)

    listed = launcher.Settings(host="0.0.0.0", port=8765, remote=True,
                               startup_environ={"WHATSAPP_UI_ALLOW_REMOTE": "true", "WHATSAPP_UI_ALLOWED_HOSTS": "mcp.example.net"})
    assert "WHATSAPP_UI_ALLOWED_HOSTS" not in launcher.remote_warning(listed)

    named = launcher.Settings(host="192.168.1.20", port=8765, remote=True, startup_environ={"WHATSAPP_UI_ALLOW_REMOTE": "true"})
    assert "WHATSAPP_UI_ALLOWED_HOSTS" not in launcher.remote_warning(named)
