"""The launcher of the web interface: where it listens, and what it refuses (UI-03..UI-06)."""
import json
import os
import socket
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ui import launcher  # noqa: E402


def run_main(environ, probe=lambda *_: None):
    """main() with a runner that records the settings instead of starting Flet, and no real port check."""
    seen = []
    code = launcher.main(environ=environ, runner=seen.append, probe=probe)
    return code, seen


def test_launcher_default_host_is_loopback():  # UI-05
    settings = launcher.resolve({})

    assert (settings.host, settings.port) == ("127.0.0.1", 8765)


def test_launcher_uses_port_from_environment():  # UI-04
    assert launcher.resolve({"WHATSAPP_UI_PORT": "9123"}).port == 9123


@pytest.mark.parametrize("port", ["abc", "0", "80", "1023", "65536", "-1", "8765.5"])
def test_a_port_that_is_not_usable_is_refused_not_replaced_by_the_default(port):  # UI-04
    with pytest.raises(launcher.LaunchError, match="WHATSAPP_UI_PORT"):
        launcher.resolve({"WHATSAPP_UI_PORT": port})


@pytest.mark.parametrize("blank", ["", "  "])
def test_a_blank_port_or_host_means_the_default(blank):  # UI-04, UI-05
    settings = launcher.resolve({"WHATSAPP_UI_PORT": blank, "WHATSAPP_UI_HOST": blank})

    assert (settings.host, settings.port) == (launcher.DEFAULT_HOST, launcher.DEFAULT_PORT)


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "localhost"])
def test_a_loopback_host_needs_no_opt_in(host):  # UI-05
    assert launcher.resolve({"WHATSAPP_UI_HOST": host}).host == host


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "example.com", "10.0.0.5"])
def test_launcher_refuses_remote_host_without_opt_in(host):  # UI-06
    with pytest.raises(launcher.LaunchError) as raised:
        launcher.resolve({"WHATSAPP_UI_HOST": host})

    assert "WHATSAPP_UI_ALLOW_REMOTE=true" in str(raised.value) and host in str(raised.value)


@pytest.mark.parametrize("flag", ["false", "1", "yes", "", "TRUE-ish"])
def test_only_true_allows_a_remote_host(flag):  # UI-06
    with pytest.raises(launcher.LaunchError):
        launcher.resolve({"WHATSAPP_UI_HOST": "0.0.0.0", "WHATSAPP_UI_ALLOW_REMOTE": flag})


def test_a_remote_host_with_the_opt_in_is_accepted_and_warns_about_the_missing_tls():  # UI-06
    settings = launcher.resolve({"WHATSAPP_UI_HOST": "0.0.0.0", "WHATSAPP_UI_ALLOW_REMOTE": "True"})

    assert settings.host == "0.0.0.0" and settings.remote
    assert "TLS" in launcher.remote_warning(settings)


def test_launcher_clears_flet_server_environment():  # UI-06
    environ = {"FLET_SERVER_IP": "0.0.0.0", "FLET_SERVER_PORT": "80", "FLET_SERVER_UDS_PATH": "/tmp/x.sock",
               "KEEP": "1"}

    launcher.prepare_environment(environ)

    assert environ == {"KEEP": "1", "FLET_FORCE_WEB_SERVER": "true"}


def test_launcher_port_in_use_exits_with_reason(capsys):  # UI-03
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        if port < 1024:
            pytest.skip("ephemeral port below 1024")

        code, seen = run_main({"WHATSAPP_UI_PORT": str(port)}, probe=launcher.check_port_free)

    err = capsys.readouterr().err
    assert code == 1 and seen == []
    assert str(port) in err and "already in use" in err


def test_a_free_port_starts_the_runner_with_the_settings(capsys):  # UI-04
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    if port < 1024:
        pytest.skip("ephemeral port below 1024")

    code, seen = run_main({"WHATSAPP_UI_PORT": str(port)})

    assert code == 0 and [(s.host, s.port) for s in seen] == [("127.0.0.1", port)]
    assert f"http://127.0.0.1:{port}" in capsys.readouterr().out


def test_main_refuses_a_remote_host_with_a_message_and_code_1(capsys):  # UI-06
    code, seen = run_main({"WHATSAPP_UI_HOST": "0.0.0.0"})

    assert code == 1 and seen == []
    assert "WHATSAPP_UI_ALLOW_REMOTE" in capsys.readouterr().err


def test_a_stray_flet_variable_cannot_move_the_bind(capsys):  # UI-06
    environ = {"FLET_SERVER_IP": "0.0.0.0", "FLET_SERVER_PORT": "80"}

    code, seen = run_main(environ)

    assert code == 0 and seen[0].host == "127.0.0.1"
    assert "FLET_SERVER_IP" not in environ and "FLET_SERVER_PORT" not in environ


def test_the_port_saved_by_the_interface_is_used_and_the_environment_wins(tmp_path, capsys):  # UI-04
    config = tmp_path / "config"
    config.mkdir()
    (config / "ui_settings.json").write_text(json.dumps({"env": {"WHATSAPP_UI_PORT": "9123", "WHATSAPP_UI_HOST": "localhost"}}))
    os.chmod(config / "ui_settings.json", 0o600)

    from_file = launcher.main(environ={}, runner=(seen := []).append, probe=lambda *_: None)
    in_env = launcher.main(environ={"WHATSAPP_UI_PORT": "9456"}, runner=(seen2 := []).append, probe=lambda *_: None)

    assert from_file == in_env == 0
    assert (seen[0].host, seen[0].port) == ("localhost", 9123)
    assert (seen2[0].host, seen2[0].port) == ("localhost", 9456)


def test_importing_the_launcher_does_not_load_flet():
    root = os.path.dirname(os.path.dirname(__file__))
    code = f"import sys; sys.path.insert(0, {root!r}); import ui.launcher; sys.exit(1 if 'flet' in sys.modules else 0)"

    done = subprocess.run([sys.executable, "-I", "-c", code])

    assert done.returncode == 0


def test_the_settings_carry_the_environment_as_it_was_before_the_file_was_applied(tmp_path):  # CFG-04
    import json
    config = tmp_path / "config"
    config.mkdir()
    (config / "ui_settings.json").write_text(json.dumps({"env": {"WHATSAPP_UI_PORT": "9123", "WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "50"}}))
    os.chmod(config / "ui_settings.json", 0o600)
    environ = {"KEEP": "1"}
    seen = []

    launcher.main(environ=environ, runner=seen.append, probe=lambda *_: None)

    assert seen[0].port == 9123 and environ["WHATSAPP_AUTO_DOWNLOAD_MAX_MB"] == "50"        # applied to the process
    assert seen[0].startup_environ == {"KEEP": "1"}                                          # but remembered as it was


def test_the_assets_folder_is_the_one_inside_the_package():
    assert os.path.isfile(os.path.join(launcher.ASSETS_DIR, "fonts", "OFL.txt"))
