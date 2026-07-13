"""nexus_seed tests (ADR-039 action item #4).

Uses the same pg_setup/admin_conn/app_conn fixtures as test_vault_indexer.py.
Each test sets TALOS_NEXUS_STUB=1 explicitly via monkeypatch (matching
test_p5_extraction.py's convention) so seed_entities() reads
nexus_seed._STUB_ENTITY_INVENTORY -- no live NEXUS connection required.
"""

from __future__ import annotations

import hashlib
import uuid

from talos.nexus_seed import _STUB_ENTITY_INVENTORY, seed_entities


def _seed_board(conn, board_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (board_id, f"Board {board_id}"),
        )
    conn.commit()


def _query(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    conn.rollback()
    return rows


def _entity_id(board_id, entity_type, external_ref):
    return hashlib.sha256(f"{board_id}:{entity_type}:{external_ref}".encode()).hexdigest()[:32]


def test_seed_idempotence(pg_setup, admin_conn, monkeypatch):
    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    stats1 = seed_entities(board_id)
    assert stats1.created == len(_STUB_ENTITY_INVENTORY)
    assert stats1.updated == 0
    assert stats1.stale == 0

    rows1 = _query(
        admin_conn,
        "SELECT id, entity_type, name, external_ref FROM entities "
        "WHERE board_id = %s ORDER BY external_ref",
        (board_id,),
    )

    stats2 = seed_entities(board_id)
    assert stats2.created == 0
    assert stats2.updated == len(_STUB_ENTITY_INVENTORY)
    assert stats2.stale == 0

    rows2 = _query(
        admin_conn,
        "SELECT id, entity_type, name, external_ref FROM entities "
        "WHERE board_id = %s ORDER BY external_ref",
        (board_id,),
    )
    assert rows1 == rows2


def test_seed_deterministic_id(pg_setup, admin_conn, monkeypatch):
    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    seed_entities(board_id)

    entry = _STUB_ENTITY_INVENTORY[0]
    expected_id = _entity_id(board_id, entry["entity_type"], entry["external_ref"])
    rows = _query(
        admin_conn,
        "SELECT id FROM entities WHERE board_id = %s AND external_ref = %s",
        (board_id, entry["external_ref"]),
    )
    assert rows == [(expected_id,)]


def test_shrunk_inventory_marks_stale_not_deleted(pg_setup, admin_conn, monkeypatch):
    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    seed_entities(board_id)
    full_count = len(_query(admin_conn, "SELECT 1 FROM entities WHERE board_id = %s", (board_id,)))
    assert full_count == len(_STUB_ENTITY_INVENTORY)

    shrunk = [_STUB_ENTITY_INVENTORY[0]]
    monkeypatch.setattr("talos.nexus_seed._fetch_inventory", lambda board_id: shrunk)

    stats = seed_entities(board_id)
    assert stats.stale == len(_STUB_ENTITY_INVENTORY) - 1

    # Nothing deleted -- still the same row count.
    after_count = _query(admin_conn, "SELECT 1 FROM entities WHERE board_id = %s", (board_id,))
    assert len(after_count) == full_count

    stale_flags = _query(
        admin_conn,
        "SELECT external_ref, metadata->>'stale' FROM entities WHERE board_id = %s",
        (board_id,),
    )
    stale_map = dict(stale_flags)
    assert stale_map[shrunk[0]["external_ref"]] is None
    for entry in _STUB_ENTITY_INVENTORY[1:]:
        assert stale_map[entry["external_ref"]] == "true"


def test_reappearing_entity_clears_stale_flag(pg_setup, admin_conn, monkeypatch):
    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    seed_entities(board_id)

    shrunk = [_STUB_ENTITY_INVENTORY[0]]
    monkeypatch.setattr("talos.nexus_seed._fetch_inventory", lambda board_id: shrunk)
    seed_entities(board_id)

    stale_before = _query(
        admin_conn,
        "SELECT metadata->>'stale' FROM entities WHERE board_id = %s AND external_ref = %s",
        (board_id, _STUB_ENTITY_INVENTORY[1]["external_ref"]),
    )
    assert stale_before == [("true",)]

    monkeypatch.undo()
    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    seed_entities(board_id)

    stale_after = _query(
        admin_conn,
        "SELECT metadata->>'stale' FROM entities WHERE board_id = %s AND external_ref = %s",
        (board_id, _STUB_ENTITY_INVENTORY[1]["external_ref"]),
    )
    assert stale_after == [(None,)]


def test_cross_board_isolation(pg_setup, admin_conn, app_conn, monkeypatch):
    monkeypatch.setenv("TALOS_NEXUS_STUB", "1")
    board_a = f"board-a-{uuid.uuid4().hex[:8]}"
    board_b = f"board-b-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_a)
    _seed_board(admin_conn, board_b)

    seed_entities(board_a)
    seed_entities(board_b)

    with app_conn.cursor() as cur:
        cur.execute("SET LOCAL app.board_id = %s", (board_b,))
        cur.execute("SELECT external_ref FROM entities")
        visible = {r[0] for r in cur.fetchall()}
    app_conn.rollback()

    assert visible == {e["external_ref"] for e in _STUB_ENTITY_INVENTORY}
