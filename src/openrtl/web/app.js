"use strict";

const elements = Object.fromEntries([
  "project-label", "revision-label", "status-label", "digest-label", "notice", "create-project-button", "upgrade-project-button",
  "memory-list", "operation-list", "evidence-list", "specification",
  "review-card", "review-button", "approve-button", "activity-list",
  "conversation-list", "chat-form", "chat-input", "composer-status"
].map(id => [id, document.getElementById(id)]));
let state = null;
let cursor = 0;
let card = null;
let pending = new Set();
let projectReady = false;

function node(tag, text, className) {
  const item = document.createElement(tag);
  item.textContent = text;
  if (className) item.className = className;
  return item;
}

function empty(target) { target.replaceChildren(); }
function message(kind, value) {
  elements["conversation-list"].append(node("div", value, "message " + kind));
  elements["conversation-list"].scrollTop = elements["conversation-list"].scrollHeight;
}
function notice(value) { elements.notice.textContent = value; }

async function api(path, options) {
  const response = await fetch(path, { cache: "no-store", credentials: "omit", ...options });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Request failed");
  return body;
}
function post(path, body) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) });
}

function renderMemory(rows) {
  empty(elements["memory-list"]);
  if (!rows.length) {
    elements["memory-list"].append(node("div", "No design facts yet.", "muted"));
    return;
  }
  for (const row of rows) {
    const item = node("div", "", "nav-item");
    item.append(node("strong", row.kind.toUpperCase() + " · " + row.id));
    item.append(node("span", row.text));
    item.append(node("small", "  ·  " + row.provenance.replace("_", " ")));
    elements["memory-list"].append(item);
  }
}

function renderOperations(rows) {
  empty(elements["operation-list"]);
  const entries = Object.entries(rows).reverse().slice(0, 20);
  if (!entries.length) {
    elements["operation-list"].append(node("div", "No operations yet.", "muted"));
    return;
  }
  for (const [id, row] of entries) {
    const item = node("div", "", "nav-item");
    item.append(node("strong", row.phase.replaceAll("_", " ")));
    item.append(node("small", id.slice(0, 10) + " · " + (row.result_revision ?? "no result")));
    if (["queued", "active"].includes(row.phase)) {
      const cancel = node("button", "Request cancellation");
      cancel.type = "button";
      cancel.addEventListener("click", async () => {
        try {
          const result = await post("/api/operations/" + id + "/cancel", {});
          notice("Cancellation outcome: " + result.phase.replaceAll("_", " ") +
            (result.phase === "cancelled" ? ". No provider dispatch began." :
              ". Uncertain provider work requires reconciliation before retry."));
          refresh();
        } catch (error) { notice("Cancellation request was not accepted. Refresh operation state."); }
      });
      item.append(cancel);
    }
    elements["operation-list"].append(item);
  }
}

function renderSpecification(spec) {
  empty(elements.specification);
  if (!spec) {
    elements.specification.append(node("div", "Describe your circuit to begin.", "muted"));
    return;
  }
  elements.specification.append(node("h2", spec.title + " · " + spec.top));
  elements.specification.append(node("p", spec.behavior));
  elements.specification.append(node("p", "Clock/reset: " + spec.clock_reset));
  for (const section of [["Requirements", spec.requirements], ["Questions", spec.questions],
                         ["Assumptions", spec.assumptions]]) {
    if (!section[1].length) continue;
    elements.specification.append(node("h3", section[0]));
    const list = document.createElement("ul");
    for (const row of section[1]) list.append(node("li", row.id + ": " + row.text));
    elements.specification.append(list);
  }
}

function renderSnapshot(snapshot) {
  state = snapshot.state;
  cursor = snapshot.next_cursor;
  elements["revision-label"].textContent = "Revision " + state.revision;
  elements["status-label"].textContent = state.active ? "Operation active" : state.status.replaceAll("_", " ");
  elements["digest-label"].textContent = state.approved_spec || "No approved design";
  elements["composer-status"].textContent = state.active ? "An operation is active. Refresh keeps its identity." :
    snapshot.capabilities.provider ? "Messages are shown only in this browser session." :
    "Provider unavailable. Restart with explicit provider selection to chat.";
  renderMemory(state.engineering_memory || []);
  renderOperations(state.workspace_operations || {});
  renderSpecification(state.spec);
  empty(elements["evidence-list"]);
  elements["evidence-list"].append(node("div", state.simulation ?
    state.simulation.status + " · " + state.simulation.evidence_kind : "No simulation yet.", "muted"));
  for (const event of snapshot.events) {
    elements["activity-list"].prepend(node("div", "r" + event.sequence + " · " + event.event, "event-item"));
  }
  while (elements["activity-list"].children.length > 64) elements["activity-list"].lastChild.remove();
  if (snapshot.more_events) setTimeout(refresh, 0);
}

