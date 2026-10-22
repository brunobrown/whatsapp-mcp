"""Testes do monitor, parte 5: chamar o agente pelo nome, responder ao agente, mensagens da própria conta, auto_chats."""
import asyncio
import json
import os
import sys
import threading
import time
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402
import monitor  # noqa: E402
import whatsapp  # noqa: E402
from tests.test_monitor_scan import ANA, ANA_LID, BOB, DM_ANA, GROUP, GROUP2, ME, NOW, add, hits, make  # noqa: E402
from tests.test_monitor_reply import TOKEN, audit_lines, put_audit, real_sends, sent  # noqa: E402,F401

BOT = "@wall-e"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch)
    monkeypatch.setattr(monitor, "CONFIG_PATH", str(tmp_path / "monitor_profiles.json"))
    monkeypatch.setattr(monitor, "AUDIT_PATH", str(tmp_path / "monitor_audit.log"))
    return c


def profile(**fields):
    fields.setdefault("aliases", [BOT])
    fields.setdefault("triggers", ["alias"])
    return monitor.Profile(name="p", **fields)


def run(p=None, cursor=0, limit=20):
    return monitor.scan(p or profile(), cursor, limit, now=NOW)


def config(**fields):
    body = {"aliases": [BOT], "approval_token": TOKEN, **fields}
    with open(monitor.CONFIG_PATH, "w") as f:
        json.dump({"profiles": {"p": body}}, f)


def parsed(**fields):
    with open(monitor.CONFIG_PATH, "w") as f:
        json.dump({"profiles": {"p": fields}}, f)
    return monitor.get_profile("p")


# ---- ALIAS-01: o nome na mensagem --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "@wall-e qual é o próximo item?", "oi @Wall-E, me ajuda", "@WALL-E", "ok. @wall-e!", "(@wall-e)", "pergunta @wall-e",
    "@wall-e\ne a segunda linha", "¿@wall-e?", "x\t@wall-e",
])
def test_alias_fires_as_a_whole_word_in_any_case(conn, text):  # ALIAS-01
    add(conn, "M", text)

    assert hits(run()) == [("M", [("alias", BOT)])]


@pytest.mark.parametrize("text", [
    "@wall-ember", "@wall-e-bot", "@wall-e2", "a@wall-e", "email: eu@wall-e.com", "wall-e", "@wall", "@@wall-e2", "sem nome",
    "x@wall-e", "@wall-e_",
])
def test_alias_does_not_fire_inside_other_words(conn, text):  # ALIAS-01
    add(conn, "M", text)

    assert hits(run()) == []


def test_alias_without_arroba_matches_the_word_only(conn):  # ALIAS-01
    add(conn, "HIT", "ei wall-e, tudo bem?")
    add(conn, "MISS1", "o wall-ember")
    add(conn, "MISS2", "a@wall-e")
    add(conn, "MISS3", "@wall-e") # com @ antes: o nome sem @ não casa dentro de uma menção ao outro

    assert [h[0] for h in hits(run(profile(aliases=["wall-e"])))] == ["HIT"]


def test_several_aliases_each_report_themselves(conn):  # ALIAS-01
    add(conn, "M", "@robo e @wall-e juntos")

    assert hits(run(profile(aliases=["@wall-e", "@robo", "@outro"]))) == [("M", [("alias", "@wall-e"), ("alias", "@robo")])]


def test_regex_characters_in_an_alias_are_literal(conn):  # ALIAS-01
    add(conn, "HIT", "chama @bot+1 aqui")
    add(conn, "MISS", "chama @bot111 aqui")

    assert [h[0] for h in hits(run(profile(aliases=["@bot+1"])))] == ["HIT"]


# ---- ALIAS-02: command -------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, command", [
    ("@wall-e qual é o próximo item da lista de tarefas?", "qual é o próximo item da lista de tarefas?"),
    ("@Wall-E, me ajuda", "me ajuda"),
    ("oi @wall-e: veja isso", "oi veja isso"),
    ("pergunta para o @wall-e", "pergunta para o"),
    ("@wall-e - status?", "status?"),
    ("@wall-e", ""),
    ("  @wall-e   muitos   espaços  ", "muitos   espaços"),
    ("@wall-e primeira, @wall-e segunda", "primeira, @wall-e segunda"),
])
def test_command_is_the_text_without_the_name(conn, text, command):  # ALIAS-02
    add(conn, "M", text)

    event = run().events[0]

    assert event["command"] == command and event["message"]["content"] == text


