"""P1-8 tests: send policy, override_token and audit log."""
import json
import os
import sqlite3
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402
import send_policy  # noqa: E402
import whatsapp  # noqa: E402

CLIENT = "120363111@g.us"          # "Client Beta": client group
TECH = "120363222@g.us"            # "Project Alpha": technical group
TWIN_A, TWIN_B = "120363333@g.us", "120363444@g.us"   # two chats with the same name
LID, PHONE = "100000000000001", "551188880101"
OK_BODY = {"success": True, "message": "Message sent", "message_id": "ID1",
           "timestamp": "2026-10-21T10:30:00-03:00", "chat_jid": TECH}
TOKEN = "owner-secret"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "messages.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);"
        "CREATE TABLE contacts_identity (id INTEGER PRIMARY KEY AUTOINCREMENT, lid TEXT, phone TEXT,"
        " full_name TEXT, push_name TEXT, updated_at TIMESTAMP);"
    )
    conn.executemany("INSERT INTO chats VALUES (?, ?, NULL)", [
        (CLIENT, "Client Beta"), (TECH, "Project Alpha"),
        (TWIN_A, "Café"), (TWIN_B, "café"),
        (f"{LID}@lid", "Alex"),
    ])
    conn.execute("INSERT INTO contacts_identity (lid, phone, full_name) VALUES (?, ?, 'Alex')", (LID, PHONE))
    conn.commit()
    conn.close()
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)
    return path


def write_policy(path_holder, **policy):
    with open(send_policy.POLICY_PATH, "w") as f:
        json.dump(policy, f)


def audit_lines():
    if not os.path.exists(send_policy.AUDIT_PATH):
        return []
    with open(send_policy.AUDIT_PATH) as f:
        return [json.loads(line) for line in f if line.strip()]


def response(status=200, payload=OK_BODY):
    resp = mock.Mock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.text = str(payload)
    return resp


@pytest.fixture
def post():
    with mock.patch("whatsapp.requests.post", return_value=response()) as p:
        yield p


@pytest.fixture
def media_file(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"12345")
    return str(path)


def blocked(out):
    return out.get("success") is False and out.get("error_code") == "SEND_BLOCKED_BY_POLICY"


def test_denylist_blocks_a_denied_group(db, post):  # POLICY-01, 04, 05
    write_policy(None, mode="denylist", deny=["Client Beta"])

    out = main.send_message(CLIENT, "hi")

    assert blocked(out)
    assert "Client Beta" in out["message"] and CLIENT in out["message"]
    assert "deny" in out["message"] and "Nothing was sent" in out["message"]
    post.assert_not_called()


def test_denylist_allows_other_chats(db, post):  # POLICY-06
    write_policy(None, mode="denylist", deny=["Client Beta"])

    out = main.send_message(TECH, "hi")

    assert out == {"success": True, "message": "Message sent", "message_id": "ID1",
                   "timestamp": "2026-10-21T10:30:00-03:00", "chat_jid": TECH}
    post.assert_called_once()


def test_allowlist_blocks_what_is_not_listed(db, post):  # POLICY-02, 04
    write_policy(None, mode="allowlist", allow=[TECH])

    assert blocked(main.send_message(CLIENT, "hi"))
    post.assert_not_called()
    assert main.send_message(TECH, "hi")["success"] is True
    post.assert_called_once()


def test_missing_mode_means_denylist(db, post):  # Assumptions: mode absent
    write_policy(None, deny=[CLIENT])

    assert blocked(main.send_message(CLIENT, "hi"))
    assert main.send_message(TECH, "hi")["success"] is True


def test_deny_wins_over_allow(db, post):  # POLICY-03
    write_policy(None, mode="allowlist", allow=[CLIENT, TECH], deny=[CLIENT])

    assert blocked(main.send_message(CLIENT, "hi"))
    assert main.send_message(TECH, "hi")["success"] is True


def test_no_policy_file_allows_everything(db, post):  # POLICY-07
    assert main.send_message(CLIENT, "hi")["success"] is True


@pytest.mark.parametrize("content", [
    "{not json", '{"mode": "block-everything"}', '{"deny": "Client Beta"}',
    '{"allow": [1, 2]}', '["list"]',
])
def test_invalid_policy_blocks_every_send(db, post, content):  # POLICY-08
    with open(send_policy.POLICY_PATH, "w") as f:
        f.write(content)

    out = main.send_message(TECH, "hi")

    assert blocked(out)
    assert "policy" in out["message"].lower()
    post.assert_not_called()


