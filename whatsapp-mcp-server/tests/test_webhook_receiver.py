"""webhook_receiver.py: signature, clock window, deduplication, loopback, no text printed (HOOK-*)."""
import http.client
import io
import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import webhook_receiver as wr  # noqa: E402

SECRET = b"0123456789abcdef0123456789abcdef0123"
NOW = 1_800_000_000


def body_of(event_id="evt_1", type="message", **data):
    return json.dumps({"event_id": event_id, "type": type, "occurred_at": "2026-11-07T10:00:00Z",
                       "delivery_attempt": 1, "data": data}).encode()


def signed(body, secret=SECRET, stamp=NOW):
    return {"X-Webhook-Timestamp": str(stamp), "X-Webhook-Signature": wr.sign(secret, str(stamp), body)}


@pytest.fixture
def receiver():
    out = io.StringIO()
    return wr.Receiver(SECRET, out, clock=lambda: NOW), out


def test_valid_event_is_accepted_and_printed_as_a_line(receiver):  # HOOK-01
    r, out = receiver
    body = body_of(chat_jid="120363000000000000@g.us", sender="5511999990000", message_id="M1")

    assert r.handle(signed(body), body) == 204

    assert out.getvalue() == ("event=message id=evt_1 chat=120363000000000000@g.us sender=5511999990000 "
                              "message=M1 ts=2026-11-07T10:00:00Z\n")


@pytest.mark.parametrize("headers", [
    {},
    {"X-Webhook-Timestamp": str(NOW)},
    {"X-Webhook-Timestamp": str(NOW), "X-Webhook-Signature": "sha256=" + "0" * 64},
])
def test_missing_or_wrong_signature_is_401_and_prints_nothing(receiver, headers):  # HOOK-02
    r, out = receiver
    assert r.handle(headers, body_of()) == 401 and out.getvalue() == ""


def test_one_changed_byte_or_another_secret_is_401(receiver):  # HOOK-02
    r, out = receiver
    body = body_of()
    assert r.handle(signed(body), body + b" ") == 401
    assert r.handle(signed(body, secret=SECRET + b"x"), body) == 401
    assert out.getvalue() == ""


@pytest.mark.parametrize("stamp,status", [(NOW - 300, 204), (NOW + 300, 204), (NOW - 301, 401), (NOW + 301, 401)])
def test_clock_window_is_five_minutes(receiver, stamp, status):  # HOOK-03
    r, out = receiver
    body = body_of()
    assert r.handle(signed(body, stamp=stamp), body) == status
    assert bool(out.getvalue()) == (status == 204)


@pytest.mark.parametrize("stamp", ["abc", "", "-5", "1e9", "12.5"])
def test_a_timestamp_that_is_not_an_integer_is_401(receiver, stamp):  # HOOK-03
    r, out = receiver
    body = body_of()
    headers = {"X-Webhook-Timestamp": stamp, "X-Webhook-Signature": wr.sign(SECRET, stamp, body)}
    assert r.handle(headers, body) == 401 and out.getvalue() == ""


def test_same_event_id_is_204_but_printed_once(receiver):  # HOOK-04
    r, out = receiver
    body = body_of()
    assert r.handle(signed(body), body) == 204 and r.handle(signed(body), body) == 204
    other = body_of(event_id="evt_2")
    assert r.handle(signed(other), other) == 204
    assert out.getvalue().count("\n") == 2


def test_deduplication_memory_is_bounded(receiver, monkeypatch):  # HOOK-04
    monkeypatch.setattr(wr, "SEEN_LIMIT", 2)
    r, out = receiver
    for n in (1, 2, 3):
        b = body_of(event_id=f"e{n}")
        r.handle(signed(b), b)
    b = body_of(event_id="e1")          # forgotten: printed again, memory did not grow without limit
    r.handle(signed(b), b)
    assert out.getvalue().count("id=e1") == 2 and len(r._seen) == 2


@pytest.mark.parametrize("host,ok", [("127.0.0.1", True), ("::1", True), ("localhost", True),
                                     ("0.0.0.0", False), ("192.168.1.5", False), ("example.com", False), ("::", False)])
def test_only_loopback_may_listen(receiver, host, ok):  # HOOK-05
    r, _ = receiver
    if ok:
        assert wr.is_loopback(host)
    else:
        with pytest.raises(wr.ConfigError):
            wr.make_server(host, 0, r)


@pytest.mark.parametrize("secret", [None, b"", b"short", b"x" * 31, b"replace-with-your-own-value" * 2,
                                    b"<at least 32 random bytes, e.g. openssl rand -hex 32>"])
def test_weak_or_example_secret_is_refused(secret):  # HOOK-06
    with pytest.raises(wr.ConfigError):
        wr.Receiver(secret, io.StringIO())


def test_secret_comes_from_the_environment_or_a_private_file(tmp_path):  # HOOK-06
    assert wr.load_secret(None, {"WEBHOOK_SECRET": SECRET.decode()}) == SECRET
    with pytest.raises(wr.ConfigError):
        wr.load_secret(None, {})
    f = tmp_path / "s"
    f.write_bytes(SECRET + b"\n")
    os.chmod(f, 0o644)
    with pytest.raises(wr.ConfigError):
        wr.load_secret(str(f))
    os.chmod(f, 0o600)
    assert wr.load_secret(str(f)) == SECRET