def test_events_without_alias_have_no_command(conn):  # ALIAS-02
    add(conn, "M", f"@{ME} oi")

    event = run(monitor.Profile(name="p", targets=["me"])).events[0]

    assert event["command"] is None


# ---- ALIAS-03: grupo e privado -----------------------------------------------------------------------------

def test_alias_in_groups_and_direct_chats_but_not_every_direct_message(conn):  # ALIAS-03
    add(conn, "G", "@wall-e no grupo", chat=GROUP)
    add(conn, "D", "@wall-e no privado", chat=DM_ANA, sender=ANA)
    add(conn, "D2", "privado sem o nome", chat=DM_ANA, sender=ANA)
    add(conn, "G2", "grupo sem o nome", chat=GROUP)

    assert [h[0] for h in hits(run())] == ["G", "D"]


# ---- ALIAS-04/05: configuração ----------------------------------------------------------------------------

@pytest.mark.parametrize("fields, fragment", [
    ({"aliases": "wall-e"}, "aliases"),
    ({"aliases": []}, "aliases"),
    ({"aliases": ["com espaço"]}, "aliases"),
    ({"aliases": ["a"]}, "aliases"),
    ({"aliases": ["x" * 41]}, "aliases"),
    ({"aliases": [f"@bot{n}" for n in range(11)]}, "aliases"),
    ({"aliases": [5]}, "aliases"),
    ({"aliases": ["@wall-e", "@wall-e"]}, "aliases"),
    ({}, "targets or aliases"),
    ({"targets": ["me"], "triggers": ["alias"]}, "alias"),
    ({"aliases": ["@bot"], "triggers": ["mention"]}, "targets"),
    ({"aliases": ["@bot"], "triggers": ["from"]}, "targets"),
    ({"aliases": ["@bot"], "triggers": ["reply", "alias"]}, "targets"),
    ({"aliases": ["@bot"], "allow_own_alias": "sim"}, "allow_own_alias"),
    ({"aliases": ["@bot"], "triggers": ["agent_reply"], "allow_own_alias": True}, "alias"),
    ({"targets": ["me"], "allow_own_alias": True}, "aliases"),
    ({"aliases": ["@bot"], "auto_chats": "grupo"}, "auto_chats"),
    ({"aliases": ["@bot"], "auto_chats": ["Grupo"]}, "auto_chats"),
    ({"aliases": ["@bot"], "auto_chats": [5]}, "auto_chats"),
])
def test_invalid_alias_configuration(conn, fields, fragment):  # ALIAS-04
    profile_, problem = parsed(**fields)

    assert profile_ is None and problem["error_code"] == "MONITOR_CONFIG_INVALID" and fragment in problem["message"]


def test_aliases_are_stored_in_lower_case_and_listed(conn):  # ALIAS-04, ALIAS-12
    config(aliases=["@Wall-E", "@ROBO"], allow_own_alias=True, auto_chats=[GROUP], mode="auto")

    p, _ = monitor.get_profile("p")
    listed = monitor.list_profiles()["profiles"][0]

    assert p.aliases == ["@wall-e", "@robo"]
    assert (listed["aliases"], listed["allow_own_alias"], listed["auto_chats"]) == (["@wall-e", "@robo"], True, [GROUP])
    assert listed["triggers"] == ["alias", "agent_reply"] and "approval_token" not in listed


@pytest.mark.parametrize("fields, triggers", [
    ({"targets": ["me"]}, ["mention", "reply", "direct"]),
    ({"aliases": ["@bot"]}, ["alias", "agent_reply"]),
    ({"targets": ["me"], "aliases": ["@bot"]}, ["mention", "reply", "direct", "alias", "agent_reply"]),
    ({"targets": ["me"], "aliases": ["@bot"], "triggers": ["alias"]}, ["alias"]),
])
def test_default_triggers_follow_what_the_profile_has(conn, fields, triggers):  # ALIAS-05
    p, problem = parsed(**fields)

    assert problem is None and p.triggers == triggers