@pytest.mark.parametrize("tool", ["send_message", "send_file", "send_audio_message"])
def test_policy_applies_to_every_send_tool(db, post, media_file, tool, tmp_path):  # POLICY-09
    write_policy(None, mode="denylist", deny=[CLIENT])
    args = {"send_message": ("hi",), "send_file": (media_file,)}.get(tool)
    if tool == "send_audio_message":
        ogg = str(tmp_path / "a.ogg")
        open(ogg, "wb").write(b"x")
        args = (ogg,)

    out = getattr(main, tool)(CLIENT, *args)

    assert blocked(out)
    post.assert_not_called()
    assert getattr(main, tool)(TECH, *args)["success"] is True


def test_jid_rule_matches_only_that_jid(db, post):  # POLICY-10
    write_policy(None, mode="denylist", deny=[CLIENT])

    assert blocked(main.send_message(CLIENT, "hi"))
    assert main.send_message(TECH, "hi")["success"] is True


def test_name_rule_is_case_insensitive(db, post):  # POLICY-11
    write_policy(None, mode="denylist", deny=["CLIENT beta"])

    assert blocked(main.send_message(CLIENT, "hi"))


@pytest.mark.parametrize("rule, target", [
    (PHONE, f"{LID}@lid"), (f"{PHONE}@s.whatsapp.net", LID), (LID, PHONE), (f"{LID}@lid", f"{PHONE}@s.whatsapp.net"),
])
def test_phone_and_lid_of_the_same_person_are_the_same_destination(db, post, rule, target):  # POLICY-12
    write_policy(None, mode="denylist", deny=[rule])

    assert blocked(main.send_message(target, "hi"))
    post.assert_not_called()


def test_unrelated_person_is_not_matched_by_identity(db, post):  # POLICY-12 (no false positive)
    write_policy(None, mode="denylist", deny=[PHONE])

    assert main.send_message("5511888880000", "hi")["success"] is True


def test_ambiguous_name_denies_all_and_allows_none(db, post):  # POLICY-13
    write_policy(None, mode="denylist", deny=["café"])
    assert blocked(main.send_message(TWIN_A, "hi")) and blocked(main.send_message(TWIN_B, "hi"))

    write_policy(None, mode="allowlist", allow=["café"])
    assert blocked(main.send_message(TWIN_A, "hi")) and blocked(main.send_message(TWIN_B, "hi"))
    post.assert_not_called()


def test_recipient_name_resolves_to_the_chat(db, post):  # POLICY-14
    out = main.send_message("project alpha", "hi")

    assert out["success"] is True
    assert post.call_args.kwargs["json"]["recipient"] == TECH


def test_recipient_name_is_checked_against_the_policy_by_its_jid(db, post):  # POLICY-14 + 01
    write_policy(None, mode="denylist", deny=[CLIENT])

    assert blocked(main.send_message("Client Beta", "hi"))
    post.assert_not_called()


def test_unknown_or_ambiguous_recipient_name_is_refused(db, post):  # POLICY-15
    unknown = main.send_message("Group that does not exist", "hi")
    twin = main.send_message("Café", "hi")

    assert unknown["success"] is False and unknown["error_code"] == "RECIPIENT_NOT_FOUND"
    assert twin["success"] is False and twin["error_code"] == "RECIPIENT_AMBIGUOUS"
    assert TWIN_A in twin["message"] and TWIN_B in twin["message"]
    post.assert_not_called()


def test_phone_numbers_and_jids_are_not_treated_as_names(db, post):  # POLICY-14 (compatibility)
    assert main.send_message("5511999990000", "hi")["success"] is True
    assert post.call_args.kwargs["json"]["recipient"] == "5511999990000"
    assert main.send_message("+55 (11) 99999-0000", "hi")["success"] is True
    assert main.send_message("999@g.us", "hi")["success"] is True
    assert post.call_args.kwargs["json"]["recipient"] == "999@g.us"


def test_override_token_releases_a_blocked_send(db, post):  # POLICY-16, 19
    write_policy(None, mode="denylist", deny=[CLIENT], override_token=TOKEN)

    out = main.send_message(CLIENT, "hi", override_token=TOKEN)

    assert out["success"] is True
    post.assert_called_once()
    assert audit_lines()[-1]["override"] is True and audit_lines()[-1]["result"] == "sent"


