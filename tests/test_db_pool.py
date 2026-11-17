"""Postgres connection reuse in db._conn, against a fake psycopg2."""
import sys
import types

import pytest

from api import db


class FakeConn:
    def __init__(self, log):
        self.closed = 0
        self.log = log
        self.fail_ping = False
        self.fail_rollback = False

    def cursor(self):
        conn = self

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, *a):
                if conn.fail_ping:
                    raise RuntimeError("server closed the connection")

        return Cur()

    def commit(self):
        self.log.append("commit")

    def rollback(self):
        if self.fail_rollback:
            raise RuntimeError("connection already closed")
        self.log.append("rollback")

    def close(self):
        self.closed = 1


@pytest.fixture
def fake_pg(monkeypatch):
    made = []
    log = []
    mod = types.ModuleType("psycopg2")

    def connect(url):
        c = FakeConn(log)
        made.append(c)
        return c

    mod.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg2", mod)
    monkeypatch.setattr(db, "_PG", True)
    monkeypatch.setattr(db, "_pool", [])
    return made, log


def test_sequential_blocks_reuse_one_connection(fake_pg):
    made, log = fake_pg
    for _ in range(5):
        with db._conn():
            pass
    assert len(made) == 1
    assert log == ["commit"] * 5


def test_nested_block_gets_its_own_connection(fake_pg):
    made, _ = fake_pg
    with db._conn() as outer:
        with db._conn() as inner:
            assert inner is not outer
    assert len(made) == 2
    with db._conn(), db._conn():
        pass
    assert len(made) == 2  # both came back to the pool


def test_failed_work_rolls_back_and_keeps_connection(fake_pg):
    made, log = fake_pg
    with pytest.raises(ValueError):
        with db._conn():
            raise ValueError("bad sql")
    assert log == ["rollback"]
    with db._conn():
        pass
    assert len(made) == 1


def test_dead_connection_is_discarded(fake_pg):
    made, _ = fake_pg
    with pytest.raises(ValueError):
        with db._conn() as c:
            c.fail_rollback = True
            raise ValueError("network gone")
    assert made[0].closed
    with db._conn():
        pass
    assert len(made) == 2


def test_idle_connection_is_pinged_and_replaced_if_dropped(fake_pg, monkeypatch):
    made, _ = fake_pg
    with db._conn() as c:
        pass
    c.fail_ping = True
    monkeypatch.setattr(db, "_POOL_CHECK_AFTER_S", -1)  # treat as idle
    with db._conn() as c2:
        assert c2 is not c
    assert c.closed and len(made) == 2