def test_defaults_of_the_new_fields(conn):  # ALIAS-05
    p, _ = parsed(aliases=["@bot"])

    assert (p.allow_own_alias, p.auto_chats) == (False, [])


# ---- ALIAS-06: responder ao agente ------------------------------------------------------------------------

def agent_message(conn, id="AGENT1", chat=GROUP, minute=50):
    """Uma resposta que o monitor enviou: está no banco como da própria conta e o id está no log de auditoria."""
    add(conn, id, "Resposta do agente", chat=chat, from_me=1, minute=minute)
    put_audit(reply_id=id, chat_jid=chat, ts=(NOW - timedelta(minutes=30)).isoformat())


def test_a_reply_to_a_message_the_agent_sent_fires_agent_reply(conn):  # ALIAS-06
    agent_message(conn)
    add(conn, "R", "obrigado, mas e a segunda parte?", quoted="AGENT1", sender=BOB)
    add(conn, "OTHER", "resposta a outra coisa", quoted="NOT_AGENT", sender=BOB)

    result = run(profile(triggers=["agent_reply"]))

    assert hits(result) == [("R", [("agent_reply", "agent")])]
    assert result.events[0]["command"] is None


def test_agent_reply_needs_the_same_chat(conn):  # ALIAS-06
    agent_message(conn, chat=GROUP)
    add(conn, "R", "cita o id em outro chat", quoted="AGENT1", chat=GROUP2, sender=BOB)

    assert hits(run(profile(triggers=["agent_reply"]))) == []


def test_agent_reply_counts_replies_sent_by_any_profile(conn):  # ALIAS-06
    add(conn, "A2", "do outro perfil", from_me=1, minute=50)
    put_audit(reply_id="A2", profile="outro-perfil", chat_jid=GROUP)
    add(conn, "R", "e agora?", quoted="A2", sender=BOB)

    assert [h[0] for h in hits(run(profile(triggers=["agent_reply"])))] == ["R"]


def test_only_sent_replies_are_agent_messages(conn):  # ALIAS-06
    add(conn, "BLOCKED", "bloqueada", from_me=1, minute=50)
    put_audit(reply_id="BLOCKED", result="blocked", chat_jid=GROUP)
    add(conn, "HUMAN", "eu mesmo escrevi", from_me=1, minute=51)
    add(conn, "R1", "r", quoted="BLOCKED", sender=BOB)
    add(conn, "R2", "r", quoted="HUMAN", sender=BOB)

    assert hits(run(profile(triggers=["agent_reply"]))) == []


def test_agent_reply_without_an_audit_log_just_does_not_fire(conn):  # ALIAS-06
    add(conn, "R", "r", quoted="AGENT1", sender=BOB)

    assert hits(run(profile(triggers=["agent_reply"]))) == []


def test_agent_reply_obeys_the_profile_filters(conn):  # ALIAS-06
    agent_message(conn)
    add(conn, "OK", "continua?", quoted="AGENT1", sender=ANA)
    add(conn, "DENIED", "continua?", quoted="AGENT1", sender=BOB)
    add(conn, "OFFTOPIC", "ok valeu", quoted="AGENT1", sender=ANA)

    assert [h[0] for h in hits(run(profile(triggers=["agent_reply"], senders_deny=[BOB])))] == ["OK", "OFFTOPIC"]
    assert [h[0] for h in hits(run(profile(triggers=["agent_reply"], topics=["continua"], senders_deny=[BOB])))] == ["OK"]


# ---- ALIAS-07/08: a própria conta -------------------------------------------------------------------------

def test_own_messages_never_fire_by_default(conn):  # ALIAS-07
    add(conn, "OWN", "@wall-e eu mesmo chamando", from_me=1)

    assert hits(run()) == []


