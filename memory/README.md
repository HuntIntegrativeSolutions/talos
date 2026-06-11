# memory/

Polyglot memory adapters — each store does the job it is best at (see ADR-003):

- **postgres** — system of record (the board, business records).
- **graph** — knowledge & topology; federates with the NEXUS graph (read-through, never duplicated).
- **vector** — semantic + episodic recall (prior audits, verified solutions).
- **redis** — working memory, the live dashboard pub/sub, dispatcher coordination.
