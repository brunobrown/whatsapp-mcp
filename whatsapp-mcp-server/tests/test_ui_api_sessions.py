"""Server-side sessions (MIG-02, AUTH-05): id, CSRF token and idle clock per session, at most 20."""
from ui.api.sessions import MAX_SESSIONS, SessionStore


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(idle=60.0):
    clock = Clock()
    return SessionStore(idle_seconds=idle, clock=clock), clock


def test_a_new_session_has_a_long_random_id_and_csrf_token_and_is_not_signed_in():
    store, _ = make()

    a, b = store.create("origin-a"), store.create("origin-b")

    assert a.id != b.id and a.csrf != b.csrf and a.id != a.csrf
    assert len(a.id) >= 32 and len(a.csrf) >= 32
    assert a.authenticated is False
    assert store.get(a.id) is a and store.get(b.id) is b


def test_an_unknown_or_missing_cookie_finds_nothing():
    store, _ = make()

    assert store.get("nope") is None and store.get(None) is None and store.get("") is None


def test_the_id_and_the_token_change_on_sign_in():
    store, _ = make()
    session = store.create("o")
    old_id, old_csrf = session.id, session.csrf

    session.sign_in()

    assert session.authenticated is True
    assert session.id != old_id and session.csrf != old_csrf
    assert store.get(old_id) is None
    assert store.get(session.id) is session


def test_sign_out_ends_the_session():
    store, _ = make()
    session = store.create("o")
    session.sign_in()

    session.sign_out()

    assert session.authenticated is False
    assert store.get(session.id) is None


def test_an_expired_session_is_not_found_and_is_remembered_as_expired():
    store, clock = make(idle=60)
    session = store.create("o")
    session.sign_in()
    cookie = session.id

    clock.now += 61

    assert store.get(cookie) is None
    assert store.was_expired(cookie) is True
    assert store.was_expired("never-existed") is False


def test_use_before_the_limit_keeps_the_session_alive():
    store, clock = make(idle=60)
    session = store.create("o")

    clock.now += 50
    assert store.get(session.id) is session and session.idle.touch()
    clock.now += 50

    assert store.get(session.id) is session


def test_the_21st_session_drops_the_oldest_idle_one():
    store, clock = make(idle=10_000)
    sessions = []
    for i in range(MAX_SESSIONS):
        clock.now += 1
        sessions.append(store.create(f"o{i}"))
    clock.now += 100
    sessions[0].idle.touch()                       # the first one was used last: the second is now the oldest idle

    newest = store.create("o-new")

    assert MAX_SESSIONS == 20
    assert store.get(sessions[1].id) is None
    assert store.get(sessions[0].id) is sessions[0]
    assert all(store.get(s.id) is s for s in sessions[2:])
    assert store.get(newest.id) is newest
    assert store.count() == 20


def test_drop_removes_a_session():
    store, _ = make()
    session = store.create("o")

    store.drop(session.id)

    assert store.get(session.id) is None


def test_a_flood_of_anonymous_sessions_drops_anonymous_ones_before_a_signed_in_session():
    store, clock = make(idle=10_000)
    signed = store.create("operator")
    signed.sign_in()
    for i in range(MAX_SESSIONS * 2):
        clock.now += 1
        store.create(f"anon{i}")

    assert store.get(signed.id) is signed
    assert store.count() == MAX_SESSIONS