def test_own_alias_fires_only_the_alias_trigger(conn):  # ALIAS-07
    add(conn, "OWN", f"@wall-e e também @{ME}", from_me=1)
    add(conn, "OWN2", "sem o nome", from_me=1)
    p = profile(targets=["me"], triggers=["alias", "mention", "reply", "direct", "from"], allow_own_alias=True)

    assert hits(run(p)) == [("OWN", [("alias", BOT)])]
    assert run(p).events[0]["command"] == f"e também @{ME}"


def test_own_alias_needs_the_alias_trigger_and_the_option(conn):  # ALIAS-07
    add(conn, "OWN", "@wall-e", from_me=1)

    assert hits(run(profile(allow_own_alias=False))) == []
    assert hits(run(profile(triggers=["agent_reply"], allow_own_alias=True))) == []


def test_a_reply_sent_by_the_monitor_never_fires_even_with_the_alias_in_it(conn):  # ALIAS-08
    add(conn, "AGENT1", "Sou o @wall-e e respondo isso", from_me=1, minute=50)
    put_audit(reply_id="AGENT1", chat_jid=GROUP)
    add(conn, "OWN", "@wall-e agora fui eu", from_me=1, minute=51)

    assert hits(run(profile(allow_own_alias=True))) == [("OWN", [("alias", BOT)])]


def test_a_reply_still_being_recorded_is_waited_for(conn, monkeypatch):  # ALIAS-08
    """O banco já tem a resposta enviada, mas o log ainda não: a varredura espera o fim do envio em andamento."""
    add(conn, "AGENT1", "Sou o @wall-e", from_me=1, minute=50)
    outcome = []

    with monitor._reply_lock:      # um monitor_reply no meio do envio
        thread = threading.Thread(target=lambda: outcome.append(hits(run(profile(allow_own_alias=True)))))
        thread.start()
        time.sleep(0.3)
        assert outcome == [], "a varredura deve esperar o envio terminar"
        put_audit(reply_id="AGENT1", chat_jid=GROUP)      # o envio registra a resposta antes de soltar o lock
    thread.join(5)

    assert outcome == [[]]


def test_the_wait_is_only_for_own_messages_with_the_alias(conn):  # ALIAS-08
    add(conn, "OTHERS", "@wall-e oi", sender=BOB)
    result = {}

    with monitor._reply_lock:
        thread = threading.Thread(target=lambda: result.setdefault("hits", hits(run(profile(allow_own_alias=True)))))
        thread.start()
        thread.join(2)
        assert not thread.is_alive(), "uma mensagem de outra pessoa não deve esperar o envio de outra resposta"

    assert result["hits"] == [("OTHERS", [("alias", BOT)])]


# ---- ALIAS-09: auto_chats ---------------------------------------------------------------------------------

def test_auto_chats_answer_anyone_in_auto_mode(conn):  # ALIAS-09
    add(conn, "G", "@wall-e oi", chat=GROUP, sender=BOB)
    add(conn, "G2", "@wall-e oi", chat=GROUP2, sender=BOB, minute=56)
    add(conn, "T", "@wall-e oi", chat=GROUP2, sender=ANA, minute=57)

    auto = run(profile(mode="auto", auto_chats=[GROUP], auto_senders=[ANA]))
    draft = run(profile(mode="draft", auto_chats=[GROUP], auto_senders=[ANA]))

    assert [e["reply_mode"] for e in auto.events] == ["auto", "draft", "auto"]
    assert {e["reply_mode"] for e in draft.events} == {"draft"}


def test_own_messages_are_auto_in_auto_mode_only(conn):  # ALIAS-09
    add(conn, "OWN", "@wall-e eu", from_me=1)

    assert run(profile(mode="auto", allow_own_alias=True)).events[0]["reply_mode"] == "auto"
    assert run(profile(mode="draft", allow_own_alias=True)).events[0]["reply_mode"] == "draft"


# ---- ALIAS-10: monitor_reply -------------------------------------------------------------------------------

def reply(message_id, chat_jid=GROUP, token=TOKEN, **kwargs):
    return monitor.monitor_reply("p", chat_jid, message_id, "Resposta", approval_token=token, now=NOW, **kwargs)