@pytest.mark.parametrize("raw", [b"not json", b"[]", b'"x"', b'{"type":"message"}', b'{"event_id":"a"}',
                                 b'{"event_id":1,"type":"message"}', b'{"event_id":"","type":"message"}'])
def test_body_that_is_not_an_event_is_400(receiver, raw):  # HOOK-07
    r, out = receiver
    assert r.handle(signed(raw), raw) == 400 and out.getvalue() == ""


def test_oversized_body_is_413(receiver):  # HOOK-07
    r, out = receiver
    raw = b"x" * (64 * 1024 + 1)
    assert r.handle(signed(raw), raw) == 413 and out.getvalue() == ""


def test_text_question_options_and_secret_are_never_printed(receiver):  # HOOK-08
    r, out = receiver
    body = body_of(chat_jid="c@g.us", text="TEXT-SECRET", question="QUESTION", options=["OPT-A"], emoji="EMOJI",
                   filename="FILE.pdf", sender="Name With Spaces\nstate=owned")

    assert r.handle(signed(body), body) == 204

    printed = out.getvalue()
    for leaked in ("TEXT-SECRET", "QUESTION", "OPT-A", "EMOJI", "FILE.pdf", SECRET.decode()):
        assert leaked not in printed
    assert printed.count("\n") == 1 and "\nstate=owned" not in printed


def test_over_real_http_on_loopback(receiver):  # HOOK-01, HOOK-02, HOOK-04
    r, out = receiver
    r.clock = __import__("time").time
    server = wr.make_server("127.0.0.1", 0, r)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        body = body_of(chat_jid="c@g.us")
        stamp = str(int(r.clock()))
        good = {"X-Webhook-Timestamp": stamp, "X-Webhook-Signature": wr.sign(SECRET, stamp, body)}

        def post(headers, payload=body):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request("POST", "/hook", payload, headers)
            status = c.getresponse().status
            c.close()
            return status

        assert post(good) == 204
        assert post(good) == 204
        assert post(good, body + b"!") == 401
        assert post({**good, "X-Webhook-Signature": "sha256=00"}) == 401
        assert out.getvalue().count("event=message") == 1
    finally:
        server.shutdown()
        server.server_close()


def serve(receiver, host="127.0.0.1"):
    server = wr.make_server(host, 0, receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def raw_post(host, port, head, payload=b"", timeout=5):
    import socket
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect((host, port))
        s.sendall(head + b"\r\n\r\n" + payload)
        data = s.recv(200)
    return int(data.split(b" ")[1]) if data else None


def test_http_layer_answers_400_and_413_for_length_problems(receiver):  # HOOK-07
    r, out = receiver
    server = serve(r)
    try:
        port = server.server_address[1]
        base = b"POST /h HTTP/1.1\r\nHost: x\r\nContent-Length: "
        assert raw_post("127.0.0.1", port, base + str(wr.MAX_BODY).encode()) == 413
        assert raw_post("127.0.0.1", port, base + b"-1") == 400
        assert raw_post("127.0.0.1", port, base + b"abc") == 400
        assert out.getvalue() == ""
    finally:
        server.shutdown()
        server.server_close()


def test_deeply_nested_json_is_400_not_a_dead_thread(receiver):  # HOOK-07
    r, out = receiver
    raw = b"[" * 60000
    assert r.handle(signed(raw), raw) == 400 and out.getvalue() == ""


def test_a_client_that_stops_sending_is_dropped_by_the_timeout(receiver, monkeypatch):  # HOOK-07
    monkeypatch.setattr(wr, "READ_TIMEOUT_SECONDS", 0.3)
    r, out = receiver
    server = serve(r)
    try:
        port = server.server_address[1]
        # promises 100 bytes, sends 3: the handler must give up instead of waiting for ever
        assert raw_post("127.0.0.1", port, b"POST /h HTTP/1.1\r\nHost: x\r\nContent-Length: 100", b"abc", timeout=3) is None
        assert out.getvalue() == ""
    finally:
        server.shutdown()
        server.server_close()


def test_it_can_listen_on_ipv6_loopback(receiver):  # HOOK-05
    r, out = receiver
    try:
        server = serve(r, "::1")
    except OSError:
        pytest.skip("this machine has no IPv6 loopback")
    try:
        body = body_of(chat_jid="c@g.us")
        r.clock = __import__("time").time
        stamp = str(int(r.clock()))
        head = (b"POST /h HTTP/1.1\r\nHost: x\r\nContent-Length: %d\r\nX-Webhook-Timestamp: %s\r\n"
                b"X-Webhook-Signature: %s" % (len(body), stamp.encode(), wr.sign(SECRET, stamp, body).encode()))
        assert raw_post("::1", server.server_address[1], head, body) == 204
        assert out.getvalue().count("event=message") == 1
    finally:
        server.shutdown()
        server.server_close()
