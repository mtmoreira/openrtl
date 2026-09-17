"use strict";

const elements = Object.fromEntries([
  "project-label", "revision-label", "status-label", "digest-label", "notice", "create-project-button", "upgrade-project-button",
  "memory-list", "operation-list", "evidence-list", "specification",
  "file-list", "planned-list", "elaborated-list", "link-list", "history-list",
  "source-identity", "source-content", "attach-source-button", "diff-source-button",
  "proposal-content", "change-review-button",
  "simulation-review-button", "simulation-plan", "simulation-run-button", "simulation-recover-button", "simulation-result",
  "review-card", "review-button", "approve-button", "activity-list",
  "conversation-list", "chat-form", "chat-input", "chat-kind", "composer-status"
].map(id => [id, document.getElementById(id)]));
let state = null;
let cursor = 0;
let card = null;
let pending = new Set();
let projectReady = false;
let selectedSource = null;
let selectedAttachment = null;
let shownRevision = null;
let latestRevision = null;
let simulationPlan = null;

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
            (result.phase === "cancelled" ? ". Execution had not started." :
              ". The external operation may still need reconciliation before retry."));
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

async function openSource(file) {
  try {
    const source = await api("/api/source?" + new URLSearchParams({
      revision: file.revision, path: file.path, digest: file.digest}));
    selectedSource = source;
    elements["source-identity"].textContent = source.path + " · r" + source.revision +
      (file.line ? " · line " + file.line : "") + " · " + source.digest;
    elements["source-content"].textContent = source.content;
    elements["source-content"].scrollTop = file.line ? (file.line - 1) * 19 : 0;
    elements["attach-source-button"].hidden = !source.content.trim();
    elements["diff-source-button"].hidden = source.revision === 0;
  } catch (error) { notice("Source identity is stale or unavailable. Select a saved revision again."); }
}

function renderWorkbench(data) {
  shownRevision = data.revision;
  for (const id of ["file-list", "planned-list", "elaborated-list", "link-list", "history-list", "proposal-content"]) empty(elements[id]);
  for (const file of data.files) {
    const button = node("button", file.path + " · r" + file.revision, "file-button");
    button.type = "button";
    button.addEventListener("click", () => openSource(file));
    elements["file-list"].append(button);
  }
  if (!data.files.length) elements["file-list"].append(node("div", "No source files yet.", "muted"));
  elements["planned-list"].append(node("div", data.planned.top ? "Top: " + data.planned.top : "No plan yet.", "muted"));
  for (const module of data.planned.test_modules) {
    const path = "dv/" + module + ".py";
    const file = data.files.find(row => row.path === path);
    const button = node("button", "DV module: " + module, "file-button");
    button.disabled = !file;
    if (file) button.addEventListener("click", () => openSource(file));
    elements["planned-list"].append(button);
  }
  if (data.elaborated.status === "elaborated") {
    elements["elaborated-list"].append(node("div", "Verilator " + data.elaborated.tool_version +
      " · exact input " + data.elaborated.input_digest, "muted"));
    for (const instance of data.elaborated.instances) {
      const button = node("button", instance.id + " → " + instance.module, "file-button");
      button.type = "button";
      button.addEventListener("click", () => openSource(instance.definition));
      elements["elaborated-list"].append(button);
    }
  } else {
    elements["elaborated-list"].append(node("div", "Unavailable: " +
      data.elaborated.reason.replaceAll("_", " ") + ". Source navigation remains available.", "muted"));
  }
  for (const requirement of data.requirements) {
    elements["link-list"].append(node("div", requirement.id + ": " + requirement.text +
      " · planned tests: " + (requirement.planned_tests.join(", ") || "none") + " · no coverage claim", "nav-item"));
  }
  if (!data.requirements.length) elements["link-list"].append(node("div", "No verification links yet.", "muted"));
  for (const row of data.history.slice().reverse()) {
    const button = node("button", "r" + row.revision + " · " + row.event, "file-button");
    button.addEventListener("click", () => loadWorkbench(row.revision));
    elements["history-list"].append(button);
  }
  if (data.proposal) {
    const changed = Object.keys(data.proposal.plan.specification).filter(key =>
      JSON.stringify(data.proposal.plan.specification[key]) !== JSON.stringify(state.spec?.[key]));
    elements["proposal-content"].append(node("p", data.proposal.summary));
    elements["proposal-content"].append(node("p", "Changed specification fields: " + (changed.join(", ") || "none")));
    elements["proposal-content"].append(node("pre", JSON.stringify(data.proposal.plan.stage_paths, null, 2)));
    elements["proposal-content"].append(node("p", "Source edits are pending generation; this is a plan, not a source diff."));
  } else elements["proposal-content"].append(node("div", "No change proposal.", "muted"));
  elements["change-review-button"].disabled = !data.proposal || data.revision !== data.current_revision;
}