def test_monitor_reply_accepts_an_alias_message_and_refuses_others(conn, sent):  # ALIAS-10
    add(conn, "CALL", "@wall-e qual o status?", sender=BOB)
    add(conn, "PLAIN", "conversa normal", sender=BOB)
    config()

    assert reply("CALL")["success"] is True
    assert reply("PLAIN")["error_code"] == "NOT_A_TRIGGER"
    assert real_sends(sent)[0]["reply_to"] == "CALL"


def test_monitor_reply_accepts_a_reply_to_the_agent(conn, sent):  # ALIAS-10
    agent_message(conn)
    add(conn, "FOLLOW", "e a segunda parte?", quoted="AGENT1", sender=BOB)
    config()

    assert reply("FOLLOW")["success"] is True


def test_monitor_reply_to_an_own_alias_message_needs_allow_own_alias(conn, sent):  # ALIAS-10
    add(conn, "OWN", "@wall-e eu", from_me=1)
    config()
    assert reply("OWN")["error_code"] == "NOT_A_TRIGGER"
    config(allow_own_alias=True)
    assert reply("OWN")["success"] is True


def test_monitor_reply_never_answers_a_reply_the_monitor_sent(conn, sent):  # ALIAS-10, ALIAS-08
    add(conn, "AGENT1", "Sou o @wall-e", from_me=1, minute=50)
    put_audit(reply_id="AGENT1", chat_jid=GROUP)
    config(allow_own_alias=True)

    assert reply("AGENT1")["error_code"] == "NOT_A_TRIGGER" and real_sends(sent) == []


def test_monitor_reply_auto_chats_and_own_messages_skip_the_token(conn, sent):  # ALIAS-09, ALIAS-10
    add(conn, "CALL", "@wall-e oi", chat=GROUP2, sender=BOB)
    add(conn, "CALL2", "@wall-e oi", chat=GROUP, sender=BOB, minute=56)
    add(conn, "OWN", "@wall-e eu", chat=GROUP, from_me=1, minute=57)
    config(mode="auto", auto_chats=[GROUP2], allow_own_alias=True,
           limits={"cooldown_seconds": 1, "replies_per_chat_per_hour": 20, "replies_per_sender_per_hour": 20})

    assert reply("CALL", chat_jid=GROUP2, token=None)["success"] is True
    assert reply("CALL2", token=None)["error_code"] == "APPROVAL_REQUIRED"
    assert reply("OWN", token=None)["success"] is True


# ---- ALIAS-11: sem perfil ---------------------------------------------------------------------------------

def test_aliases_without_a_profile_only_read(conn):  # ALIAS-11
    add(conn, "CALL", "@wall-e oi", sender=BOB)

    out = asyncio.run(monitor.monitor_messages(aliases=["@wall-e"], cursor=0, now=NOW))

    assert out["success"] is True and out["events"][0]["reply_mode"] == "none"
    assert out["events"][0]["command"] == "oi" and out["events"][0]["matched"] == [{"trigger": "alias", "target": "@wall-e"}]


def test_aliases_and_targets_can_be_mixed_without_a_profile(conn):  # ALIAS-11
    add(conn, "A", f"@{ME} oi", sender=BOB)
    add(conn, "B", "@wall-e oi", sender=BOB, minute=56)

    out = asyncio.run(monitor.monitor_messages(targets=["me"], aliases=["@wall-e"], cursor=0, now=NOW))

    assert [e["message"]["id"] for e in out["events"]] == ["A", "B"]


@pytest.mark.parametrize("kwargs", [
    {"profile": "p", "aliases": ["@wall-e"]}, {"aliases": ["com espaço"]}, {"aliases": "wall-e"}, {"aliases": []},
    {"aliases": ["@bot"], "triggers": ["mention"]},
])
def test_invalid_alias_arguments_without_a_profile(conn, kwargs):  # ALIAS-11
    config()
    out = asyncio.run(monitor.monitor_messages(cursor=0, now=NOW, **kwargs))

    assert out["success"] is False and out["error_code"] == "INVALID_ARGUMENT"


def test_the_tool_documents_the_name_and_the_new_triggers():  # ALIAS-11, ALIAS-12
    doc = main.monitor_messages.__doc__

    for expected in ("aliases", "alias", "agent_reply", "command"):
        assert expected in doc
