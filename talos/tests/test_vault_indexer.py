"""Vault indexer tests (ADR-039 action item #2).

Uses the same pg_setup/admin_conn/app_conn fixtures as test_pgvector_store.py
(conftest already mirrors V0009's notes/links/tags/chunks exactly) plus the
same deterministic fake_embedder pattern -- no real model load, CI-safe.

admin_conn is a superuser connection and bypasses RLS by table ownership
(see conftest.py's own doc comment) -- SET LOCAL app.board_id does nothing
for it. Every query here therefore filters board_id explicitly, matching
test_pgvector_store.py's own pattern, rather than relying on RLS.
"""

from __future__ import annotations

import hashlib
import textwrap
import uuid

import pytest

from talos.vault.indexer import index_vault


def _fake_embed(texts: list[str]) -> list[list[float]]:
    vecs = []
    for t in texts:
        h = hashlib.sha256(t.encode()).digest()
        raw = (h * ((384 // len(h)) + 1))[:384]
        vec = [b / 255.0 + 0.01 for b in raw]
        norm = sum(x * x for x in vec) ** 0.5
        vecs.append([x / norm for x in vec])
    return vecs


@pytest.fixture()
def fake_embedder(monkeypatch):
    monkeypatch.setattr("talos.memory.embedding.get_embed_fn", lambda: _fake_embed)


def _seed_board(conn, board_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO boards (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (board_id, f"Board {board_id}"),
        )
    conn.commit()


def _seed_entity(conn, board_id: str, entity_type: str, name: str, external_ref: str) -> str:
    import hashlib

    entity_id = hashlib.sha256(f"{board_id}:{entity_type}:{external_ref}".encode()).hexdigest()[:32]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO entities (id, board_id, entity_type, name, external_ref) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (entity_id, board_id, entity_type, name, external_ref),
        )
    conn.commit()
    return entity_id


def _write(vault_dir, rel_path: str, content: str) -> None:
    p = vault_dir / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n")


def _query(conn, sql, params=()):
    """Plain query via a superuser connection -- caller must filter
    board_id explicitly (admin_conn bypasses RLS, so SET LOCAL is a no-op)."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    conn.rollback()
    return rows


# ---------------------------------------------------------------------------
# Parse fixtures
# ---------------------------------------------------------------------------

def test_index_parses_aliases_embeds_tags(pg_setup, admin_conn, fake_embedder, tmp_path):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    _write(tmp_path, "note.md", """
        ---
        title: Note One
        tags: [alpha]
        ---
        # Body

        See [[Target Note|alias text]] and ![[diagram.png]].

        #beta #nested/tag
    """)
    _write(tmp_path, "Target Note.md", "# Target Note\ncontent")

    stats = index_vault(board_id, tmp_path)
    assert stats.notes_created == 2

    rows = _query(
        admin_conn,
        "SELECT n.path, l.link_type, l.target_note_id IS NOT NULL AS resolved "
        "FROM links l JOIN notes n ON n.id = l.src_note_id "
        "WHERE l.board_id = %s AND n.path = 'note.md' AND l.valid_until IS NULL",
        (board_id,),
    )
    kinds = {(r[1], r[2]) for r in rows}
    assert ("wikilink", True) in kinds  # resolved to target-note.md
    assert ("embed", False) in kinds  # diagram.png has no note

    tags = {r[0] for r in _query(
        admin_conn,
        "SELECT tag FROM tags t JOIN notes n ON n.id = t.note_id "
        "WHERE t.board_id = %s AND n.path = 'note.md'",
        (board_id,),
    )}
    assert tags == {"alpha", "beta", "nested/tag"}


def test_index_handles_missing_and_empty_frontmatter(pg_setup, admin_conn, fake_embedder, tmp_path):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    _write(tmp_path, "no-fm.md", "# No Frontmatter\njust body text")
    _write(tmp_path, "empty-fm.md", "---\n---\n# Empty Frontmatter\nbody")

    stats = index_vault(board_id, tmp_path)
    assert stats.notes_created == 2

    # No frontmatter `title:` key in either fixture, so title falls back to
    # the filename stem (parser.py's documented fallback) -- not the H1.
    titles = {r[0] for r in _query(
        admin_conn, "SELECT title FROM notes WHERE board_id = %s", (board_id,)
    )}
    assert titles == {"no-fm", "empty-fm"}


def test_index_unresolved_link_stores_target_slug(pg_setup, admin_conn, fake_embedder, tmp_path):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    _write(tmp_path, "note.md", "# Note\n[[Nonexistent Note]]")
    index_vault(board_id, tmp_path)

    rows = _query(
        admin_conn,
        "SELECT target_note_id, target_slug FROM links WHERE board_id = %s AND valid_until IS NULL",
        (board_id,),
    )
    assert len(rows) == 1
    assert rows[0][0] is None
    assert rows[0][1] == "nonexistent note"


# ---------------------------------------------------------------------------
# Bi-temporal supersession
# ---------------------------------------------------------------------------

def test_link_removal_is_bi_temporal_not_deleted(pg_setup, admin_conn, fake_embedder, tmp_path):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    _write(tmp_path, "a.md", "# A\n[[B]]")
    _write(tmp_path, "b.md", "# B\ncontent")
    index_vault(board_id, tmp_path)

    _write(tmp_path, "a.md", "# A\nno more links")
    index_vault(board_id, tmp_path)

    rows = _query(
        admin_conn,
        "SELECT l.valid_until FROM links l JOIN notes n ON n.id = l.src_note_id "
        "WHERE l.board_id = %s AND n.path = 'a.md'",
        (board_id,),
    )
    assert len(rows) == 1
    assert rows[0][0] is not None  # historical row still present, closed

    open_rows = _query(
        admin_conn,
        "SELECT l.id FROM links l JOIN notes n ON n.id = l.src_note_id "
        "WHERE l.board_id = %s AND n.path = 'a.md' AND l.valid_until IS NULL",
        (board_id,),
    )
    assert open_rows == []


# ---------------------------------------------------------------------------
# Forward-reference resolution
# ---------------------------------------------------------------------------

def test_forward_reference_resolves_when_target_appears(pg_setup, admin_conn, fake_embedder, tmp_path):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    _write(tmp_path, "y.md", "# Y\n[[X]]")
    index_vault(board_id, tmp_path)

    pending = _query(
        admin_conn,
        "SELECT target_note_id, target_slug FROM links WHERE board_id = %s AND valid_until IS NULL",
        (board_id,),
    )
    assert pending == [(None, "x")]

    _write(tmp_path, "x.md", "# X\ncontent")
    index_vault(board_id, tmp_path)

    rows = _query(
        admin_conn,
        "SELECT l.target_note_id IS NOT NULL, l.valid_until FROM links l "
        "JOIN notes n ON n.id = l.src_note_id "
        "WHERE l.board_id = %s AND n.path = 'y.md' ORDER BY l.valid_from",
        (board_id,),
    )
    assert len(rows) == 2
    assert rows[0][0] is False and rows[0][1] is not None  # old row closed
    assert rows[1][0] is True and rows[1][1] is None  # new row resolved, open


# ---------------------------------------------------------------------------
# Note-deletion downgrade
# ---------------------------------------------------------------------------

def test_note_deletion_deletes_outgoing_downgrades_incoming(pg_setup, admin_conn, fake_embedder, tmp_path):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    _write(tmp_path, "x.md", "# X\ncontent")
    _write(tmp_path, "y.md", "# Y\n[[X]]")
    index_vault(board_id, tmp_path)

    x_id = _query(
        admin_conn, "SELECT id FROM notes WHERE board_id = %s AND path = 'x.md'", (board_id,)
    )[0][0]

    (tmp_path / "x.md").unlink()
    stats = index_vault(board_id, tmp_path)
    assert stats.notes_deleted == 1

    assert _query(admin_conn, "SELECT 1 FROM notes WHERE board_id = %s AND id = %s", (board_id, x_id)) == []
    assert _query(admin_conn, "SELECT 1 FROM chunks WHERE board_id = %s AND note_id = %s", (board_id, x_id)) == []
    assert _query(admin_conn, "SELECT 1 FROM links WHERE board_id = %s AND src_note_id = %s", (board_id, x_id)) == []

    # Both the closed historical row and its fresh live replacement are now
    # downgraded to target_slug='x' (see indexer.py's _delete_note doc) --
    # assert on the live (valid_until IS NULL) row specifically.
    incoming_open = _query(
        admin_conn,
        "SELECT target_note_id, target_slug FROM links l JOIN notes n ON n.id = l.src_note_id "
        "WHERE l.board_id = %s AND n.path = 'y.md' AND l.valid_until IS NULL",
        (board_id,),
    )
    assert incoming_open == [(None, "x")]

    incoming_all = _query(
        admin_conn,
        "SELECT target_note_id, target_slug FROM links l JOIN notes n ON n.id = l.src_note_id "
        "WHERE l.board_id = %s AND n.path = 'y.md'",
        (board_id,),
    )
    assert incoming_all == [(None, "x"), (None, "x")]


# ---------------------------------------------------------------------------
# Rebuild idempotence
# ---------------------------------------------------------------------------

def test_rebuild_is_idempotent(pg_setup, admin_conn, fake_embedder, tmp_path):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    _write(tmp_path, "a.md", "# A\n[[B]]\n#tagged")
    _write(tmp_path, "b.md", "# B\nsome content here")
    index_vault(board_id, tmp_path)

    index_vault(board_id, tmp_path, rebuild=True)
    first = _query(
        admin_conn, "SELECT path, content_hash FROM notes WHERE board_id = %s ORDER BY path", (board_id,)
    )

    index_vault(board_id, tmp_path, rebuild=True)
    second = _query(
        admin_conn, "SELECT path, content_hash FROM notes WHERE board_id = %s ORDER BY path", (board_id,)
    )
    assert first == second

    def _counts():
        return (
            len(_query(admin_conn, "SELECT 1 FROM notes WHERE board_id = %s", (board_id,))),
            len(_query(admin_conn, "SELECT 1 FROM links WHERE board_id = %s", (board_id,))),
            len(_query(admin_conn, "SELECT 1 FROM tags WHERE board_id = %s", (board_id,))),
            len(_query(
                admin_conn,
                "SELECT 1 FROM chunks WHERE board_id = %s AND source = 'vault'",
                (board_id,),
            )),
        )

    counts_first = _counts()
    index_vault(board_id, tmp_path, rebuild=True)
    assert _counts() == counts_first


# ---------------------------------------------------------------------------
# Cross-board isolation
# ---------------------------------------------------------------------------

def test_cross_board_isolation(pg_setup, admin_conn, app_conn, fake_embedder, tmp_path):
    board_a = f"board-a-{uuid.uuid4().hex[:8]}"
    board_b = f"board-b-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_a)
    _seed_board(admin_conn, board_b)

    vault_a = tmp_path / "vault-a"
    vault_b = tmp_path / "vault-b"
    _write(vault_a, "note.md", "---\ntitle: A Note\n---\n# A\n[[Other]] #a-tag")
    _write(vault_b, "note.md", "---\ntitle: B Note\n---\n# B\n[[Other]] #b-tag")

    index_vault(board_a, vault_a)
    index_vault(board_b, vault_b)

    with app_conn.cursor() as cur:
        cur.execute("SET LOCAL app.board_id = %s", (board_b,))
        cur.execute("SELECT tag FROM tags")
        tags_visible = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT title FROM notes")
        notes_visible = {r[0] for r in cur.fetchall()}
    app_conn.rollback()

    assert tags_visible == {"b-tag"}
    assert notes_visible == {"B Note"}


# ---------------------------------------------------------------------------
# Review regressions: unparseable files + links inside code


def test_unparseable_file_skipped_not_fatal_not_deleted(pg_setup, admin_conn, fake_embedder, tmp_path):
    board_id = f"vault-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    _write(tmp_path, "good.md", "# Good\n[[bad]]")
    _write(tmp_path, "bad.md", "# Bad but fine\nbody")
    stats = index_vault(board_id, tmp_path)
    assert stats.notes_created == 2

    # Corrupt bad.md's frontmatter so parsing raises; good.md gets a new link.
    (tmp_path / "bad.md").write_text("---\ntitle: [unclosed\n---\nbody\n")
    _write(tmp_path, "good.md", "# Good\n[[bad]] [[extra]]")

    stats = index_vault(board_id, tmp_path)
    # The run survived, good.md was re-indexed, and bad.md was neither
    # re-indexed nor treated as deleted.
    assert stats.notes_updated == 1
    assert stats.notes_deleted == 0
    rows = _query(
        admin_conn,
        "SELECT path FROM notes WHERE board_id = %s ORDER BY path",
        (board_id,),
    )
    assert [r[0] for r in rows] == ["bad.md", "good.md"]


def test_wikilinks_inside_code_are_not_links(pg_setup, admin_conn, fake_embedder, tmp_path):
    board_id = f"vault-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    _write(
        tmp_path,
        "note.md",
        """
        # Note
        Real link: [[Target]]

        ```python
        phantom = "[[NotALink]]"
        ```
        Inline `[[AlsoNotALink]]` code.
        """,
    )
    index_vault(board_id, tmp_path)

    rows = _query(
        admin_conn,
        "SELECT target_slug FROM links WHERE board_id = %s AND valid_until IS NULL",
        (board_id,),
    )
    assert {r[0] for r in rows} == {"target"}


# ---------------------------------------------------------------------------
# Entity matching (ADR-039 action item #4)


def _entity_links(conn, board_id, note_path):
    return _query(
        conn,
        "SELECT e.name, l.link_type FROM note_entity_links l "
        "JOIN notes n ON n.id = l.note_id JOIN entities e ON e.id = l.entity_id "
        "WHERE l.board_id = %s AND n.path = %s AND l.valid_until IS NULL",
        (board_id, note_path),
    )


def test_entity_exact_token_match_no_substring_false_positive(pg_setup, admin_conn, fake_embedder, tmp_path):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)
    _seed_entity(admin_conn, board_id, "tag", "PIT_01", "PLC1:PIT_01")

    _write(tmp_path, "hit.md", "# Hit\nAlarm on PIT_01 today.")
    _write(tmp_path, "miss.md", "# Miss\nWRK_PIT_01 and PIT_012 are different tags.")
    index_vault(board_id, tmp_path)

    assert _entity_links(admin_conn, board_id, "hit.md") == [("PIT_01", "mentions")]
    assert _entity_links(admin_conn, board_id, "miss.md") == []


def test_entity_wikilink_match(pg_setup, admin_conn, fake_embedder, tmp_path):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)
    _seed_entity(admin_conn, board_id, "controller", "PLC_WEST_01", "PLC_WEST_01")

    _write(tmp_path, "note.md", "# Note\nSee [[PLC_WEST_01]] for details.")
    index_vault(board_id, tmp_path)

    assert _entity_links(admin_conn, board_id, "note.md") == [("PLC_WEST_01", "mentions")]


def test_entity_short_name_skipped_for_body_but_matches_wikilink(pg_setup, admin_conn, fake_embedder, tmp_path):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)
    _seed_entity(admin_conn, board_id, "tag", "M1", "PLC1:M1")

    _write(tmp_path, "body-only.md", "# Body\nMotor M1 runs fine.")
    _write(tmp_path, "wikilink.md", "# Link\n[[M1]]")
    index_vault(board_id, tmp_path)

    assert _entity_links(admin_conn, board_id, "body-only.md") == []
    assert _entity_links(admin_conn, board_id, "wikilink.md") == [("M1", "mentions")]


def test_entity_frontmatter_documents_without_wikilink_or_mention(pg_setup, admin_conn, fake_embedder, tmp_path):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)
    _seed_entity(admin_conn, board_id, "tag", "PIT_01", "PLC1:PIT_01")

    _write(tmp_path, "note.md", """
        ---
        documents: [PIT_01]
        ---
        # Note
        No wikilink or body mention of the tag here.
    """)
    index_vault(board_id, tmp_path)

    assert _entity_links(admin_conn, board_id, "note.md") == [("PIT_01", "documents")]


def test_entity_documents_wins_over_mention_single_row(pg_setup, admin_conn, fake_embedder, tmp_path):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)
    _seed_entity(admin_conn, board_id, "tag", "PIT_01", "PLC1:PIT_01")

    _write(tmp_path, "note.md", """
        ---
        documents: [PIT_01]
        ---
        # Note
        PIT_01 is mentioned right here too.
    """)
    index_vault(board_id, tmp_path)

    rows = _entity_links(admin_conn, board_id, "note.md")
    assert rows == [("PIT_01", "documents")]


def test_entity_unresolvable_documents_entry_logged_and_skipped(pg_setup, admin_conn, fake_embedder, tmp_path):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    _write(tmp_path, "note.md", """
        ---
        documents: [NoSuchTag]
        ---
        # Note
        body
    """)
    index_vault(board_id, tmp_path)

    assert _entity_links(admin_conn, board_id, "note.md") == []


def test_entity_link_removal_is_bi_temporal_not_deleted(pg_setup, admin_conn, fake_embedder, tmp_path):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)
    _seed_entity(admin_conn, board_id, "tag", "PIT_01", "PLC1:PIT_01")

    _write(tmp_path, "note.md", "# Note\nPIT_01 mentioned here.")
    index_vault(board_id, tmp_path)
    assert _entity_links(admin_conn, board_id, "note.md") == [("PIT_01", "mentions")]

    _write(tmp_path, "note.md", "# Note\nNo tag mention anymore.")
    index_vault(board_id, tmp_path)
    assert _entity_links(admin_conn, board_id, "note.md") == []

    all_rows = _query(
        admin_conn,
        "SELECT l.valid_until FROM note_entity_links l JOIN notes n ON n.id = l.note_id "
        "WHERE l.board_id = %s AND n.path = 'note.md'",
        (board_id,),
    )
    assert len(all_rows) == 1
    assert all_rows[0][0] is not None  # historical row still present, closed


def test_entity_rematch_picks_up_newly_seeded_entity_on_unchanged_note(pg_setup, admin_conn, fake_embedder, tmp_path):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)

    _write(tmp_path, "note.md", "# Note\nAlarm on PIT_01 today.")
    index_vault(board_id, tmp_path)
    assert _entity_links(admin_conn, board_id, "note.md") == []

    before = _query(
        admin_conn,
        "SELECT content_hash, mtime FROM notes WHERE board_id = %s AND path = 'note.md'",
        (board_id,),
    )

    # Entity seeded after the note was already indexed -- note.md's file
    # never changed, so a normal re-index (hash-unchanged) would never match it.
    _seed_entity(admin_conn, board_id, "tag", "PIT_01", "PLC1:PIT_01")

    stats = index_vault(board_id, tmp_path)
    assert stats.notes_unchanged == 1
    assert _entity_links(admin_conn, board_id, "note.md") == []  # not yet -- no rematch

    stats = index_vault(board_id, tmp_path, rematch_entities=True)
    assert _entity_links(admin_conn, board_id, "note.md") == [("PIT_01", "mentions")]

    after = _query(
        admin_conn,
        "SELECT content_hash, mtime FROM notes WHERE board_id = %s AND path = 'note.md'",
        (board_id,),
    )
    assert before == after  # rematch touched only entity links, not the note row


def test_entity_payoff_join_one_query(pg_setup, admin_conn, fake_embedder, tmp_path):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)
    _seed_entity(admin_conn, board_id, "tag", "PIT_01", "PLC1:PIT_01")

    _write(tmp_path, "affected.md", "# Affected\nDiscusses PIT_01 behavior.")
    _write(tmp_path, "unrelated.md", "# Unrelated\nNothing to do with tags.")
    index_vault(board_id, tmp_path)

    rows = _query(
        admin_conn,
        "SELECT n.path FROM notes n "
        "JOIN note_entity_links l ON l.note_id = n.id AND l.valid_until IS NULL "
        "JOIN entities e ON e.id = l.entity_id "
        "WHERE n.board_id = %s AND e.name = %s",
        (board_id, "PIT_01"),
    )
    assert [r[0] for r in rows] == ["affected.md"]


def test_entity_matching_can_be_disabled(pg_setup, admin_conn, fake_embedder, tmp_path):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)
    _seed_entity(admin_conn, board_id, "tag", "PIT_01", "PLC1:PIT_01")

    _write(tmp_path, "note.md", "# Note\nPIT_01 mentioned here.")
    index_vault(board_id, tmp_path, match_entities=False)

    assert _entity_links(admin_conn, board_id, "note.md") == []


def test_entity_note_deletion_removes_entity_links(pg_setup, admin_conn, fake_embedder, tmp_path):
    board_id = f"board-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_id)
    _seed_entity(admin_conn, board_id, "tag", "PIT_01", "PLC1:PIT_01")

    _write(tmp_path, "note.md", "# Note\nPIT_01 mentioned here.")
    index_vault(board_id, tmp_path)
    assert _entity_links(admin_conn, board_id, "note.md") == [("PIT_01", "mentions")]

    (tmp_path / "note.md").unlink()
    index_vault(board_id, tmp_path)

    rows = _query(
        admin_conn,
        "SELECT 1 FROM note_entity_links WHERE board_id = %s",
        (board_id,),
    )
    assert rows == []


# ---------------------------------------------------------------------------
# Cross-board isolation (entities + note_entity_links)


def test_entity_cross_board_isolation(pg_setup, admin_conn, app_conn, fake_embedder, tmp_path):
    board_a = f"board-a-{uuid.uuid4().hex[:8]}"
    board_b = f"board-b-{uuid.uuid4().hex[:8]}"
    _seed_board(admin_conn, board_a)
    _seed_board(admin_conn, board_b)
    _seed_entity(admin_conn, board_a, "tag", "PIT_01", "PLC1:PIT_01")
    _seed_entity(admin_conn, board_b, "tag", "PIT_01", "PLC1:PIT_01")

    vault_a = tmp_path / "vault-a"
    vault_b = tmp_path / "vault-b"
    _write(vault_a, "note.md", "# A\nPIT_01 mentioned.")
    _write(vault_b, "note.md", "# B\nPIT_01 mentioned.")
    index_vault(board_a, vault_a)
    index_vault(board_b, vault_b)

    with app_conn.cursor() as cur:
        cur.execute("SET LOCAL app.board_id = %s", (board_b,))
        cur.execute("SELECT name FROM entities")
        entities_visible = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT 1 FROM note_entity_links")
        links_visible = cur.fetchall()
    app_conn.rollback()

    assert entities_visible == {"PIT_01"}
    assert len(links_visible) == 1
