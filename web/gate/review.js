// review.js — task review screen: deliverable preview (Markdown -> sanitized
// HTML, with a raw-source toggle), critic verdicts table, and the five
// ADR-011 gate outcome buttons. Button enable/require logic is delegated
// entirely to outcome-rules.js (evaluateOutcome) — never re-implemented here.

let _currentBoardId = null;
let _currentTaskId = null;
let _currentGateStatus = null;
let _showingRaw = false;
let _nexusCacheTickTimer = null;
let _lastNexusCacheData = [];

async function openReviewTask(taskId) {
  _currentBoardId = currentBoardId();
  _currentTaskId = taskId;
  stopQueuePolling();
  document.getElementById("screen-queue").classList.add("hidden");
  document.getElementById("screen-review").classList.remove("hidden");
  document.getElementById("review-outcome-result").textContent = "";
  clearOutcomeFields();
  await loadReviewTask();
  if (_nexusCacheTickTimer) clearInterval(_nexusCacheTickTimer);
  _nexusCacheTickTimer = setInterval(renderNexusCacheTable, 1000);
}

function clearOutcomeFields() {
  for (const outcome of Object.keys(OUTCOME_RULES)) {
    const input = document.getElementById(`outcome-input-${outcome}`);
    if (input) input.value = "";
  }
}

function backToQueue() {
  if (_nexusCacheTickTimer) clearInterval(_nexusCacheTickTimer);
  _nexusCacheTickTimer = null;
  showQueueScreen();
}

async function loadReviewTask() {
  const [taskResp, gateResp] = await Promise.all([
    authFetch(`/boards/${encodeURIComponent(_currentBoardId)}/tasks/${encodeURIComponent(_currentTaskId)}`),
    authFetch(`/boards/${encodeURIComponent(_currentBoardId)}/tasks/${encodeURIComponent(_currentTaskId)}/gate`),
  ]);
  if (!taskResp.ok || !gateResp.ok) return;

  const task = await taskResp.json();
  const gate = await gateResp.json();
  _currentGateStatus = gate;

  document.getElementById("review-title").textContent = task.title || _currentTaskId;
  renderDeliverable(gate.deliverable);
  renderCriticsTable(gate.critics || []);
  renderOutcomeButtons();
  _lastNexusCacheData = gate.nexus_results_freshness || [];
  renderNexusCacheTable();
}

function renderDeliverable(deliverable) {
  const panel = document.getElementById("deliverable-panel");
  const rawPanel = document.getElementById("deliverable-raw");
  panel.innerHTML = "";
  rawPanel.textContent = "";

  if (deliverable == null) {
    const p = document.createElement("p");
    p.className = "escalation-placeholder";
    p.textContent =
      "No deliverable — this task entered review via an error-escalation path " +
      "(budget exhausted or model failure), not a normal completion.";
    panel.appendChild(p);
    return;
  }

  rawPanel.textContent = JSON.stringify(deliverable, null, 2);

  if (deliverable.summary) {
    const html = marked.parse(String(deliverable.summary));
    const clean = DOMPurify.sanitize(html);
    const summaryDiv = document.createElement("div");
    summaryDiv.innerHTML = clean;
    panel.appendChild(summaryDiv);
  }

  if (Array.isArray(deliverable.citations) && deliverable.citations.length > 0) {
    const heading = document.createElement("h4");
    heading.textContent = "Citations";
    panel.appendChild(heading);
    const list = document.createElement("ul");
    for (const c of deliverable.citations) {
      const li = document.createElement("li");
      li.textContent = `${c.finding_id} — ${c.status}`; // textContent: structured data, never innerHTML
      list.appendChild(li);
    }
    panel.appendChild(list);
  }
}

function toggleRawDeliverable() {
  _showingRaw = !_showingRaw;
  document.getElementById("deliverable-panel").classList.toggle("hidden", _showingRaw);
  document.getElementById("deliverable-raw").classList.toggle("hidden", !_showingRaw);
  document.getElementById("raw-toggle-btn").textContent = _showingRaw
    ? "Show rendered"
    : "Show raw";
}

function renderCriticsTable(critics) {
  const tbody = document.getElementById("critics-rows");
  tbody.innerHTML = "";
  if (critics.length === 0) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.textContent = "No critic results.";
    row.appendChild(cell);
    tbody.appendChild(row);
    return;
  }
  for (const c of critics) {
    const row = document.createElement("tr");
    const cells = [
      c.critic_name,
      c.verdict,
      c.required ? "required" : "advisory",
      c.waivable ? "waivable" : "not waivable",
      c.safety_class ? "SAFETY" : "",
    ];
    for (const text of cells) {
      const td = document.createElement("td");
      td.textContent = text;
      row.appendChild(td);
    }
    if (c.safety_class) row.classList.add("safety-row");
    tbody.appendChild(row);
  }
}

