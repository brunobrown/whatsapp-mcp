"""Testes do monitor, parte 1: perfis em monitor_profiles.json (CFG-01..05)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import monitor  # noqa: E402


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / "monitor_profiles.json"
    monkeypatch.setattr(monitor, "CONFIG_PATH", str(path))

    def write(content):
        path.write_text(content if isinstance(content, str) else json.dumps(content))
    return write


def profile(**fields):
    return {"profiles": {"p": {"targets": ["me"], **fields}}}


def test_missing_file_means_no_profiles(tmp_path, monkeypatch):  # CFG-01
    monkeypatch.setattr(monitor, "CONFIG_PATH", str(tmp_path / "absent.json"))

    assert monitor.list_profiles() == {"success": True, "profiles": []}
    assert monitor.get_profile("p")[1]["error_code"] == "PROFILE_NOT_FOUND"


def test_file_is_read_on_every_call(config):  # CFG-01
    config(profile())
    assert monitor.get_profile("p")[0].mode == "draft"
    config(profile(mode="auto"))
    assert monitor.get_profile("p")[0].mode == "auto"


def test_defaults(config):  # CFG-02
    config(profile())

    p, err = monitor.get_profile("p")

    assert err is None
    assert (p.enabled, p.mode, p.triggers, p.max_event_age_minutes) == (True, "draft", ["mention", "reply", "direct"], 60)
    assert p.limits == {"replies_per_chat_per_hour": 6, "replies_per_sender_per_hour": 4, "cooldown_seconds": 20,
                        "max_reply_chars": 1500}
    assert (p.chats, p.senders_allow, p.senders_deny, p.topics, p.auto_senders, p.approval_token) == ([], [], [], [], [], None)


def test_partial_limits_keep_the_other_defaults(config):  # CFG-02
    config(profile(limits={"max_reply_chars": 300}))

    assert monitor.get_profile("p")[0].limits["max_reply_chars"] == 300
    assert monitor.get_profile("p")[0].limits["cooldown_seconds"] == 20


def test_values_are_normalised(config):  # CFG-02
    config(profile(targets=["me", "+55 (81) 99999-0000", "5581888880000@s.whatsapp.net", "207571524796601@lid"],
                   chats=["120363411171471138@g.us"], topics=["  Deploy ", "LOGIN"],
                   senders={"allow": ["+5511911112222"], "deny": ["5511900001111@s.whatsapp.net"]},
                   triggers=["mention", "from", "mention"]))

    p = monitor.get_profile("p")[0]

    assert p.targets == ["me", "5581999990000", "5581888880000", "207571524796601"]
    assert p.topics == ["deploy", "login"] and p.triggers == ["mention", "from"]
    assert p.senders_allow == ["5511911112222"] and p.senders_deny == ["5511900001111"]
    assert p.chats == ["120363411171471138@g.us"]


@pytest.mark.parametrize("content, fragment", [
    ("{not json", "not valid JSON"),
    ("[]", "object"),
    ({"profiles": {}, "extra": 1}, "only"),
    ({"profiles": []}, "profiles"),
    ({"profiles": {"p": []}}, "must be an object"),
    (profile(unknown=1), "unknown key"),
    (profile(mode="loud"), "mode"),
    (profile(triggers=["mention", "poke"]), "triggers"),
    (profile(triggers=[]), "triggers"),
    ({"profiles": {"p": {}}}, "targets"),
    ({"profiles": {"p": {"targets": []}}}, "targets"),
    ({"profiles": {"p": {"targets": ["abc"]}}}, "not a phone"),
    ({"profiles": {"p": {"targets": ["123"]}}}, "not a phone"),
    ({"profiles": {"p": {"targets": ["120363411171471138@g.us"]}}}, "not a phone"),
    ({"profiles": {"p": {"targets": [5581999990000]}}}, "targets"),
    (profile(chats=["Grupo da empresa"]), "chat JID"),
    (profile(chats=["5581999990000@example.net"]), "chat JID"),
    (profile(senders={"allow": ["abc"]}), "not a phone"),
    (profile(senders={"permit": []}), "allow and/or deny"),
    (profile(senders=[]), "senders"),
    (profile(auto_senders=["x"]), "not a phone"),
    (profile(limits={"cooldown_seconds": 0}), "at least 1"),
    (profile(limits={"cooldown_seconds": "20"}), "at least 1"),
    (profile(limits={"cooldown_seconds": True}), "at least 1"),
    (profile(limits={"per_day": 3}), "limits"),
    (profile(max_event_age_minutes=0), "at least 1"),
    (profile(approval_token=""), "approval_token"),
    (profile(approval_token=123), "approval_token"),
    (profile(enabled="yes"), "enabled"),
    (profile(instructions=5), "instructions"),
    (profile(topics=[""]), "topics"),
])
def test_invalid_file_blocks_every_profile(config, content, fragment):  # CFG-03
    good = {"ok": {"targets": ["me"]}}
    if isinstance(content, dict) and isinstance(content.get("profiles"), dict):
        content = {**content, "profiles": {**good, **content["profiles"]}}  # um perfil bom ao lado do ruim
    config(content)

    for answer in (monitor.get_profile("ok")[1], monitor.list_profiles()):
        assert answer["success"] is False and answer["error_code"] == "MONITOR_CONFIG_INVALID"
        assert fragment in answer["message"]
    assert monitor.get_profile("ok")[0] is None


def test_unreadable_file_is_invalid(config, tmp_path, monkeypatch):  # CFG-03
    monkeypatch.setattr(monitor, "CONFIG_PATH", str(tmp_path))  # um diretório: não dá para ler como arquivo

    assert monitor.list_profiles()["error_code"] == "MONITOR_CONFIG_INVALID"


def test_listing_never_shows_the_token(config):  # CFG-04
    config({"profiles": {"suporte": {
        "targets": ["me", "5581999990000"], "triggers": ["mention", "direct"], "chats": ["120363411171471138@g.us"],
        "senders": {"allow": ["5511911112222"]}, "topics": ["deploy"], "mode": "auto", "auto_senders": ["5511911112222"],
        "approval_token": "segredo-12345", "limits": {"cooldown_seconds": 5}, "max_event_age_minutes": 30,
        "instructions": "Responda só sobre o software X"}}})

    out = monitor.list_profiles()

    assert out["success"] is True and "segredo-12345" not in json.dumps(out)
    assert out["profiles"] == [{
        "name": "suporte", "enabled": True, "targets": ["me", "5581999990000"], "triggers": ["mention", "direct"],
        "chats": ["120363411171471138@g.us"], "senders": {"allow": ["5511911112222"], "deny": []}, "topics": ["deploy"],
        "mode": "auto", "auto_senders": ["5511911112222"],
        "limits": {"replies_per_chat_per_hour": 6, "replies_per_sender_per_hour": 4, "cooldown_seconds": 5,
                   "max_reply_chars": 1500},
        "max_event_age_minutes": 30, "instructions": "Responda só sobre o software X", "has_approval_token": True}]


def test_listing_without_token(config):  # CFG-04
    config(profile())

    assert monitor.list_profiles()["profiles"][0]["has_approval_token"] is False


def test_disabled_and_unknown_profiles(config):  # CFG-05
    config({"profiles": {"off": {"targets": ["me"], "enabled": False}, "on": {"targets": ["me"]}}})

    assert monitor.get_profile("off")[1]["error_code"] == "PROFILE_DISABLED"
    assert monitor.get_profile("nope")[1]["error_code"] == "PROFILE_NOT_FOUND"
    assert "on, off" in monitor.get_profile("nope")[1]["message"] or "off, on" in monitor.get_profile("nope")[1]["message"]
    assert monitor.get_profile("on")[0].name == "on"
    assert [p["name"] for p in monitor.list_profiles()["profiles"]] == ["off", "on"]  # a listagem mostra também os desligados


def test_the_example_file_is_valid(monkeypatch):
    monkeypatch.setattr(monitor, "CONFIG_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "monitor.example.json"))

    out = monitor.list_profiles()

    assert out["success"] is True and [p["name"] for p in out["profiles"]] == ["suporte-software", "acompanhar-numero"]
