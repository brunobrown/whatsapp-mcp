"""Each MCP tool declares whether it only reads, sends, deletes or downloads (ToolAnnotations)."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402

READ = {"search_contacts", "list_messages", "list_chats", "get_chat", "get_direct_chat_by_contact",
        "get_contact_chats", "get_last_interaction", "get_message_context", "get_message_thread",
        "get_poll_results", "watch_poll", "list_polls", "get_message_reactions", "get_monitor_profiles", "monitor_messages",
        "get_group_participants", "get_connection_status", "get_webhook_status", "list_pending_drafts"}
LOCAL = {"discard_pending_draft"}
SEND = {"test_webhook", "send_message", "send_file", "send_audio_message", "send_poll", "vote_poll", "react_to_message", "monitor_reply"}
DELETE = {"delete_message"}
DOWNLOAD = {"download_media", "download_media_batch"}


def annotations():
    return {t.name: t.annotations for t in asyncio.run(main.mcp.list_tools())}


def test_every_registered_tool_has_a_category():  # ANN-01
    registered = set(annotations())
    categorised = READ | LOCAL | SEND | DELETE | DOWNLOAD
    assert registered - categorised == set(), "tool(s) registered without an annotation category"
    assert categorised - registered == set(), "category lists a tool that is not registered"


@pytest.mark.parametrize("name", sorted(READ))
def test_read_tools_are_read_only(name):  # ANN-01
    assert annotations()[name].read_only_hint is True


@pytest.mark.parametrize("name", sorted(SEND))
def test_send_tools_write_but_do_not_destroy(name):  # ANN-01
    a = annotations()[name]
    assert (a.read_only_hint, a.destructive_hint, a.open_world_hint) == (False, False, True)


def test_delete_message_is_destructive():  # ANN-01
    a = annotations()["delete_message"]
    assert (a.read_only_hint, a.destructive_hint, a.open_world_hint) == (False, True, True)


@pytest.mark.parametrize("name", sorted(DOWNLOAD))
def test_download_tools_write_files_but_are_idempotent(name):  # ANN-01
    a = annotations()[name]
    assert (a.read_only_hint, a.destructive_hint, a.idempotent_hint) == (False, False, True)


@pytest.mark.parametrize("name", sorted(LOCAL))
def test_local_write_tools_touch_only_local_state(name):  # ANN-01, DRAFT-07
    a = annotations()[name]
    assert (a.read_only_hint, a.destructive_hint, a.open_world_hint) == (False, False, False)