async function refresh() {
  if (!projectReady) return;
  try {
    renderSnapshot(await api("/api/snapshot?cursor=" + cursor));
    for (const id of [...pending]) {
      const job = await api("/api/operations/" + id);
      if (["completed", "failed", "cancelled", "reconciliation_needed"].includes(job.phase)) {
        pending.delete(id);
        if (job.reply) message("agent", job.reply);
        if (job.phase !== "completed") notice("Operation " + job.phase.replaceAll("_", " ") +
          ". Review its saved state before another request.");
      }
    }
  } catch (error) {
    notice("Connection unavailable. Reconnect will read the saved project state; no operation is retried.");
  }
}

elements["chat-form"].addEventListener("submit", async event => {
  event.preventDefault();
  const value = elements["chat-input"].value.trim();
  if (!value || !state) return;
  const id = crypto.randomUUID().replaceAll("-", "");
  try {
    const job = await post("/api/discussions", {
      message: value, client_operation_id: id, expected_revision: state.revision
    });
    message("user", value);
    elements["chat-input"].value = "";
    pending.add(job.id);
    notice("Design Lead operation submitted. Its state will survive a page refresh.");
    refresh();
  } catch (error) {
    notice("Request was not accepted. Refresh the project state and review permissions.");
  }
});

elements["review-button"].addEventListener("click", async () => {
  if (!state) return;
  try {
    card = await api("/api/review?kind=specification");
    empty(elements["review-card"]);
    elements["review-card"].append(node("div", JSON.stringify(card.payload, null, 2), "review-line"));
    elements["review-card"].append(node("div", "Revision " + card.revision + " · " + card.payload_digest, "muted"));
    elements["approve-button"].hidden = false;
  } catch (error) {
    notice("No specification is available for review yet.");
  }
});

elements["approve-button"].addEventListener("click", async () => {
  if (!card) return;
  try {
    await post("/api/approve", {
      kind: card.kind, expected_revision: card.revision,
      state_digest: card.state_digest, payload_digest: card.payload_digest
    });
    card = null;
    elements["approve-button"].hidden = true;
    notice("The displayed specification was approved. Engineering stages still require their normal controls.");
    refresh();
  } catch (error) {
    notice("That review is stale or incomplete. Refresh and inspect the current proposal.");
  }
});

elements["create-project-button"].addEventListener("click", async () => {
  try {
    await post("/api/project", {});
    elements["create-project-button"].hidden = true;
    projectReady = true;
    notice("Project created. Describe a circuit to begin.");
    refresh();
  } catch (error) { notice("Project creation failed. Check the selected path and server state."); }
});

elements["upgrade-project-button"].addEventListener("click", async () => {
  try {
    await post("/api/project/upgrade", {});
    elements["upgrade-project-button"].hidden = true;
    projectReady = true;
    notice("Project session upgraded. Review its current engineering state before continuing.");
    refresh();
  } catch (error) { notice("Project upgrade failed. The saved project remains available for inspection."); }
});

async function bootstrap() {
  try {
    const project = await api("/api/project");
    elements["project-label"].textContent = project.name;
    projectReady = project.ready;
    if (!project.created) {
      elements["create-project-button"].hidden = false;
      elements["status-label"].textContent = "Project not created";
      notice("Create the selected local project to begin. The server cannot select other filesystem paths.");
      return;
    }
    if (!projectReady) {
      elements["upgrade-project-button"].hidden = false;
      elements["status-label"].textContent = "Session upgrade required";
      notice("This project uses an older session schema. Review and explicitly upgrade it to continue.");
      return;
    }
    refresh();
  } catch (error) { notice("Project service unavailable. Reconnect will inspect saved state."); }
}

bootstrap();
setInterval(refresh, 2000);
