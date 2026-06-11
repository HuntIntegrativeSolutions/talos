# engine/

The board's source of truth (PostgreSQL) and task lifecycle.

- `schema.sql` — board tables, the review gate, and the Space Agent layer.
- *(planned)* dispatcher loop (claim ready tasks, spawn profiles, reclaim crashed workers),
  the board API, and Row-Level Security setup.

Ported from Hermes' board (NousResearch, MIT), hardened for multi-client with `board_id` + RLS and
a real review/approval gate.
