"""Testes do monitor, parte 3: a ferramenta monitor_messages (MON-01, MON-03, MON-13, MON-15, CFG-04/05)."""
import asyncio
import inspect
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402
import monitor  # noqa: E402
import whatsapp  # noqa: E402
from tests.test_monitor_scan import ANA, DM_ANA, GROUP, ME, NOW, add, make  # noqa: E402


@pytest.fixture
def conn(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch)
    monkeypatch.setattr(monitor, "CONFIG_PATH", str(tmp_path / "monitor_profiles.json"))
    return c


def write_config(profiles):
    with open(monitor.CONFIG_PATH, "w") as f:
        json.dump({"profiles": profiles}, f)


class Time:
    """Relógio e sleep falsos: a espera avança o relógio sem esperar de verdade."""

    def __init__(self, on_sleep=None):
        self.now, self.sleeps, self.on_sleep = 1000.0, [], on_sleep

    def clock(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep(len(self.sleeps))


def call(clock=None, **kwargs):
    clock = clock or Time()
    kwargs.setdefault("targets", ["me"])
    return asyncio.run(monitor.monitor_messages(sleep=clock.sleep, clock=clock.clock, now=NOW, **kwargs))


def test_tools_are_registered_and_the_monitor_is_async():  # MON-15
    assert inspect.iscoroutinefunction(main.monitor_messages)
    assert inspect.iscoroutinefunction(monitor.monitor_messages)
    doc = main.monitor_messages.__doc__
    for expected in ("cursor", "wait_seconds", "mention", "reply", "direct", "from", "untrusted_content", "profile",
                     "OWN_ACCOUNT_UNKNOWN", "has_more"):
        assert expected in doc
    assert "never the token" in main.get_monitor_profiles.__doc__


def test_first_call_returns_only_the_cursor_and_does_not_wait(conn):  # MON-01
    add(conn, "OLD", f"@{ME} antigo")
    clock = Time()

    out = call(clock, wait_seconds=30)

    assert out["success"] is True and out["cursor"] == "1" and out["events"] == []
    assert out["has_more"] is False and out["timed_out"] is False and clock.sleeps == []
    assert out["untrusted_content"] is True and "untrusted" in out["notice"]


def test_new_message_after_the_cursor_is_returned_once(conn):  # MON-02
    add(conn, "OLD", f"@{ME} antigo")
    cursor = call()["cursor"]
    add(conn, "NEW", f"@{ME} novo")

    out = call(cursor=cursor)
    again = call(cursor=out["cursor"])

    assert [e["message"]["id"] for e in out["events"]] == ["NEW"] and out["cursor"] == "2"
    assert again["events"] == [] and again["cursor"] == "2" and again["timed_out"] is False


def test_wait_returns_as_soon_as_an_event_arrives(conn):  # MON-03
    add(conn, "OLD", "ruído")
    clock = Time(on_sleep=lambda n: add(conn, "NEW", f"@{ME} oi") if n == 3 else None)

    out = call(clock, cursor="1", wait_seconds=30)

    assert [e["message"]["id"] for e in out["events"]] == ["NEW"] and out["timed_out"] is False
    assert len(clock.sleeps) == 3 and out["cursor"] == "2"


def test_wait_times_out_with_the_cursor_moved_past_noise(conn):  # MON-03
    clock = Time(on_sleep=lambda n: add(conn, f"N{n}", "ruído", minute=40 + n) if n == 2 else None)

    out = call(clock, cursor=0, wait_seconds=5)

    assert out["events"] == [] and out["timed_out"] is True and out["cursor"] == "1"
    assert 4.9 <= sum(clock.sleeps) <= 5.1 and max(clock.sleeps) <= 1.0


def test_no_wait_means_one_look(conn):  # MON-03
    clock = Time()

    out = call(clock, cursor=0)

    assert out["events"] == [] and out["timed_out"] is False and clock.sleeps == []


def test_cursor_accepts_a_number_or_its_text(conn):  # MON-13
    add(conn, "A", f"@{ME}")
    add(conn, "B", f"@{ME}")

    assert len(call(cursor=0)["events"]) == 2
    assert len(call(cursor="0")["events"]) == 2
    assert [e["message"]["id"] for e in call(cursor=" 1 ")["events"]] == ["B"]


@pytest.mark.parametrize("kwargs", [
    {"cursor": "abc"}, {"cursor": "-1"}, {"cursor": -1}, {"cursor": True}, {"cursor": "1.5"}, {"cursor": "١"},
    {"cursor": 5},                                  # além do que está gravado
    {"wait_seconds": -1}, {"wait_seconds": 56}, {"wait_seconds": "5"}, {"wait_seconds": True},
    {"limit": 0}, {"limit": 101}, {"limit": "5"}, {"limit": True}, {"limit": 1.5},
    {"triggers": ["poke"]}, {"triggers": []}, {"triggers": "mention"},
    {"targets": []}, {"targets": ["abc"]}, {"targets": ["123"]}, {"targets": "me"},
    {"profile": "p", "targets": ["me"]}, {"profile": "p", "triggers": ["mention"]},
])
def test_invalid_arguments(conn, kwargs):  # MON-13
    add(conn, "A", "x")
    write_config({"p": {"targets": ["me"]}})
    base = {} if "profile" in kwargs else {"targets": ["me"]}
    if "targets" in kwargs:
        base = {}

    out = call(**{**base, **kwargs}) if not ("profile" in kwargs) else call(**kwargs)

    assert out["success"] is False and out["error_code"] == "INVALID_ARGUMENT" and out["message"]


def test_neither_profile_nor_targets(conn):  # MON-13
    out = asyncio.run(monitor.monitor_messages(now=NOW))

    assert out["error_code"] == "INVALID_ARGUMENT"


def test_profile_brings_targets_instructions_and_filters(conn):  # MON-12, CFG-04
    write_config({"suporte": {"targets": ["me"], "triggers": ["mention"], "topics": ["deploy"], "mode": "auto",
                              "auto_senders": [ANA], "instructions": "Responda só sobre o software X"}})
    add(conn, "SKIP", f"@{ME} outro assunto")
    add(conn, "HIT", f"@{ME} o deploy falhou")

    out = asyncio.run(monitor.monitor_messages(profile="suporte", cursor=0, now=NOW))

    assert [e["message"]["id"] for e in out["events"]] == ["HIT"]
    assert out["events"][0]["reply_mode"] == "auto"
    assert out["profile"] == "suporte" and out["instructions"] == "Responda só sobre o software X"
    assert out["untrusted_content"] is True


def test_ad_hoc_watching_is_read_only(conn):  # MON-12
    add(conn, "M", f"@{ME} oi")

    out = call(cursor=0)

    assert out["events"][0]["reply_mode"] == "none" and "profile" not in out and "instructions" not in out


def test_ad_hoc_targets_and_triggers(conn):  # MON-04, MON-07
    add(conn, "G", "do grupo", sender=ANA)
    add(conn, "D", "do privado", chat=DM_ANA, sender=ANA)

    only_from = call(cursor=0, targets=[ANA], triggers=["from"])
    default = call(cursor=0, targets=[ANA])

    assert [e["message"]["id"] for e in only_from["events"]] == ["G", "D"]
    assert [e["message"]["id"] for e in default["events"]] == ["D"]


def test_profile_errors(conn):  # CFG-05
    write_config({"off": {"targets": ["me"], "enabled": False}})

    assert call(targets=None, profile="off")["error_code"] == "PROFILE_DISABLED"
    assert call(targets=None, profile="nope")["error_code"] == "PROFILE_NOT_FOUND"
    with open(monitor.CONFIG_PATH, "w") as f:
        f.write("{")
    assert call(targets=None, profile="off")["error_code"] == "MONITOR_CONFIG_INVALID"


def test_unknown_own_account_is_reported_even_on_the_first_call(conn, monkeypatch):  # MON-14
    def unknown():
        raise monitor.OwnAccountUnknown("sem número")
    monkeypatch.setattr(monitor, "own_number", unknown)

    assert call()["error_code"] == "OWN_ACCOUNT_UNKNOWN"
    assert call(cursor=0)["error_code"] == "OWN_ACCOUNT_UNKNOWN"
    assert call(targets=["5511999990000"])["success"] is True  # sem "me" não precisa do número


def test_missing_database(tmp_path, monkeypatch):  # MON-14
    missing = tmp_path / "none.db"
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", str(missing))
    monkeypatch.setattr(monitor, "own_number", lambda: ME)

    out = call()

    assert out["error_code"] == "DIRECTORY_UNAVAILABLE" and not missing.exists()


def test_a_page_is_reported_with_has_more(conn):  # MON-11
    for n in range(3):
        add(conn, f"M{n}", f"@{ME}", minute=50 + n)

    first = call(cursor=0, limit=2)
    rest = call(cursor=first["cursor"], limit=2)

    assert len(first["events"]) == 2 and first["has_more"] is True
    assert [e["message"]["id"] for e in rest["events"]] == ["M2"] and rest["has_more"] is False


def test_get_monitor_profiles_tool(conn):  # CFG-04
    write_config({"p": {"targets": ["me"], "approval_token": "segredo"}})

    out = main.get_monitor_profiles()

    assert out["profiles"][0]["name"] == "p" and "segredo" not in json.dumps(out)