@pytest.mark.parametrize("token, configured", [("wrong", TOKEN), (TOKEN, None), ("", TOKEN), (None, TOKEN)])
def test_wrong_or_missing_token_keeps_the_block(db, post, token, configured):  # POLICY-17
    policy = {"mode": "denylist", "deny": [CLIENT]}
    if configured:
        policy["override_token"] = configured
    write_policy(None, **policy)

    out = main.send_message(CLIENT, "hi", override_token=token)

    assert blocked(out)
    post.assert_not_called()


def test_token_never_appears_in_log_or_response(db, post):  # POLICY-18
    write_policy(None, mode="denylist", deny=[CLIENT], override_token=TOKEN)

    for attempt in (TOKEN, "wrong-guess"):
        out = main.send_message(CLIENT, "hi", override_token=attempt)
        assert TOKEN not in json.dumps(out) and "wrong-guess" not in json.dumps(out)
    raw = open(send_policy.AUDIT_PATH).read()
    assert TOKEN not in raw and "wrong-guess" not in raw


def test_token_does_not_unlock_an_invalid_policy(db, post):  # POLICY-20
    with open(send_policy.POLICY_PATH, "w") as f:
        f.write('{"mode": "x", "override_token": "%s"}' % TOKEN)

    assert blocked(main.send_message(TECH, "hi", override_token=TOKEN))
    post.assert_not_called()


def test_audit_records_sent_blocked_and_failed(db, post):  # POLICY-21
    write_policy(None, mode="denylist", deny=[CLIENT])
    main.send_message(TECH, "hello")
    main.send_message(CLIENT, "forbidden")
    post.return_value = response(500, {"success": False, "message": "boom"})
    main.send_message(TECH, "will fail")

    sent, blk, failed = audit_lines()

    assert (sent["result"], blk["result"], failed["result"]) == ("sent", "blocked", "failed")
    assert sent["kind"] == "message" and sent["recipient"] == TECH and sent["chat_jid"] == TECH
    assert sent["chat_name"] == "Project Alpha" and sent["message_id"] == "ID1" and sent["override"] is False
    assert blk["chat_name"] == "Client Beta" and "deny" in blk["rule"] and "message_id" not in blk
    assert "message_id" not in failed
    assert all(line["ts"] for line in (sent, blk, failed))


def test_audit_kind_and_size_for_files(db, post, media_file):  # POLICY-21, 22
    main.send_file(TECH, media_file)

    [line] = audit_lines()

    assert line["kind"] == "file" and line["size"] == 5


def test_audit_never_stores_the_message_text(db, post):  # POLICY-22
    main.send_message(TECH, "secret-client-text")

    [line] = audit_lines()

    assert "secret-client" not in json.dumps(line)
    assert line["size"] == len("secret-client-text")


def test_audit_failure_does_not_stop_the_send(db, post, monkeypatch, caplog):  # POLICY-23
    monkeypatch.setattr(send_policy, "AUDIT_PATH", os.path.join(os.path.dirname(send_policy.POLICY_PATH), "no", "such", "log"))

    with caplog.at_level("WARNING"):
        out = main.send_message(TECH, "hi")

    assert out["success"] is True
    assert any("audit" in r.message.lower() for r in caplog.records)


def test_policy_is_read_on_every_send(db, post):  # POLICY-24
    assert main.send_message(CLIENT, "hi")["success"] is True
    write_policy(None, mode="denylist", deny=[CLIENT])
    assert blocked(main.send_message(CLIENT, "hi"))
    os.remove(send_policy.POLICY_PATH)
    assert main.send_message(CLIENT, "hi")["success"] is True


def test_empty_recipient_is_still_rejected_without_a_log_of_a_destination(db, post):  # compatibilidade
    out = main.send_message("", "hi")

    assert out["success"] is False
    post.assert_not_called()


# --- fixes from the verification and the security review ---------------------------------------

@pytest.mark.parametrize("sneaky", [
    f"{CLIENT}@s.whatsapp.net",        # whatsmeow only reads the first two parts of the split on @
    "120363111:7@g.us",                # device suffix
    "120363111.0:7@g.us",              # agent + device
    "120363111@G.US",                  # uppercase server
    f"  {CLIENT}  ",                   # spaces
])
def test_jid_variants_cannot_bypass_a_deny_rule(db, post, sneaky):  # POLICY-25
    write_policy(None, mode="denylist", deny=[CLIENT])

    out = main.send_message(sneaky, "hi")

    assert out["success"] is False
    assert out["error_code"] in ("SEND_BLOCKED_BY_POLICY", "RECIPIENT_INVALID")
    post.assert_not_called()