async function loadWorkbench(revision) {
  try { renderWorkbench(await api("/api/workbench?revision=" + revision)); }
  catch (error) { notice("That engineering revision is unavailable."); }
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
  elements["chat-kind"].disabled = state.status === "discovery";
  elements["chat-kind"].options[1].disabled = state.stage !== 6 || !state.manifest;
  if (elements["chat-kind"].options[1].disabled) elements["chat-kind"].value = "question";
  renderMemory(state.engineering_memory || []);
  renderOperations(state.workspace_operations || {});
  renderSpecification(state.spec);
  if (simulationPlan && simulationPlan.revision !== state.revision) {
    simulationPlan = null;
    elements["simulation-run-button"].hidden = true;
    elements["simulation-plan"].textContent = "Project revision changed. Review the run configuration again.";
  }
  elements["simulation-review-button"].disabled = !snapshot.capabilities.simulation ||
    state.status !== "building" || state.stage !== 6 || !!state.active;
  elements["simulation-recover-button"].hidden = !snapshot.capabilities.simulation ||
    !state.active || state.active.kind !== "simulation";
  empty(elements["simulation-result"]);
  if (state.simulation) {
    const current = state.simulation.input_digest === snapshot.design_input_digest;
    elements["simulation-result"].append(node("div", "Run " + state.simulation.run_id + " · " +
      state.simulation.status + (current ? " · current input" : " · historical input"), "nav-item"));
    elements["simulation-result"].append(node("div", "Evidence: " + state.simulation.evidence_kind +
      " · tests: " + state.simulation.tests.join(", ") +
      " · model tests: " + state.simulation.model_tests, "muted"));
    if (state.simulation.diagnostics) elements["simulation-result"].append(
      node("pre", state.simulation.diagnostics, "review-line"));
  } else {
    elements["simulation-result"].append(node("div", state.last_error || "No run evidence yet.", "muted"));
  }
  if (snapshot.progress && state.active && snapshot.progress.operation_id === state.active.id) {
    elements["simulation-result"].append(node("div", "Simulation " + snapshot.progress.phase +
      " · " + snapshot.progress.elapsed_ms + " ms", "muted"));
  }
  if (shownRevision === null || (shownRevision === latestRevision && latestRevision !== state.revision))
    loadWorkbench(state.revision);
  latestRevision = state.revision;
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
    const discovery = state.status === "discovery";
    const job = await post(discovery ? "/api/discussions" : "/api/questions", discovery ?
      {message: value, client_operation_id: id, expected_revision: state.revision} :
      {message: value, client_operation_id: id, expected_revision: state.revision,
        attachment: selectedAttachment, kind: elements["chat-kind"].value, intent: "feature"});
    message("user", value);
    elements["chat-input"].value = "";
    selectedAttachment = null;
    pending.add(job.id);
    notice("Design Lead operation submitted. Its state will survive a page refresh.");
    refresh();
  } catch (error) {
    notice("Request was not accepted. Refresh the project state and review permissions.");
  }
});

async function showReview(kind) {
  if (!state) return;
  try {
    card = await api("/api/review?kind=" + kind);
    empty(elements["review-card"]);
    elements["review-card"].append(node("div", JSON.stringify(card.payload, null, 2), "review-line"));
    elements["review-card"].append(node("div", "Revision " + card.revision + " · " + card.payload_digest, "muted"));
    elements["approve-button"].textContent = "Approve displayed " + kind;
    elements["approve-button"].hidden = false;
  } catch (error) {
    notice("No " + kind + " is available for review yet.");
  }
}
elements["review-button"].addEventListener("click", () => showReview("specification"));
elements["change-review-button"].addEventListener("click", () => showReview("change"));

elements["simulation-review-button"].addEventListener("click", async () => {
  try {
    simulationPlan = await api("/api/simulation/plan");
    elements["simulation-plan"].textContent = JSON.stringify(simulationPlan, null, 2);
    elements["simulation-run-button"].hidden = false;
  } catch (error) { notice("Simulation is unavailable. Review the baseline and explicitly selected runtime."); }
});

elements["simulation-run-button"].addEventListener("click", async () => {
  if (!simulationPlan || !state) return;
  try {
    const job = await post("/api/simulations", {client_operation_id: crypto.randomUUID().replaceAll("-", ""),
      expected_revision: simulationPlan.revision, plan_digest: simulationPlan.plan_digest});
    pending.add(job.id);
    simulationPlan = null;
    elements["simulation-run-button"].hidden = true;
    notice("Simulation submitted for the displayed source and runtime configuration.");
    refresh();
  } catch (error) { notice("Simulation was not accepted. Review the current revision and runtime again."); }
});

elements["simulation-recover-button"].addEventListener("click", async () => {
  if (!state?.active || state.active.kind !== "simulation") return;
  try {
    await post("/api/simulation/recover", {operation_id: state.active.id});
    notice("The exact owned runtime was reconciled. Interrupted output was not accepted as evidence.");
    refresh();
  } catch (error) {
    notice("Recovery is unavailable while this process still owns the operation, or runtime identity changed. Reopen the workspace and inspect the saved operation before retrying.");
  }
});

elements["attach-source-button"].addEventListener("click", () => {
  if (!selectedSource) return;
  const lines = selectedSource.content.split(/\r?\n/);
  if (lines.at(-1) === "") lines.pop();
  const count = lines.length;
  selectedAttachment = {revision: selectedSource.revision, path: selectedSource.path,
    digest: selectedSource.digest, start_line: 1, end_line: Math.min(count, 200)};
  notice("Attached " + selectedSource.path + " at revision " + selectedSource.revision +
    ", lines 1-" + selectedAttachment.end_line + ". The exact source identity is checked when sent.");
});

elements["diff-source-button"].addEventListener("click", async () => {
  if (!selectedSource) return;
  try {
    const diff = await api("/api/diff?" + new URLSearchParams({before: selectedSource.revision - 1,
      after: selectedSource.revision, path: selectedSource.path}));
    elements["source-identity"].textContent = diff.path + " · r" + diff.before_revision + " → r" + diff.after_revision;
    elements["source-content"].textContent = diff.content || "No source change between these revisions.";
  } catch (error) { notice("No comparable source revision is available."); }
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
