"""The read-only pages: bridge status and counters, pending drafts and the audit logs (OPS-01, OPS-02, OPS-03, UX-03)."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

ft = pytest.importorskip("flet")

from ui.app import ops_pages  # noqa: E402
from ui.app.ops_pages import Poller, activity_view, human_duration, human_when, status_view  # noqa: E402
from ui.core.bridge import Status, Unavailable  # noqa: E402

TEXT = "TEXTO-SECRETO-DE-UMA-MENSAGEM"


def walk(control):
    yield control
    for child in getattr(control, "controls", None) or []:
        yield from walk(child)
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        yield from walk(content)
    for row in getattr(control, "rows", None) or []:
        for cell in row.cells:
            yield from walk(cell.content)


def of_type(control, kind):
    return [c for c in walk(control) if isinstance(c, kind)]


def texts(control):
    return " | ".join(str(c.value) for c in of_type(control, ft.Text) if c.value)


def labels_of_buttons(control):
    return [str(b.content) for b in of_type(control, (ft.Button, ft.FilledButton, ft.TextButton, ft.OutlinedButton, ft.IconButton))]


CONNECTED = Status({"connected": True, "logged_in": True, "phone": "5511999990000", "version": "0.5.0", "started_at": "2026-11-07T10:00:00Z",
                    "uptime_seconds": 8100, "last_event_at": "2026-11-07T12:00:00Z", "last_message_at": None, "pending_media_retries": 2})
COUNTERS = {"success": True, "window_hours": 24, "messages": {"received": 40, "from_this_account": 3},
            "monitor_replies": {"by_profile": {"support": {"sent": 2, "drafted": 5}}, "error_codes": {"RATE_LIMITED": 1}},
            "sends": {"by_result": {"sent": 4, "blocked": 1}, "with_override": 1}, "pending_drafts": 2,
            "webhooks": {"enabled": True, "endpoints": 1, "delivered": 9, "failed": 0, "dropped": 0, "queued": 0}}


def test_the_status_page_says_whether_the_bridge_is_connected_and_paired():  # OPS-01
    shown = texts(status_view(CONNECTED, COUNTERS))

    assert "Conectado ao WhatsApp" in shown and "5511999990000" in shown and "0.5.0" in shown and "2 h 15 min" in shown


def test_a_bridge_that_is_up_but_not_connected_says_so():  # OPS-01
    shown = texts(status_view(Status({"connected": False, "logged_in": True, "phone": "55"}), None))

    assert "Desconectado do WhatsApp" in shown and "Conectado ao WhatsApp" not in shown


def test_an_unpaired_bridge_says_it_needs_pairing_in_the_terminal():  # OPS-01
    shown = texts(status_view(Status({"connected": True, "logged_in": False}), None))

    assert "não está pareado" in shown.lower() and "terminal" in shown.lower()


def test_a_time_that_has_not_happened_is_a_dash_and_a_known_one_is_readable():  # OPS-01
    shown = texts(status_view(CONNECTED, None))

    assert "—" in shown and "2026-11-07 12:00 UTC" in shown


@pytest.mark.parametrize("reason, words", [("unreachable", "não responde"), ("unauthorized", "token"), ("unexpected", "inesperada"), ("unsupported", "antigo")])
def test_a_bridge_that_cannot_be_read_says_why_and_what_to_do(reason, words):  # OPS-01, UX-03
    view = status_view(Unavailable(reason), None)

    assert words in texts(view).lower() and "Conectado ao WhatsApp" not in texts(view)
    assert any(isinstance(c, ft.Semantics) and c.live_region for c in walk(view))


def test_before_the_first_answer_the_page_says_it_is_loading():  # UX-03
    shown = texts(status_view(None, None))

    assert "Carregando" in shown


def test_the_counters_are_shown_as_numbers_per_window():  # OPS-02
    shown = texts(status_view(CONNECTED, COUNTERS))

    for expected in ("40", "support", "RATE_LIMITED", "últimas 24 horas", "9"):
        assert expected in shown


def test_counters_that_could_not_be_read_are_said_not_shown_as_zero():  # UX-03
    broken = dict(COUNTERS, messages=None, webhooks=None)

    shown = texts(status_view(CONNECTED, broken))

    assert "indisponível" in shown.lower()


def test_the_status_page_only_offers_to_refresh():  # OPS-02
    assert labels_of_buttons(status_view(CONNECTED, COUNTERS, on_refresh=lambda: None)) == ["Atualizar agora"]
    assert labels_of_buttons(status_view(CONNECTED, COUNTERS)) == []


def test_ops_panels_have_no_message_text_and_no_actions():  # OPS-02
    draft = {"id": "d_1", "profile": "support", "chat_jid": "1@g.us", "message_id": "M1", "sender": "5511", "created_at": "2026-11-07T10:00:00+00:00",
             "expires_at": "2026-11-08T10:00:00+00:00"}
    send = {"ts": "2026-11-07T10:00:00-03:00", "kind": "text", "chat_jid": "1@g.us", "size": 12, "result": "sent", "override": False}
    monitor = {"ts": "2026-11-07T10:05:00-03:00", "profile": "support", "chat_jid": "1@g.us", "sender": "5511", "result": "drafted", "mode": "draft"}
    view = activity_view([draft], [send], [monitor])
    shown = texts(view).lower()

    assert labels_of_buttons(view) == []
    for word in ("aprovar", "descartar", "enviar", "responder", "votar"):
        assert word not in shown
    assert "d_1" in shown and "support" in shown and "drafted" in shown


def test_the_activity_page_says_where_a_draft_is_read_instead_of_showing_its_text():  # OPS-02
    shown = texts(activity_view([{"id": "d_1", "profile": "p", "chat_jid": "1@g.us", "message_id": "M", "sender": "5", "created_at": "t", "expires_at": "u"}], [], []))

    assert "list_pending_drafts" in shown and TEXT not in shown


def test_each_list_says_when_it_is_empty():  # UX-03
    shown = texts(activity_view([], [], []))

    assert shown.count("Nenhum") >= 3


def test_a_log_row_shows_the_rule_and_the_override_of_a_send():  # OPS-02
    shown = texts(activity_view([], [{"ts": "t", "kind": "text", "chat_jid": "1@g.us", "size": 1, "result": "blocked", "rule": 'deny rule "x"', "override": True}], []))

    assert "blocked" in shown and "deny rule" in shown and "liberado" in shown.lower()


def test_durations_read_like_a_person_would_say_them():  # OPS-01
    assert human_duration(0) == "menos de 1 min" and human_duration(59) == "menos de 1 min" and human_duration(60) == "1 min"
    assert human_duration(8100) == "2 h 15 min" and human_duration(90000) == "1 d 1 h" and human_duration(None) == "—"


def test_times_are_shown_in_utc_to_the_minute_and_a_missing_one_is_a_dash():  # OPS-01
    assert human_when("2026-11-07T12:00:30Z") == "2026-11-07 12:00 UTC" and human_when("2026-11-07T09:00:00-03:00") == "2026-11-07 12:00 UTC"
    assert human_when(None) == "—" and human_when("not a time") == "—"


# ---- the refresh that stops when the page is left ----

def test_status_polling_stops_when_panel_is_left():  # OPS-03
    async def scenario():
        fetched, applied = [], []
        gate = asyncio.Event()

        async def sleep(seconds):
            await gate.wait()
            gate.clear()

        def fetch():
            fetched.append(1)
            return len(fetched)

        poller = Poller(fetch, applied.append, interval=5, sleep=sleep)
        task = asyncio.create_task(poller.run())
        await asyncio.sleep(0.05)
        assert applied == [1]
        gate.set()
        await asyncio.sleep(0.05)
        assert applied == [1, 2]
        poller.stop()
        gate.set()
        await asyncio.wait_for(task, 1)
        await asyncio.sleep(0.05)
        return fetched, applied

    fetched, applied = asyncio.run(scenario())

    assert fetched == [1, 1] and applied == [1, 2]                       # nothing after stop


def test_a_result_that_arrives_after_stop_is_not_applied():  # OPS-03
    async def scenario():
        applied = []
        release = asyncio.Event()

        async def blocking(fn):
            await release.wait()
            return fn()
        poller = Poller(lambda: "late", applied.append, interval=1, sleep=lambda s: asyncio.sleep(0), run_blocking=blocking)
        task = asyncio.create_task(poller.run())
        await asyncio.sleep(0.01)
        poller.stop()
        release.set()
        await asyncio.wait_for(task, 1)
        return applied

    assert asyncio.run(scenario()) == []


def test_a_fetch_that_fails_does_not_end_the_polling():  # OPS-03
    async def scenario():
        calls, applied = [], []

        def fetch():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return "ok"
        poller = Poller(fetch, applied.append, interval=0, sleep=lambda s: asyncio.sleep(0))
        task = asyncio.create_task(poller.run())
        await asyncio.sleep(0.05)
        poller.stop()
        await asyncio.wait_for(task, 1)
        return applied

    assert asyncio.run(scenario())[:1] == ["ok"]


def test_the_interval_is_moderate():  # OPS-03
    assert 3 <= ops_pages.REFRESH_SECONDS <= 30


def test_no_imperative_call_in_the_pages():  # EXT-02
    source = open(ops_pages.__file__, encoding="utf-8").read()

    assert ".update(" not in source and "page.add" not in source
