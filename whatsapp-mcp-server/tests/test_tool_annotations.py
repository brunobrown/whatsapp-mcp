"""Cada ferramenta MCP declara se só lê, envia, apaga ou baixa (ToolAnnotations)."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402

READ = {"search_contacts", "list_messages", "list_chats", "get_chat", "get_direct_chat_by_contact",
        "get_contact_chats", "get_last_interaction", "get_message_context", "get_message_thread",
        "get_poll_results", "list_polls", "get_monitor_profiles", "monitor_messages",
        "get_group_participants", "get_connection_status"}
SEND = {"send_message", "send_file", "send_audio_message", "send_poll", "vote_poll", "monitor_reply"}
DELETE = {"delete_message"}
DOWNLOAD = {"download_media", "download_media_batch"}


def annotations():
    return {t.name: t.annotations for t in asyncio.run(main.mcp.list_tools())}


def test_every_registered_tool_has_a_category():  # ANN-01
    registered = set(annotations())
    categorised = READ | SEND | DELETE | DOWNLOAD
    assert registered - categorised == set(), "tool(s) registered without an annotation category"
    assert categorised - registered == set(), "category lists a tool that is not registered"


@pytest.mark.parametrize("name", sorted(READ))
def test_read_tools_are_read_only(name):  # ANN-01
    assert annotations()[name].readOnlyHint is True


@pytest.mark.parametrize("name", sorted(SEND))
def test_send_tools_write_but_do_not_destroy(name):  # ANN-01
    a = annotations()[name]
    assert (a.readOnlyHint, a.destructiveHint, a.openWorldHint) == (False, False, True)


def test_delete_message_is_destructive():  # ANN-01
    a = annotations()["delete_message"]
    assert (a.readOnlyHint, a.destructiveHint, a.openWorldHint) == (False, True, True)


@pytest.mark.parametrize("name", sorted(DOWNLOAD))
def test_download_tools_write_files_but_are_idempotent(name):  # ANN-01
    a = annotations()[name]
    assert (a.readOnlyHint, a.destructiveHint, a.idempotentHint) == (False, False, True)
