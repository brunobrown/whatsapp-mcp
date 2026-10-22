"""Testes do monitor, parte 2: varredura e gatilhos (MON-01..12, MON-14)."""
import os
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import monitor  # noqa: E402
import send_policy  # noqa: E402
import whatsapp  # noqa: E402

ME, ME_LID = "558181721592", "191134835990646"
ANA, ANA_LID = "5511999990000", "207571524796601"
BOB = "5511888880000"
GROUP, GROUP2, DM_ANA = "120363000000000001@g.us", "120363000000000002@g.us", f"{ANA}@s.whatsapp.net"
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)

SCHEMA = """
    CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);
    CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,
        is_from_me BOOLEAN, media_type TEXT, filename TEXT, url TEXT, media_key BLOB, file_sha256 BLOB,
        file_enc_sha256 BLOB, file_length INTEGER, local_path TEXT{extra}, PRIMARY KEY (id, chat_jid));
    CREATE TABLE contacts_identity (id INTEGER PRIMARY KEY, lid TEXT UNIQUE, phone TEXT UNIQUE,
        full_name TEXT, push_name TEXT);
    INSERT INTO contacts_identity (lid, phone, full_name) VALUES ('191134835990646', '558181721592', 'Eu');
    INSERT INTO contacts_identity (lid, phone, full_name) VALUES ('207571524796601', '5511999990000', 'Ana');
    INSERT INTO chats VALUES ('120363000000000001@g.us', 'Grupo 1', NULL), ('120363000000000002@g.us', 'Grupo 2', NULL),
        ('5511999990000@s.whatsapp.net', 'Ana', NULL), ('5511888880000@s.whatsapp.net', 'Bob', NULL);"""
EXTRA = ", mimetype TEXT, forwarded BOOLEAN, quoted_id TEXT"


def make(tmp_path, monkeypatch, extra=EXTRA):
    path = str(tmp_path / "messages.db")
    c = sqlite3.connect(path)
    c.executescript(SCHEMA.format(extra=extra))
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)
    monkeypatch.setattr(monitor, "own_number", lambda: ME)
    return c


@pytest.fixture
def conn(tmp_path, monkeypatch):
    return make(tmp_path, monkeypatch)