function renderNexusCacheTable() {
  const tbody = document.getElementById("nexus-cache-rows");
  tbody.innerHTML = "";
  if (_lastNexusCacheData.length === 0) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 3;
    cell.textContent = "No cached NEXUS results for this board.";
    row.appendChild(cell);
    tbody.appendChild(row);
    return;
  }
  for (const entry of _lastNexusCacheData) {
    const fetchedAt = entry.fetched_at ? new Date(entry.fetched_at) : null;
    const liveSeconds = fetchedAt ? (Date.now() - fetchedAt.getTime()) / 1000 : null;

    const row = document.createElement("tr");
    const toolCell = document.createElement("td");
    toolCell.textContent = entry.tool_name;

    const ageCell = document.createElement("td");
    ageCell.textContent = liveSeconds != null ? `${formatDuration(liveSeconds)} ago` : "—";

    const actionCell = document.createElement("td");
    const btn = document.createElement("button");
    btn.textContent = "Re-fetch";
    btn.addEventListener("click", () => refetchNexusCacheEntry(entry.tool_name));
    actionCell.appendChild(btn);

    row.appendChild(toolCell);
    row.appendChild(ageCell);
    row.appendChild(actionCell);
    tbody.appendChild(row);
  }
}

async function refetchNexusCacheEntry(toolName) {
  const resp = await authFetch(
    `/boards/${encodeURIComponent(_currentBoardId)}/nexus_cache/invalidate?tool_name=${encodeURIComponent(toolName)}`,
    { method: "POST" }
  );
  if (resp.ok) {
    await loadReviewTask();
  }
}

function buildOutcomeContext() {
  const critics = (_currentGateStatus && _currentGateStatus.critics) || [];
  return {
    allRequiredPass: !!(_currentGateStatus && _currentGateStatus.all_required_pass),
    hasNonWaivableFailingRequired: critics.some(
      (c) => c.required && !c.waivable && c.verdict === "fail"
    ),
  };
}

function renderOutcomeButtons() {
  const ctx = buildOutcomeContext();
  for (const outcome of Object.keys(OUTCOME_RULES)) {
    const btn = document.getElementById(`outcome-btn-${outcome}`);
    const fieldWrap = document.getElementById(`outcome-field-${outcome}`);
    const evalResult = evaluateOutcome(outcome, ctx, null);
    btn.disabled = !evalResult.enabled;
    const hint = document.getElementById(`outcome-hint-${outcome}`);
    if (outcome === "waive" && !evalResult.enabled) {
      hint.textContent = "Disabled: a required safety-class critic is failing. Use Escalate.";
    } else {
      hint.textContent = "";
    }
    if (fieldWrap) {
      fieldWrap.classList.toggle("hidden", evalResult.requiredField == null);
    }
    btn.onclick = () => submitOutcome(outcome);
  }
}

async function submitOutcome(outcome) {
  const ctx = buildOutcomeContext();
  const rule = OUTCOME_RULES[outcome];
  let payload = { outcome };
  let fieldValue = null;

  if (rule.requiredField === "reason") {
    fieldValue = document.getElementById("outcome-input-reject").value.trim();
    payload.reason = fieldValue;
  } else if (rule.requiredField === "justification") {
    fieldValue = document.getElementById(`outcome-input-${outcome}`).value.trim();
    payload.justification = fieldValue;
  } else if (rule.requiredField === "new_deliverable") {
    const raw = document.getElementById("outcome-input-edit").value.trim();
    try {
      payload.new_deliverable = JSON.parse(raw);
      fieldValue = raw;
    } catch (e) {
      showOutcomeResult("new_deliverable must be valid JSON.", true);
      return;
    }
  }

  const evalResult = evaluateOutcome(outcome, ctx, fieldValue);
  if (!evalResult.enabled) {
    showOutcomeResult("This outcome is not currently available for this task.", true);
    return;
  }
  if (evalResult.missingRequiredField) {
    showOutcomeResult(`${evalResult.requiredField} is required for ${outcome}.`, true);
    return;
  }

  const resp = await authFetch(
    `/boards/${encodeURIComponent(_currentBoardId)}/tasks/${encodeURIComponent(_currentTaskId)}/gate`,
    { method: "POST", body: JSON.stringify(payload) }
  );
  const body = await resp.json().catch(() => ({}));
  if (resp.ok) {
    showOutcomeResult(`Outcome recorded: ${body.outcome}.`, false);
    if (outcome === "edit") {
      await loadReviewTask(); // re-run critics against the edited deliverable
    }
  } else {
    const detail = body.detail;
    const message = typeof detail === "string" ? detail : detail && detail.error;
    showOutcomeResult(message || `Request failed (${resp.status}).`, true);
  }
}

function showOutcomeResult(message, isError) {
  const el = document.getElementById("review-outcome-result");
  el.textContent = message;
  el.classList.toggle("error", isError);
}
