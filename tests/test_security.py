import pytest

from pitdb.connection import PitDB


def test_loopback_host_detection():
    assert PitDB._is_loopback_host("localhost")
    assert PitDB._is_loopback_host("127.0.0.1")
    assert PitDB._is_loopback_host("::1")
    assert not PitDB._is_loopback_host("192.168.1.10")
    assert not PitDB._is_loopback_host("db.internal")


def test_query_disabled_by_default():
    db = PitDB.__new__(PitDB)
    db._allow_unsafe_query = False
    db._q = lambda *_args, **_kwargs: "ok"
    with pytest.raises(RuntimeError, match="disabled by default"):
        db.query("1+1")


def test_query_enabled_with_opt_in():
    db = PitDB.__new__(PitDB)
    db._allow_unsafe_query = True
    db._q = lambda query: f"ran:{query}"
    assert db.query("1+1") == "ran:1+1"