def add(conn, id, content="oi", chat=GROUP, sender=ANA, minute=55, quoted=None, from_me=0, **cols):
    cols = {**cols, "id": id, "chat_jid": chat, "sender": ME if from_me else sender, "content": content,
            "timestamp": f"2026-10-21 11:{minute:02d}:00+00:00", "is_from_me": from_me, "quoted_id": quoted}
    conn.execute(f"INSERT INTO messages ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))
    conn.commit()


def profile(**fields):
    fields.setdefault("targets", ["me"])
    return monitor.Profile(name="p", **fields)


def run(profile_=None, cursor=0, limit=20, adhoc=False):
    return monitor.scan(profile_ or profile(), cursor, limit, adhoc=adhoc, now=NOW)


def hits(result):
    return [(e["message"]["id"], [(m["trigger"], m["target"]) for m in e["matched"]]) for e in result.events]


def test_current_cursor_is_the_newest_position(conn):  # MON-01
    assert monitor.current_cursor() == 0
    add(conn, "A")
    add(conn, "B")
    assert monitor.current_cursor() == 2


def test_current_cursor_of_a_missing_database_is_an_error_and_creates_nothing(tmp_path, monkeypatch):  # MON-14
    missing = tmp_path / "none.db"
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", str(missing))

    with pytest.raises(send_policy.DirectoryError):
        monitor.current_cursor()
    assert not missing.exists()


@pytest.mark.parametrize("text", [f"@{ME} pode ver?", f"@{ME_LID} pode ver?", f"oi (@{ME}), tudo bem", f"@{ME}"])
def test_mention_of_the_account_by_phone_or_lid(conn, text):  # MON-04
    add(conn, "M", text)

    assert hits(run()) == [("M", [("mention", "me")])]


@pytest.mark.parametrize("text", [f"escreva a ana@{ME}.com", f"@@{ME}", f"@{ME}9", "@5511999990000 olá", "sem menção"])
def test_text_that_does_not_mention_the_account(conn, text):  # MON-04
    add(conn, "M", text)

    assert hits(run()) == []


def test_mention_of_another_number_by_phone_or_lid(conn):  # MON-04
    add(conn, "P", f"@{ANA} e @{BOB}")
    add(conn, "L", f"@{ANA_LID} veja isto")
    add(conn, "N", "ninguém")

    out = hits(run(profile(targets=[ANA])))

    assert out == [("P", [("mention", ANA)]), ("L", [("mention", ANA)])]


def test_many_targets_report_each_one_that_matched(conn):  # MON-04
    add(conn, "M", f"@{ME} @{BOB}")

    assert hits(run(profile(targets=["me", ANA, BOB]))) == [("M", [("mention", "me"), ("mention", BOB)])]


def test_reply_to_a_message_sent_by_the_account(conn):  # MON-05
    add(conn, "MINE", "pergunta", from_me=1, minute=50)
    add(conn, "R", "resposta", quoted="MINE")
    add(conn, "OTHERS", "outra", sender=BOB, minute=51)
    add(conn, "NOT", "não é comigo", quoted="OTHERS", minute=56)

    assert hits(run()) == [("R", [("reply", "me")])]


def test_reply_to_a_message_of_another_target(conn):  # MON-05
    add(conn, "HERS", "dela", sender=ANA, minute=50)
    add(conn, "R", "resposta", sender=BOB, quoted="HERS")
    add(conn, "UNREL", "x", sender=BOB, minute=52)
    add(conn, "R2", "resposta a outra", sender=BOB, quoted="UNREL")

    assert hits(run(profile(targets=[ANA_LID]))) == [("R", [("reply", ANA_LID)])]


def test_reply_only_counts_a_quote_in_the_same_chat(conn):  # MON-05
    add(conn, "SAME", "minha em outro chat", from_me=1, chat=GROUP2, minute=50)
    add(conn, "R", "cita id de outro chat", chat=GROUP, quoted="SAME")

    assert hits(run()) == []


def test_direct_messages(conn):  # MON-06
    add(conn, "DM", "oi, tem um minuto?", chat=DM_ANA, sender=ANA)
    add(conn, "GRP", "no grupo", chat=GROUP, sender=ANA)
    add(conn, "DM2", "outra pessoa", chat=f"{BOB}@s.whatsapp.net", sender=BOB)

    assert hits(run()) == [("DM", [("direct", "me")]), ("DM2", [("direct", "me")])]


def test_direct_of_another_target_is_only_their_own_chat(conn):  # MON-06
    add(conn, "WITH_ANA", "oi", chat=DM_ANA, sender=ANA)
    add(conn, "WITH_BOB", "oi", chat=f"{BOB}@s.whatsapp.net", sender=BOB)

    assert hits(run(profile(targets=[ANA_LID]))) == [("WITH_ANA", [("direct", ANA_LID)])]


def test_from_is_only_for_other_numbers_and_only_when_asked(conn):  # MON-07
    add(conn, "G", "no grupo", chat=GROUP, sender=ANA)
    add(conn, "D", "no privado", chat=DM_ANA, sender=ANA)
    add(conn, "OTHER", "de outro", sender=BOB)

    default = hits(run(profile(targets=[ANA])))
    asked = hits(run(profile(targets=[ANA, "me"], triggers=["from"])))

    assert default == [("D", [("direct", ANA)])]
    assert asked == [("G", [("from", ANA)]), ("D", [("from", ANA)])]


def test_from_matches_the_lid_of_the_sender(conn):  # MON-07
    add(conn, "G", "no grupo", sender=ANA_LID)

    assert hits(run(profile(targets=[ANA], triggers=["from"]))) == [("G", [("from", ANA)])]


def test_triggers_not_listed_do_not_fire(conn):  # MON-04..07
    add(conn, "MINE", "x", from_me=1, minute=50)
    add(conn, "M", f"@{ME}")
    add(conn, "R", "r", quoted="MINE")
    add(conn, "D", "d", chat=DM_ANA)

    assert [h[0] for h in hits(run(profile(triggers=["mention"])))] == ["M"]
    assert [h[0] for h in hits(run(profile(triggers=["reply"])))] == ["R"]
    assert [h[0] for h in hits(run(profile(triggers=["direct"])))] == ["D"]


@pytest.mark.parametrize("fields", [{}, {"targets": ["me", ANA, BOB]}, {"triggers": ["mention", "reply", "direct", "from"]}])
def test_messages_sent_by_the_account_never_fire(conn, fields):  # MON-08
    add(conn, "SELF1", f"@{ME} me marquei", from_me=1)
    add(conn, "SELF2", "no privado", chat=DM_ANA, from_me=1)
    add(conn, "THEIRS", "x", minute=50)
    add(conn, "SELF3", "me respondi", from_me=1, quoted="THEIRS")

    assert hits(run(profile(**fields))) == []


def test_old_messages_are_dropped_but_the_cursor_moves_on(conn):  # MON-09
    add(conn, "OLD", f"@{ME}", minute=0)           # vira uma mensagem de ontem, como o histórico reenviado
    conn.execute("UPDATE messages SET timestamp = '2026-10-20 08:00:00+00:00' WHERE id = 'OLD'")
    add(conn, "FRESH", f"@{ME}", minute=59)
    add(conn, "EDGE", f"@{ME}", minute=0)          # exatamente 60 min antes de NOW: ainda vale

    result = run()

    assert hits(result) == [("FRESH", [("mention", "me")]), ("EDGE", [("mention", "me")])]
    assert result.cursor == 3 and result.has_more is False
    assert hits(run(profile(max_event_age_minutes=30))) == [("FRESH", [("mention", "me")])]


def test_only_messages_after_the_cursor(conn):  # MON-02
    add(conn, "A", f"@{ME}")
    add(conn, "B", f"@{ME}")
    add(conn, "C", f"@{ME}")

    assert [h[0] for h in hits(run(cursor=1))] == ["B", "C"]
    first = run(limit=1)
    assert hits(first) == [("A", [("mention", "me")])] and first.cursor == 1 and first.has_more is True
    assert run(cursor=first.cursor).cursor == 3
    assert hits(run(cursor=3)) == [] and run(cursor=3).cursor == 3


def test_pages_neither_lose_nor_repeat_events(conn):  # MON-11
    for n in range(7):
        add(conn, f"M{n}", f"@{ME}" if n % 2 == 0 else "ruído", minute=50 + n)

    seen, cursor, pages = [], 0, 0
    while True:
        result = run(cursor=cursor, limit=2)
        seen += [h[0] for h in hits(result)]
        cursor, pages = result.cursor, pages + 1
        if not result.has_more:
            break

    assert seen == ["M0", "M2", "M4", "M6"] and pages >= 2


def test_cursor_stops_right_after_the_last_event_of_a_full_page(conn):  # MON-11
    add(conn, "A", f"@{ME}")
    add(conn, "NOISE", "ruído")
    add(conn, "B", f"@{ME}")

    page = run(limit=1)

    assert page.cursor == 1 and page.has_more is True  # NOISE ainda não foi lido
    assert [h[0] for h in hits(run(cursor=page.cursor, limit=1))] == ["B"]


def test_scan_reads_more_than_one_batch(conn, monkeypatch):  # MON-02
    monkeypatch.setattr(monitor, "_SCAN_BATCH", 3)
    for n in range(10):
        add(conn, f"N{n}", "ruído", minute=40 + n)
    add(conn, "HIT", f"@{ME}", minute=55)

    result = run()

    assert hits(result) == [("HIT", [("mention", "me")])] and result.cursor == 11


def test_filters_chats_senders_and_topics(conn):  # MON-10
    add(conn, "G1", f"@{ME} deploy quebrou", chat=GROUP, sender=ANA)
    add(conn, "G2", f"@{ME} deploy quebrou", chat=GROUP2, sender=ANA)
    add(conn, "B1", f"@{ME} DEPLOY de novo", chat=GROUP, sender=BOB)
    add(conn, "T", f"@{ME} outro assunto", chat=GROUP, sender=ANA)

    def ids(**fields):
        return [h[0] for h in hits(run(profile(**fields)))]

    assert ids(chats=[GROUP2]) == ["G2"]
    assert ids(topics=["deploy"]) == ["G1", "G2", "B1"]
    assert ids(topics=["login", "Deploy".casefold()]) == ["G1", "G2", "B1"]
    assert ids(senders_allow=[ANA]) == ["G1", "G2", "T"]
    assert ids(senders_allow=[ANA_LID]) == ["G1", "G2", "T"]          # o mesmo número como LID
    assert ids(senders_deny=[BOB]) == ["G1", "G2", "T"]
    assert ids(senders_allow=[ANA, BOB], senders_deny=[BOB]) == ["G1", "G2", "T"]   # deny vence allow
    assert ids(chats=[GROUP], senders_allow=[ANA], topics=["deploy"]) == ["G1"]       # todos juntos
    assert ids(topics=["inexistente"]) == []


def test_topics_do_not_match_a_media_without_caption(conn):  # MON-10
    add(conn, "IMG", "", chat=DM_ANA, media_type="image")

    assert hits(run(profile(topics=["deploy"]))) == []
    assert [h[0] for h in hits(run(profile()))] == ["IMG"]


def test_event_shape_and_reply_mode(conn):  # MON-12
    add(conn, "MINE", "pergunta", from_me=1, minute=50)
    add(conn, "R", f"@{ME} resposta", quoted="MINE", sender=ANA)
    add(conn, "R2", f"@{ME} outra", sender=BOB)

    auto = run(profile(mode="auto", auto_senders=[ANA_LID]))
    draft = run(profile())
    adhoc = run(adhoc=True)

    event = auto.events[0]
    assert event["matched"] == [{"trigger": "mention", "target": "me"}, {"trigger": "reply", "target": "me"}]
    msg = event["message"]
    assert msg["id"] == "R" and msg["reply_to"]["id"] == "MINE" and msg["reply_to"]["found"] is True
    assert msg["reply_to"]["content"] == "pergunta" and msg["is_target"] is False and "media" in msg
    assert [e["reply_mode"] for e in auto.events] == ["auto", "draft"]     # BOB não é de confiança
    assert {e["reply_mode"] for e in draft.events} == {"draft"}
    assert {e["reply_mode"] for e in adhoc.events} == {"none"}


def test_events_of_several_chats_keep_their_own_threads(conn):  # MON-12
    add(conn, "SAME", "minha no grupo 1", from_me=1, chat=GROUP, minute=50)
    add(conn, "SAME", "minha no grupo 2", from_me=1, chat=GROUP2, minute=50)
    add(conn, "R1", f"@{ME}", chat=GROUP, quoted="SAME")
    add(conn, "R2", f"@{ME}", chat=GROUP2, quoted="SAME")

    quoted = {e["message"]["chat_jid"]: e["message"]["reply_to"]["content"] for e in run().events}

    assert quoted == {GROUP: "minha no grupo 1", GROUP2: "minha no grupo 2"}


def test_old_database_without_quoted_id_still_scans(tmp_path, monkeypatch):  # MON-02
    c = make(tmp_path, monkeypatch, extra="")
    c.execute("INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me) "
              "VALUES ('M', ?, ?, ?, '2026-10-21 11:55:00+00:00', 0)", (GROUP, ANA, f"@{ME} oi"))
    c.commit()

    result = run()

    assert hits(result) == [("M", [("mention", "me")])] and result.events[0]["message"]["reply_to"] is None


def test_own_number_comes_from_the_environment_first(monkeypatch):  # MON-14
    monkeypatch.setenv("WHATSAPP_OWN_NUMBER", "+55 81 8172-1592")
    monkeypatch.setattr(whatsapp, "get_connection_status", lambda: pytest.fail("the bridge must not be asked"))

    assert monitor.own_number() == "558181721592"


def test_own_number_from_the_bridge_and_remembered(monkeypatch):  # MON-14
    monkeypatch.delenv("WHATSAPP_OWN_NUMBER", raising=False)
    monitor._own_cache.clear()
    monkeypatch.setattr(whatsapp, "get_connection_status", lambda: {"phone": "558181721592", "connected": True})
    assert monitor.own_number() == "558181721592"

    monkeypatch.setattr(whatsapp, "get_connection_status", lambda: {"connected": False, "error_code": "BRIDGE_UNREACHABLE"})
    assert monitor.own_number() == "558181721592"   # a última conhecida
    monitor._own_cache.clear()


def test_own_number_unknown(monkeypatch):  # MON-14
    monkeypatch.delenv("WHATSAPP_OWN_NUMBER", raising=False)
    monitor._own_cache.clear()
    monkeypatch.setattr(whatsapp, "get_connection_status", lambda: {"connected": False, "error_code": "BRIDGE_UNREACHABLE"})

    with pytest.raises(monitor.OwnAccountUnknown):
        monitor.own_number()


def test_a_scan_without_me_does_not_need_the_account_number(conn, monkeypatch):  # MON-14
    monkeypatch.setattr(monitor, "own_number", lambda: pytest.fail("not needed"))
    add(conn, "M", f"@{ANA}")

    assert hits(run(profile(targets=[ANA]))) == [("M", [("mention", ANA)])]


def test_normalize_number():
    assert monitor.normalize_number("+55 (81) 98172-1592") == "5581981721592"
    assert monitor.normalize_number("207571524796601@lid") == "207571524796601"
    assert monitor.normalize_number("5511999990000@s.whatsapp.net") == "5511999990000"
    assert monitor.normalize_number("5511999990000:7@s.whatsapp.net") == "5511999990000"
    for bad in ("", "abc", "123456", "12345678901234567890123", "1203@g.us", "٥٥١١٩٩٩٩٩٠٠٠٠", None, 55119999):
        assert monitor.normalize_number(bad) is None


UNKNOWN_LID = "999888777666555"   # um LID que contacts_identity não conhece


def test_deny_list_fails_closed_for_a_sender_that_cannot_be_matched(conn):  # MON-10 (revisão de segurança)
    add(conn, "UNKNOWN", f"@{ME} oi", sender=UNKNOWN_LID)          # remetente sem mapeamento: pode ser o bloqueado
    add(conn, "KNOWN", f"@{ME} oi", sender=ANA_LID)                # mapeado para a Ana: dá para comparar
    add(conn, "PHONE", f"@{ME} oi", sender=BOB)                    # telefone: o bloqueio por telefone o alcança

    ids = lambda **f: [h[0] for h in hits(run(profile(**f)))]  # noqa: E731

    assert ids(senders_deny=[BOB]) == ["KNOWN"]        # UNKNOWN é pulado; BOB é o bloqueado
    assert ids(senders_deny=[ANA]) == ["UNKNOWN", "PHONE"]   # a Ana tem telefone e LID: o desconhecido é outra pessoa
    assert ids() == ["UNKNOWN", "KNOWN", "PHONE"]      # sem lista de bloqueio nada é pulado


def test_allow_list_never_lets_an_unknown_sender_in(conn):  # MON-10
    add(conn, "UNKNOWN", f"@{ME} oi", sender=UNKNOWN_LID)

    assert hits(run(profile(senders_allow=[ANA]))) == []