def test_equivalent_jid_forms_are_blocked_as_the_same_chat(db, post):  # POLICY-25
    write_policy(None, mode="denylist", deny=[CLIENT])

    for variant in ("120363111:7@g.us", "120363111@G.US", f"  {CLIENT}  "):
        assert blocked(main.send_message(variant, "hi")), variant


@pytest.mark.parametrize("bad", ["a@b@g.us", "120363111@c.us", "status@broadcast", "@g.us", "abc@g.us", "123@s.whatsapp.net\n@g.us"])
def test_invalid_destinations_are_refused_before_the_bridge(db, post, bad):  # POLICY-26
    out = main.send_message(bad, "hi")

    assert out["success"] is False and out["error_code"] == "RECIPIENT_INVALID"
    post.assert_not_called()


def test_the_bridge_receives_the_canonical_jid(db, post):  # POLICY-27
    main.send_message("120363222:3@G.US", "hi")

    assert post.call_args.kwargs["json"]["recipient"] == TECH


@pytest.mark.parametrize("entry", [" 120363111@g.us ", "120363111@G.US", "120363111:4@g.us"])
def test_jid_rule_written_in_another_form_still_matches(db, post, entry):  # POLICY-28
    write_policy(None, mode="denylist", deny=[entry])

    assert blocked(main.send_message(CLIENT, "hi"))


@pytest.mark.parametrize("entry", ["a@b@g.us", "120363111@c.us", "text@g.us"])
def test_malformed_jid_rule_invalidates_the_policy(db, post, entry):  # POLICY-28
    write_policy(None, mode="denylist", deny=[entry])

    out = main.send_message(TECH, "hi")

    assert blocked(out) and "not a valid JID" in out["message"]
    post.assert_not_called()


def test_group_and_person_with_the_same_digits_are_different_destinations(db, post):  # POLICY-25 (no false positive)
    write_policy(None, mode="denylist", deny=[CLIENT])

    assert main.send_message("120363111@s.whatsapp.net", "hi")["success"] is True


def test_unreadable_directory_blocks_even_with_a_token(db, post, monkeypatch, tmp_path):  # POLICY-29
    write_policy(None, mode="denylist", deny=["Client Beta"], override_token=TOKEN)
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", str(tmp_path / "absent.db"))

    out = main.send_message(TECH, "hi", override_token=TOKEN)

    assert blocked(out) and "could not be read" in out["message"]
    post.assert_not_called()
    assert not (tmp_path / "absent.db").exists()  # never creates an empty database


def test_unreadable_directory_does_not_block_when_there_is_no_policy(tmp_path, monkeypatch, post):  # POLICY-07
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", str(tmp_path / "absent.db"))

    assert main.send_message("5511999990000", "hi")["success"] is True


def test_name_recipient_with_unreadable_directory_is_refused(tmp_path, monkeypatch, post):  # POLICY-29
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", str(tmp_path / "absent.db"))

    out = main.send_message("Project Alpha", "hi")

    assert out["success"] is False and out["error_code"] == "DIRECTORY_UNAVAILABLE"
    post.assert_not_called()


def test_deny_by_name_still_blocks_a_jid_whose_chat_has_that_name(db, post):  # POLICY-30
    write_policy(None, mode="denylist", deny=["Client Beta"])

    assert blocked(main.send_message(CLIENT, "hi"))


def test_unknown_group_cannot_be_checked_against_name_rules(db, post):  # POLICY-30
    write_policy(None, mode="denylist", deny=["Client Beta"])

    out = main.send_message("120363777@g.us", "hi")

    assert blocked(out) and "not in the chat list" in out["message"]
    post.assert_not_called()


def test_unknown_group_is_fine_when_rules_are_only_jids(db, post):  # POLICY-30
    write_policy(None, mode="denylist", deny=[CLIENT])

    assert main.send_message("120363777@g.us", "hi")["success"] is True


