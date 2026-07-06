# P7a Gate UI — Manual Verification Walkthrough

This walkthrough was executed end-to-end in a real headless Chromium browser (Playwright)
against a real, freshly-provisioned Postgres 16 instance and a real running
`uvicorn talos.api:app` server — not mocked, not through TestClient. Screenshots are
referenced by filename only; they are not committed to the repo (per the task's screenshot
policy) and were captured under `/tmp/talos-p7a-screenshots/` in the verification session.

## Environment note: a pre-existing, unrelated migration bug

While setting up a database via `alembic upgrade head` for this walkthrough,
`V0001_baseline.py`'s `_ENGINE_DIR = pathlib.Path(__file__).resolve().parent.parent`
resolves to `engine/migrations/` instead of `engine/`, so it looks for
`engine/migrations/schema.sql` and fails with `FileNotFoundError`. This is **pre-existing
and unrelated to P7a** — it explains why `talos/tests/conftest.py::pg_setup` has always
hand-applied the raw `schema*.sql` files directly and only calls `alembic stamp head`
(never `upgrade`). Worked around for this walkthrough by hand-applying
`schema.sql`/`schema-additions.sql`/`schema-p2.sql`/`schema-p3.sql` plus the V0002/V0003/V0004
DDL directly (identical to what `conftest.py` does), then `alembic stamp head`. Flagging this
here since it will bite the first real `alembic upgrade head` on a genuinely empty database —
worth a follow-up fix (change `.parent.parent` to `.parent.parent.parent` in
`V0001_baseline.py`), out of scope for this PR.

## Prerequisites used

- Postgres 16 in Docker, schema + V0002/V0003/V0004 DDL applied, `talos_app` (RLS-enforced)
  and `talos_system` (BYPASSRLS) roles created — mirrors `conftest.py::pg_setup` exactly.
- `TALOS_JWT_SECRET` set; user `engineer` created via `talos.auth.users.add_user` (the CLI's
  interactive `getpass` prompt doesn't work in a non-tty session, so the underlying function
  was called directly — functionally identical to `python -m talos.auth add-user`).
