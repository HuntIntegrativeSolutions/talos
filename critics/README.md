# critics/

Deterministic gate functions. Each returns a verdict: `pass | fail | warn`, with an evidence
anchor. A gated task cannot leave `review` until every required critic returns `pass` and a human
approves (see `engine/schema.sql` → `task_gate_results`, `v_gate_status`).

Critics are pure and reproducible — no LLM in the gate itself.