def test_unknown_group_can_be_released_with_the_token(db, post):  # POLICY-30
    write_policy(None, mode="denylist", deny=["Client Beta"], override_token=TOKEN)

    assert main.send_message("120363777@g.us", "hi", override_token=TOKEN)["success"] is True


def test_block_message_names_the_chat_apart_from_the_rule(db, post):  # POLICY-05
    write_policy(None, mode="denylist", deny=[CLIENT])

    out = main.send_message(CLIENT, "hi")

    assert 'chat "Client Beta" (120363111@g.us)' in out["message"]
    assert f'deny rule "{CLIENT}"' in out["message"]


def test_blocked_audio_is_not_converted(db, post, tmp_path):  # POLICY-09
    write_policy(None, mode="denylist", deny=[CLIENT])
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"x")

    with mock.patch("whatsapp.audio.convert_to_opus_ogg_temp") as convert:
        out = main.send_audio_message(CLIENT, str(mp3))

    assert blocked(out)
    convert.assert_not_called()
    post.assert_not_called()


def test_refused_recipients_are_audited(db, post):  # POLICY-21
    main.send_message("Group that does not exist", "hi")
    main.send_message("Café", "hi")
    main.send_message("a@b@g.us", "hi")

    rules = [(l["result"], l["rule"]) for l in audit_lines()]

    assert rules == [("failed", "RECIPIENT_NOT_FOUND"), ("failed", "RECIPIENT_AMBIGUOUS"), ("failed", "RECIPIENT_INVALID")]


def test_recipient_with_surrounding_spaces_is_trimmed(db, post):  # POLICY-14
    assert main.send_message("  Project Alpha  ", "hi")["success"] is True
    assert post.call_args.kwargs["json"]["recipient"] == TECH


def test_audit_line_is_one_json_line_and_bounded(db, post):  # POLICY-22
    main.send_message("x" * 5000 + "\nfake line", "hi")

    lines = open(send_policy.AUDIT_PATH).read().splitlines()

    assert len(lines) == 1 and len(json.loads(lines[0])["recipient"]) <= 200


def test_fullwidth_digits_are_not_a_phone_number(db, post):  # POLICY-26
    write_policy(None, mode="denylist", deny=[PHONE])

    out = main.send_message("５５５１８１９９３２７１", "hi")

    assert out["success"] is False and out["error_code"] == "RECIPIENT_NOT_FOUND"
    post.assert_not_called()


def test_deny_rule_in_another_jid_form_is_the_reason_of_the_block(db, post):  # POLICY-28
    write_policy(None, mode="denylist", deny=["120363111@G.US"])

    out = main.send_message(CLIENT, "hi")

    assert blocked(out) and 'deny rule "120363111@G.US"' in out["message"]


def test_jid_only_rules_are_blocked_without_a_readable_directory(db, post, monkeypatch, tmp_path):  # POLICY-29
    write_policy(None, mode="denylist", deny=[CLIENT])
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", str(tmp_path / "absent.db"))

    assert blocked(main.send_message(CLIENT, "hi"))
    assert blocked(main.send_message(TECH, "hi"))
    post.assert_not_called()


def test_unknown_person_is_not_blocked_by_name_rules(db, post):  # POLICY-30 (groups only)
    write_policy(None, mode="denylist", deny=["Client Beta"])

    assert main.send_message("5511888880000", "hi")["success"] is True


def test_hyphenated_user_is_only_valid_for_groups(db, post):  # POLICY-26
    out = main.send_message("5511999-1600000000@s.whatsapp.net", "hi")

    assert out["success"] is False and out["error_code"] == "RECIPIENT_INVALID"
    assert main.send_message("5511999-1600000000@g.us", "hi")["success"] is True


def test_audit_bounds_chat_name_and_invalid_recipient_message(db, post, monkeypatch):  # POLICY-22
    out = main.send_message("a@b@" + "x" * 5000, "hi")

    assert len(out["message"]) < 600


@pytest.mark.parametrize("placeholder", ["replace-with-your-own-value", "troque-por-um-valor-so-seu"])
def test_a_placeholder_override_token_never_releases_a_blocked_send(db, post, placeholder):  # POLICY-16
    # the value copied from an example file is public: sending it back must not release a blocked send
    write_policy(None, mode="denylist", deny=[CLIENT], override_token=placeholder)

    out = main.send_message(CLIENT, "hi", override_token=placeholder)

    assert out["success"] is False and out["error_code"] == send_policy.BLOCKED
    post.assert_not_called()
