"""Database section (CFG-01, CFG-10): read-only facts about the encrypted databases and the key location."""
import os

import pytest

import dbcrypto
import localconfig
import whatsapp
from ui.config.form import FormFlow
from ui.config.registry import ReadOnly, Registry
from ui.config.store import ConfigStore
from ui.sections import database as section_module

SECTION = section_module.SECTION
KEY = "ab12" * 16                       # a made-up master key, 64 hex characters


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", str(store_dir / "messages.db"))
    monkeypatch.delenv("WHATSAPP_DB_ENCRYPTION", raising=False)
    reg = Registry()
    reg.register(SECTION)

    def flow():
        f = FormFlow("database", reg, ConfigStore(reg))
        f.load()
        return f

    return flow, store_dir


def write_key(text=KEY):
    path = os.path.join(localconfig.config_dir(), "db.key")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return path


def test_the_section_is_read_only_and_has_no_restart():
    assert isinstance(SECTION.storage, ReadOnly) and SECTION.read_only and SECTION.restart is None
    assert all(f.readonly for f in SECTION.fields)


def test_the_section_is_read_only_in_the_flow_too(ctx):
    flow, _ = ctx
    f = flow()
    assert f.view().read_only is True
    f.apply({"key_present": True, "key_location": "/tmp/elsewhere"})
    outcome = f.save()
    assert outcome.ok is False
    assert f.view().values["key_location"] != "/tmp/elsewhere"


def test_it_shows_that_the_default_key_exists_and_where_but_never_the_key(ctx):
    flow, _ = ctx
    path = write_key()
    view = flow().view()
    assert view.values["key_present"] is True
    assert view.values["key_location"] == path and view.values["key_source"] == "default key file"
    assert view.values["key_problem"] == ""
    assert KEY not in repr(view) and KEY.upper() not in repr(view)


def test_it_says_when_there_is_no_key(ctx):
    flow, _ = ctx
    view = flow().view()
    assert view.values["key_present"] is False and view.values["key_problem"]
    assert view.values["key_location"] == os.path.join(localconfig.config_dir(), "db.key")


def test_a_key_in_the_environment_is_reported_by_variable_name_not_value(ctx, monkeypatch):
    flow, _ = ctx
    monkeypatch.setenv("WHATSAPP_DB_KEY", KEY)
    view = flow().view()
    assert view.values["key_present"] is True
    assert view.values["key_location"] == "WHATSAPP_DB_KEY" and "environment" in view.values["key_source"]
    assert KEY not in repr(view)


def test_a_key_file_named_by_the_environment_is_reported(ctx, monkeypatch, tmp_path):
    flow, _ = ctx
    custom = tmp_path / "elsewhere.key"
    custom.write_text(KEY)
    monkeypatch.setenv("WHATSAPP_DB_KEY_FILE", str(custom))
    view = flow().view()
    assert view.values["key_present"] is True and view.values["key_location"] == str(custom)
    assert KEY not in repr(view)


def test_an_unusable_key_is_a_problem_that_does_not_repeat_the_key(ctx):
    flow, _ = ctx
    write_key("not-a-key-" + KEY)
    view = flow().view()
    assert view.values["key_present"] is False
    assert "64 hexadecimal" in view.values["key_problem"]
    assert KEY not in repr(view)


def test_the_state_of_each_database_file_follows_its_header(ctx):
    flow, store_dir = ctx
    assert flow().view().values["messages_db"] == "missing" and flow().view().values["whatsapp_db"] == "missing"
    (store_dir / "messages.db").write_bytes(dbcrypto.SQLITE_MAGIC + b"\x00" * 100)
    (store_dir / "whatsapp.db").write_bytes(os.urandom(200))
    values = flow().view().values
    assert values["messages_db"] == "plain" and values["whatsapp_db"] == "encrypted"


def test_the_encryption_setting_is_shown_as_the_bridge_reads_it(ctx, monkeypatch):
    flow, _ = ctx
    assert flow().view().values["encryption"] == "on"
    monkeypatch.setenv("WHATSAPP_DB_ENCRYPTION", "off")
    assert flow().view().values["encryption"] == "off"


def test_no_field_can_carry_a_key():
    assert not any(f.secret for f in SECTION.fields)
    assert {f.key for f in SECTION.fields} == set(section_module.facts().keys())
