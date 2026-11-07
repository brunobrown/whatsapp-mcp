"""Where a message to the operator goes: beside the thing it is about, or out of the page for what is global (UX-04, UX-06)."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ui.app import notifier  # noqa: E402
from ui.app.notifier import Notice, Notifier  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class FakeService:
    def __init__(self, allow=True, fail_on_show=None):
        self.allow, self.fail_on_show = allow, fail_on_show
        self.permission_requests, self.shown = 0, []

    async def request_permissions(self):
        self.permission_requests += 1
        return self.allow

    async def show(self, id, *, title=None, body=None, payload=None, **rest):
        if self.fail_on_show:
            raise self.fail_on_show
        self.shown.append({"id": id, "title": title, "body": body, "payload": payload, **rest})


def run(coroutine):
    return asyncio.run(coroutine)


def deliver(n, notice, service, clock=None):
    snackbars = []
    where = run(n.deliver(notice, service, snackbars.append))
    return where, snackbars


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def n(clock):
    return Notifier(clock=clock)


def test_global_events_notify_once_and_local_feedback_stays_contextual(n, clock):  # UX-04
    service = FakeService()
    local = Notice.field_error("A porta deve estar entre 1024 e 65535")
    saved = Notice.saved("Mídia")

    assert deliver(n, local, service) == ("inline", []) and service.shown == [] and service.permission_requests == 0
    assert deliver(n, saved, service)[0] == "native" and len(service.shown) == 1
    assert deliver(n, saved, service)[0] == "skipped" and len(service.shown) == 1                # the same event again, within a minute
    clock.now += notifier.REPEAT_SECONDS + 1
    assert deliver(n, saved, service)[0] == "native" and len(service.shown) == 2                  # a minute later it is news again


def test_different_events_do_not_silence_each_other(n):  # UX-04
    service = FakeService()

    kinds = [deliver(n, notice, service)[0] for notice in (Notice.saved("Mídia"), Notice.saved("Geral"), Notice.bridge_down(), Notice.bridge_up())]

    assert kinds == ["native"] * 4 and len(service.shown) == 4


def test_the_same_event_about_two_sections_is_two_events(n):  # UX-04
    service = FakeService()

    assert deliver(n, Notice.save_failed("Mídia"), service)[0] == "native"
    assert deliver(n, Notice.save_failed("Geral"), service)[0] == "native"


def test_the_native_notification_carries_the_title_the_body_and_a_stable_id(n):  # UX-04
    service = FakeService()
    notice = Notice.bridge_down()

    deliver(n, notice, service)
    n2 = Notifier(clock=Clock())
    service2 = FakeService()
    deliver(n2, Notice.bridge_down(), service2)

    shown = service.shown[0]
    assert shown["title"] == notice.title and shown["body"] == notice.body and shown["payload"] is None
    assert shown["id"] == service2.shown[0]["id"] and 0 <= shown["id"] < 2 ** 31


def test_permission_is_asked_once_and_remembered(n, clock):  # UX-06
    service = FakeService(allow=True)

    deliver(n, Notice.saved("Mídia"), service)
    clock.now += 100
    deliver(n, Notice.saved("Mídia"), service)

    assert service.permission_requests == 1


def test_denied_notification_permission_falls_back_to_snackbar(n, clock):  # UX-06
    service = FakeService(allow=False)
    notice = Notice.save_failed("Mídia")

    where, snackbars = deliver(n, notice, service)

    assert where == "snackbar" and snackbars == [notice] and service.shown == []
    clock.now += 100
    where, snackbars = deliver(n, notice, service)
    assert where == "snackbar" and service.permission_requests == 1                  # it does not ask again and again


def test_a_service_that_fails_falls_back_to_snackbar_and_is_not_tried_again(n, clock):  # UX-06
    service = FakeService(fail_on_show=RuntimeError("no browser support"))

    where, snackbars = deliver(n, Notice.bridge_down(), service)
    clock.now += 100
    again, _ = deliver(n, Notice.bridge_up(), service)

    assert where == "snackbar" and len(snackbars) == 1 and again == "snackbar"


def test_a_permission_request_that_raises_falls_back_to_snackbar(n):  # UX-06
    class Broken(FakeService):
        async def request_permissions(self):
            raise RuntimeError("unsupported platform")

    assert deliver(n, Notice.saved("Mídia"), Broken())[0] == "snackbar"


def test_without_a_service_the_page_shows_a_snackbar(n):  # UX-06
    shown = []

    where = run(n.deliver(Notice.saved("Mídia"), None, shown.append))

    assert where == "snackbar" and [x.key for x in shown] == ["saved:Mídia"]


def test_a_snackbar_is_not_repeated_within_a_minute_either(n, clock):  # UX-04
    service = FakeService(allow=False)
    notice = Notice.bridge_down()

    first = deliver(n, notice, service)
    second = deliver(n, notice, service)

    assert first[0] == "snackbar" and second == ("skipped", [])


def test_the_texts_are_fixed_sentences_with_no_value_in_them():  # CFG-10
    notices = [Notice.saved("Mídia"), Notice.save_failed("Mídia"), Notice.bridge_down(), Notice.bridge_up(), Notice.session_ending(),
               Notice.restart_needed("bridge")]

    for notice in notices:
        text = notice.title + notice.body
        assert 0 < len(notice.title) <= 60 and 0 < len(notice.body) <= 200
        assert "{" not in text and "%" not in text and "\n" not in text
        assert notice.scope == "global"
    assert "Mídia" in Notice.saved("Mídia").body


def test_a_section_title_cannot_smuggle_control_characters_or_length_into_a_notice():  # CFG-10
    notice = Notice.saved("Mídia\n\x00" + "x" * 500)

    assert "\n" not in notice.body and "\x00" not in notice.body and len(notice.body) <= 200


def test_a_field_error_is_always_local():  # UX-04
    assert Notice.field_error("deve ser um número").scope == "local"
