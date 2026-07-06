// outcome-rules.js — the single source of truth for the five-outcome
// (ADR-011) enable/require matrix used by review.js.
//
// This file's shape is a contract with talos/tests/test_p7a_outcome_matrix.py,
// which parses OUTCOME_RULES as text (not by executing this file) and asserts
// it matches a canonical Python copy, then drives talos/api.py::submit_gate_outcome
// via TestClient to prove the server enforces the identical matrix. Any change
// to a requiredField or enabledWhen condition here must be mirrored there.
//
// ctx passed to enabledWhen is derived client-side from the getGateStatus
// response:
//   ctx.allRequiredPass               === gate_row.all_required_pass
//   ctx.hasNonWaivableFailingRequired === critics.some(c =>
//       c.required && !c.waivable && c.verdict === "fail")

const OUTCOME_RULES = {
  approve:  { requiredField: null,              enabledWhen: (ctx) => ctx.allRequiredPass },
  reject:   { requiredField: "reason",          enabledWhen: () => true },
  waive:    { requiredField: "justification",   enabledWhen: (ctx) => !ctx.hasNonWaivableFailingRequired },
  edit:     { requiredField: "new_deliverable", enabledWhen: () => true },
  escalate: { requiredField: "justification",   enabledWhen: () => true },
};

function evaluateOutcome(outcome, ctx, fieldValue) {
  const rule = OUTCOME_RULES[outcome];
  if (!rule) throw new Error(`unknown outcome ${outcome}`);
  const enabled = rule.enabledWhen(ctx);
  const missingRequiredField = rule.requiredField != null && !fieldValue;
  return { enabled, missingRequiredField, requiredField: rule.requiredField };
}