- API server started via `uvicorn` with a **PostgresSaver**-backed spine graph (not the
  test suite's in-process `MemorySaver`) so the server process and the separate
  `claim_and_run` driver process share checkpoint state through Postgres, the same as two
  real OS processes (API server + worker) would.
- `TALOS_NEXUS_STUB=1`.
- Board `verify-board`, several tasks driven to `review` via `claim_and_run`, `sla_minutes`
  set to 30 on the board.

## 1. Login

- [x] Navigated to `/gate/`. **Screenshot: `01-login-screen.png`.**
- [x] Submitted wrong credentials → inline "Invalid credentials." shown, no token stored,
      form re-usable. **Screenshot: `02-login-error.png`.**
- [x] Submitted correct credentials (`engineer` / real password) → reached the review queue.

## 2. Review queue

- [x] Board with tasks in review, oldest first: `verify-task-1` (2h old, seeded with a
      backdated `review_entered_at`) sorts first, ahead of six freshly-reviewed tasks.
- [x] SLA set to 30 minutes on the board via `PATCH /boards/{id}/sla`; the 2h-old task is
      highlighted red with an "OVERDUE" marker; fresh tasks (0m) are not.
      **Screenshot: `03-queue-populated-overdue.png`.**
- [x] Confirmed via the API directly (`GET /review-queue`) that `overdue: true`/`false` matches
      the UI's highlighting exactly.
- [ ] Browser OS-level `Notification` permission prompt and the actual OS notification banner
      were **not** visually captured — Playwright's headless Chromium auto-grants/no-ops
      notification permission prompts and Linux headless environments have no OS notification
      center to screenshot. The `Notification.requestPermission()` call site and the
      new-arrival-detection logic in `queue.js` were code-reviewed and exercised via the poll
      loop (new tasks correctly appear in the rendered table on the next poll); the
      OS-banner rendering itself is a browser/OS capability outside what this sandboxed
      environment can visually confirm. **A user with a real desktop browser should confirm
      this specific behavior once** — everything else in this document was independently,
      empirically verified.

## 3. Task review page

- [x] Deliverable Markdown renders correctly: a heading, bold text, a bullet list, and a link
      all rendered as expected HTML via `marked` → `DOMPurify` → `innerHTML`. Citations
      rendered as a separate plain list (not passed through the Markdown parser).
      **Screenshot: `06-review-deliverable-rendered.png`.**
- [x] Raw-source toggle shows the underlying deliverable JSON and back again.
      **Screenshot: `07-review-raw-toggle.png`.**
- [x] **XSS is neutralized, not just "probably safe."** Seeded a deliverable with
      `<img src=x onerror="window.__xss_fired=true">` in its summary. After rendering,
      `window.__xss_fired` was confirmed `false` and the rendered HTML confirmed to contain no
      `onerror` attribute at all (DOMPurify stripped it; the harmless `<img>` tag itself still
      rendered). **Screenshot: `13-xss-sanitized-deliverable.png`.**
- [x] Critic table shows `critic_name`, `verdict`, `required`/`advisory`, `waivable`/`not
      waivable`, and a `SAFETY` marker on safety-class rows (visually row-highlighted).
- [x] **Approve** — on a task with all required critics passing: clicked Approve, server
      returned `{"status":"ok","outcome":"approve",...}`, task left review.
      **Screenshot: `09-outcome-approve-result.png`.**
- [x] **Reject** — submitted with a reason (`"needs more detail on the tag audit trail"`);
      success. **Screenshot: `10-outcome-reject-result.png`.**
- [x] **Waive** — on a task with only a non-safety failing critic (`citations_resolvable`,
      waivable), submitted with a justification; success.
      **Screenshot: `08-outcome-waive-result.png`.**
- [x] **Waive disabled for a safety critic** — on a task with `no_live_write_in_deliverable`
      (safety-class, not waivable) failing: the Waive button was confirmed `disabled` via the
      DOM (`document.getElementById('outcome-btn-waive').disabled === true`) *before* any
      submit attempt, with an inline explanation ("Disabled: a required safety-class critic is
      failing. Use Escalate."). **Screenshot: `04-review-safety-waive-disabled.png`.**
- [x] **Edit** — submitted a `new_deliverable`; the page automatically reloaded and re-rendered
      against the freshly re-run critics (confirmed the new summary text appeared and the
      critic table refreshed), then Approve was available and used to close it out.
      **Screenshot: `11-outcome-edit-result.png`.**
- [x] **Escalate** — on the same safety-critic-failing task above, submitted Escalate with a
      mandatory justification; success, and confirmed server-side that a
      `task_gate_escalations` row was created (existing server behavior, unchanged by P7a).
      **Screenshot: `05-outcome-escalate-result.png`.**

### Bug found and fixed during this walkthrough

Outcome input fields (`reason`/`justification`/`new_deliverable` textareas) were not cleared
when navigating from one review task to another — a stale value typed for one task's
justification could silently carry over and be visible (though not submitted without the user
re-clicking) when opening a different task. Fixed by adding `clearOutcomeFields()`, called at
the top of `openReviewTask()` in `web/gate/review.js`. Full test suite re-run and confirmed
green after the fix.

## 4. Session expiry

- [x] Simulated an invalid/expired token being sent on a request (the real 8-hour expiry is
      too long to wait out in a verification session, so the request's `X-Human-Session` value
      was corrupted for one call, which exercises the identical server-side rejection path a
      genuinely expired JWT hits — same `403 {"error": "human session required"}` response).
      Confirmed: the UI automatically returned to the login screen
      (`#screen-login` no longer hidden), and confirmed via
      `JSON.stringify({...localStorage, ...sessionStorage})` that **no token was ever
      retrievable from browser storage** at any point, consistent with the in-memory-only
      token design. **Screenshot: `12-expired-jwt-relogin.png`.**

## 5. SMTP (optional path)

- [x] Covered by automated tests (`test_review_email_noop_without_smtp_host`,
      `test_review_email_swallows_smtp_error` in `talos/tests/test_p7a_gate_ui.py`), which
      assert `smtplib.SMTP` is never called when `TALOS_SMTP_HOST` is unset, and that a
      simulated SMTP connection failure is logged and swallowed without affecting the gate
      transition. Not additionally exercised against a real local debug SMTP server in this
      walkthrough — the automated tests are the stronger guarantee here (they assert the
      no-op and swallow contracts directly) and a local mail server is unnecessary to add
      confidence beyond that.

## Summary

All four ROADMAP P7a screens/behaviors were driven end-to-end against a real server and a
real headless browser: login (incl. bad credentials), the polling review queue with SLA
overdue highlighting, the task review page (Markdown render + XSS sanitization + critic table
+ all five ADR-011 outcomes, including the safety-critic waive-disabled case), and session
expiry with in-memory-only token storage confirmed. One real UI bug (stale outcome-field
values across task navigation) was found and fixed during this walkthrough. One pre-existing,
unrelated migration path bug in `V0001_baseline.py` was discovered and is flagged above for a
follow-up fix. The only item not visually confirmable in this sandboxed environment is the
literal OS-level desktop notification banner (no GUI desktop/notification center available
here); the underlying `Notification` API call sites and new-arrival-detection logic were
verified by code review and by confirming new tasks correctly appear in the polled queue.
