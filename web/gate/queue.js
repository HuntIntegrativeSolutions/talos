// queue.js — review-queue screen: polls GET /boards/{board}/review-queue every
// 15s, fires a browser Notification for newly-arrived review tasks, renders
// oldest-first with a live-ticking time-in-review and an SLA-overdue highlight.

let _queuePollTimer = null;
let _queueTickTimer = null;
let _seenTaskIds = new Set();
let _firstPoll = true;
let _lastQueueData = null;

function currentBoardId() {
  return document.getElementById("board-id-input").value.trim();
}

function showQueueScreen() {
  document.getElementById("screen-login").classList.add("hidden");
  document.getElementById("screen-queue").classList.remove("hidden");
  document.getElementById("screen-review").classList.add("hidden");
  _firstPoll = true;
  _seenTaskIds = new Set();
  requestNotificationPermissionIfNeeded();
  startQueuePolling();
}

function requestNotificationPermissionIfNeeded() {
  if (typeof Notification === "undefined") return;
  if (Notification.permission === "default") {
    Notification.requestPermission();
  }
}

function startQueuePolling() {
  stopQueuePolling();
  pollQueue();
  _queuePollTimer = setInterval(pollQueue, 15000);
  _queueTickTimer = setInterval(renderQueueRows, 1000);
}

function stopQueuePolling() {
  if (_queuePollTimer) clearInterval(_queuePollTimer);
  if (_queueTickTimer) clearInterval(_queueTickTimer);
  _queuePollTimer = null;
  _queueTickTimer = null;
}

async function pollQueue() {
  const boardId = currentBoardId();
  if (!boardId) return;
  const resp = await authFetch(`/boards/${encodeURIComponent(boardId)}/review-queue`);
  if (!resp.ok) return;
  const data = await resp.json();
  _lastQueueData = data;

  const currentIds = new Set(data.tasks.map((t) => t.task_id));
  if (!_firstPoll) {
    for (const t of data.tasks) {
      if (!_seenTaskIds.has(t.task_id)) {
        fireNewReviewNotification(t);
      }
    }
  }
  _seenTaskIds = currentIds;
  _firstPoll = false;

  renderQueueRows();
}

function fireNewReviewNotification(task) {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  new Notification("TALOS: task awaiting review", {
    body: task.title || task.task_id,
  });
}

function formatDuration(seconds) {
  if (seconds == null) return "—";
  const m = Math.floor(seconds / 60);
  const h = Math.floor(m / 60);
  const remM = m % 60;
  if (h > 0) return `${h}h ${remM}m`;
  return `${m}m`;
}

function renderQueueRows() {
  if (!_lastQueueData) return;
  const tbody = document.getElementById("queue-rows");
  tbody.innerHTML = "";
  const slaMinutes = _lastQueueData.sla_minutes;

  if (_lastQueueData.tasks.length === 0) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.textContent = "No tasks in review.";
    row.appendChild(cell);
    tbody.appendChild(row);
    return;
  }

  for (const t of _lastQueueData.tasks) {
    const enteredAt = t.review_entered_at ? new Date(t.review_entered_at) : null;
    const liveSeconds = enteredAt ? (Date.now() - enteredAt.getTime()) / 1000 : null;
    const overdue = slaMinutes != null && liveSeconds != null && liveSeconds / 60 > slaMinutes;

    const row = document.createElement("tr");
    if (overdue) row.classList.add("overdue");

    const titleCell = document.createElement("td");
    const link = document.createElement("a");
    link.href = "#";
    link.textContent = t.title || t.task_id;
    link.addEventListener("click", (e) => {
      e.preventDefault();
      openReviewTask(t.task_id);
    });
    titleCell.appendChild(link);

    const assigneeCell = document.createElement("td");
    assigneeCell.textContent = t.assignee || "—";

    const timeCell = document.createElement("td");
    timeCell.textContent = formatDuration(liveSeconds);

    const overdueCell = document.createElement("td");
    overdueCell.textContent = overdue ? "OVERDUE" : "";

    row.appendChild(titleCell);
    row.appendChild(assigneeCell);
    row.appendChild(timeCell);
    row.appendChild(overdueCell);
    tbody.appendChild(row);
  }
}
