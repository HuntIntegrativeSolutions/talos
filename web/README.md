# web/

The view layer: the board rendered as a Space Agent **Space**.

- Columns, cards, and per-task widgets are agent-authored and time-travel-versioned.
- Talks to the engine **only** through the board API — never the database.
- Time-travel versions the *layout*, never task records.

Pattern (and, where it helps, code) from Space Agent (agent0ai, MIT).
