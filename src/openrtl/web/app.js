"use strict";

const elements = Object.fromEntries([
  "project-label", "revision-label", "status-label", "digest-label", "create-project-button", "upgrade-project-button",
  "memory-list", "operation-list", "evidence-list", "specification",
  "file-list", "planned-list", "elaborated-list", "link-list", "history-list",
  "source-identity", "source-content", "attach-source-button", "diff-source-button",
  "proposal-content", "change-review-button",
  "simulation-review-button", "simulation-plan", "simulation-run-button", "simulation-recover-button", "simulation-result",
  "waveform-run-list", "waveform-status", "waveform-search", "waveform-signal-list",
  "waveform-start", "waveform-end", "waveform-radix", "waveform-load", "waveform-zoom-in",
  "waveform-zoom-out", "waveform-pan-left", "waveform-pan-right", "waveform-chart",
  "waveform-cursor-a", "waveform-cursor-b", "waveform-values", "waveform-save", "waveform-attach",
  "review-card", "review-button", "approve-button", "activity-list", "history-detail",
  "conversation-list", "chat-form", "chat-input", "chat-kind", "composer-status",
  "provider-form", "provider-kind", "provider-openai-settings", "provider-ollama-settings",
  "provider-model", "provider-ollama-model", "provider-spend", "provider-key", "provider-enabled", "provider-status",
  "provider-timeout", "provider-detailed-capture",
  "provider-recovery", "provider-reconcile-button"
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
let waveProjectId = null;
let waveRunsRevision = null;
let selectedRun = null;
let waveCatalog = null;
let waveSignals = [];
let waveWindow = null;
let providerFieldsLoaded = false;
let providerRevision = null;
let historyRequest = 0;
let workbenchRequest = 0;
let refreshPromise = null;
let refreshRequested = false;
const systemMessages = new Map();
const deliveredReplies = new Set();
const displayedUserOperations = new Set();
const restoredMessageIds = new Set();
const inspectedReplies = new Set();
const replyQueries = new Set();
const operationPhases = new Map();
const operationProgress = new Map();
const observedEvents = new Set();
const terminalPhases = new Set(["completed", "failed", "cancelled", "reconciliation_needed"]);
const providerFailureGuidance = Object.freeze({
  provider_credential_unavailable: "The server could not resolve an API key. Check the key source.",
  provider_client_unavailable: "The local provider client could not start. Check the optional SDK installation.",
  provider_authentication_rejected: "The provider rejected authentication. Check the API key.",
  provider_access_denied: "The provider denied account or model access.",
  provider_model_unavailable: "The provider did not accept the selected model.",
  provider_request_rejected: "The provider rejected the request. Check model and schema compatibility.",
  provider_rate_or_quota_limited: "The provider reported a rate or quota limit.",
  provider_service_unavailable: "The provider service reported an error.",
  provider_connection_failed: "The provider request could not connect or complete.",
  provider_response_invalid: "The provider response was invalid.",
  provider_timeout: "The model exceeded the configured time limit. Inspect History and increase the provider timeout if needed; the request was not retried.",
  provider_cancelled: "The provider request was cancelled; its external completion may still be uncertain.",
  provider_result_invalid: "The provider result or usage could not be validated.",
  expert_invocation_failed: "The provider call failed; its exact cause was not safely classified.",
  provider_spend_budget_exhausted: "The project ceiling cannot cover another full call reservation.",
  provider_spend_uncertain: "A previous call's cost is still uncertain.",
  expert_call_budget_exhausted: "The project's call budget is exhausted.",
  expert_output_invalid: "The response failed local validation."
});
function operationFailure(code) {
  return providerFailureGuidance[code] ? providerFailureGuidance[code] +
    (state?.provider?.uncertain ? " Review the saved cost reservation before another call." : "") :
    "Operation failed. Review its saved state before another request.";
}

function node(tag, text, className) {
  const item = document.createElement(tag);
  item.textContent = text;
  if (className) item.className = className;
  return item;
}

function empty(target) { target.replaceChildren(); }

function printable(value) {
  if (value === null || value === undefined) return "Unavailable · not recorded";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function detailRow(label, value) {
  const row = node("div", "", "history-field");
  row.append(node("strong", label), node("span", printable(value)));
  return row;
}

async function loadHistory(sequence) {
  const request = ++historyRequest;
  try {
    const detail = await api("/api/history/" + sequence);
    if (request !== historyRequest) return;
    const target = elements["history-detail"];
    empty(target);
    target.classList.remove("muted");
    target.append(node("h3", "Saved execution trace · r" + detail.event.sequence));
    target.append(node("p", "Inspecting this event does not change the source revision shown in the workbench.", "muted"));
    const inspect = node("button", "Inspect source at revision " + detail.event.sequence);
    inspect.type = "button";
    inspect.addEventListener("click", () => loadWorkbench(detail.event.sequence));
    const current = node("button", "Inspect current source revision");
    current.type = "button";
    current.addEventListener("click", () => { if (state) loadWorkbench(state.revision); });
    const actions = node("div", "", "source-actions");
    actions.append(inspect, current);
    target.append(actions);
    target.append(detailRow("Event", detail.event.event));
    for (const [name, value] of Object.entries(detail.event.fields)) target.append(detailRow(name, value));
    target.append(node("h4", "Related events"));
    const timeline = node("div", "", "history-timeline");
    for (const event of detail.trace || []) timeline.append(node("div",
      "r" + event.sequence + " · " + event.event + " · " + printable(event.fields), "history-trace-row"));
    target.append(timeline, node("h4", "Saved state"));
    for (const [name, value] of Object.entries(detail.state)) target.append(detailRow(name, value));
    if (detail.evidence) {
      target.append(node("h4", "Simulation evidence"));
      for (const [name, value] of Object.entries(detail.evidence)) target.append(detailRow(name, value));
    }
    target.append(node("h4", "Artifact changes"));
    if (!detail.files?.length) target.append(node("p", "No artifact changes were recorded for this operation.", "muted"));
    for (const file of detail.files || []) {
      const row = node("div", "", "history-artifact");
      row.append(detailRow(file.change + " · " + file.path, file.digest ?? file.previous_digest));
      const revision = file.change === "deleted" ? file.previous_revision : file.revision;
      const digest = file.change === "deleted" ? file.previous_digest : file.digest;
      if (revision !== null && revision !== undefined && digest) {
        const button = node("button", "Open " + file.path + " · r" + revision);
        button.type = "button";
        button.addEventListener("click", () => openSource({revision, digest, path: file.path}));
        row.append(button);
      }
      target.append(row);
    }
    target.append(node("h4", "Runtime, usage and cost"));
    appendTelemetryMetrics(target, detail.metrics);
    target.append(node("h4", "Detailed local capture"));
    target.append(detailRow("Capture availability", detail.capture ?? detail.capture_availability ??
      detail.visibility?.detailed_capture ?? "Unavailable for this attempt"));
    const records = Array.isArray(detail.details) ? detail.details : [];
    if (!records.length) target.append(node("p", "No detailed records were retained for this attempt. Enabling capture now cannot recover earlier inputs, outputs or provider-returned reasoning.", "muted"));
    for (const [index, record] of records.entries()) renderCaptureRecord(target, record, index);
    target.append(node("h4", "Visibility"));
    for (const [name, value] of Object.entries(detail.visibility || {})) target.append(detailRow(name, value));
    target.append(node("p", "Reasoning is shown only when the provider returned it and local capture retained it. Inaccessible model internals are unavailable. Missing token, cost, tool or process data is not a zero value.", "muted"));
    target.scrollIntoView({block: "nearest", behavior: "smooth"});
  } catch (error) {
    if (request !== historyRequest) return;
    notice("That saved history entry is unavailable or failed validation.");
  }
}
function appendTelemetryMetrics(target, metrics) {
  if (!metrics || !Object.keys(metrics).length) {
    target.append(node("p", "Runtime, token counts and cost are unavailable unless recorded in the linked events or capture records below.", "muted"));
    return;
  }
  for (const [name, value] of Object.entries(metrics)) target.append(detailRow(name, value));
}
function renderCaptureRecord(target, record, index) {
  const entry = record && typeof record === "object" ? record : {content: record};
  const title = entry.title || entry.category || entry.kind || entry.type || "Capture record";
  const section = node("details", "", "capture-record");
  section.append(node("summary", (index + 1) + ". " + title));
  for (const [name, value] of Object.entries(entry)) {
    if (name === "title") continue;
    const field = node("div", "", "capture-field");
    field.append(node("strong", name.replaceAll("_", " ")));
    field.append(node("pre", printable(value), "capture-content"));
    section.append(field);
  }
  target.append(section);
}
function message(kind, value) {
  const item = node("div", "", "message " + kind);
  item.append(node("strong", kind === "system" ? "System" : kind === "user" ? "You" : "Design Lead", "message-identity"),
    node("div", value, "message-text"));
  elements["conversation-list"].append(item);
  scrollConversation();
  return item;
}
function scrollConversation() {
  const list = elements["conversation-list"];
  list.scrollTop = list.scrollHeight;
}
function systemMessage(key, value) {
  const previous = systemMessages.get(key);
  if (previous) {
    if (previous.value !== value) {
      previous.item.querySelector(".message-text").textContent = value;
      previous.value = value;
    }
    return previous.item;
  }
  const item = message("system", value);
  systemMessages.set(key, {item, value});
  return item;
}
function notice(value, key = value) { systemMessage("notice:" + key, value); }
function reconcileOperation(job, progress = null) {
  const previousPhase = operationPhases.get(job.id);
  if (terminalPhases.has(previousPhase) && !terminalPhases.has(job.phase)) return;
  operationPhases.set(job.id, job.phase);
  if (progress) operationProgress.set(job.id, progress);
  progress = operationProgress.get(job.id) || null;
  const phaseMessages = {queued: "Your request is queued", active: "Your request is running",
    completed: "Your request completed", failed: "Your request failed", cancelled: "Your request was cancelled",
    reconciliation_needed: "Your request needs review", cancellation_requested: "Cancellation requested"};
  let description = phaseMessages[job.phase] || "Request status unavailable";
  if (job.phase === "failed") description += ". " + operationFailure(job.error_code);
  else if (job.phase === "reconciliation_needed") description += ". Completion is uncertain; inspect the saved operation before another request.";
  else if (job.phase === "cancelled") description += ". Execution did not start.";
  else if (job.phase === "cancellation_requested") description += ". Cancellation has not been confirmed.";
  else if (job.phase === "completed") description += ". Details are available in History.";
  if (progress && !terminalPhases.has(job.phase)) description += "\n" +
    progress.stage + " · " + progress.phase.replaceAll("_", " ") +
    " · " + progress.elapsed_ms + " ms elapsed";
  systemMessage("operation:" + job.id, description);
  if (terminalPhases.has(job.phase)) {
    pending.delete(job.id);
    if (!inspectedReplies.has(job.id)) replyQueries.add(job.id);
  }
  else pending.add(job.id);
  if (job.reply && !deliveredReplies.has(job.id)) {
    deliveredReplies.add(job.id);
    message("agent", job.reply);
  }
}

async function loadConversation() {
  try {
    const result = await api("/api/conversation");
    for (const row of result.messages) {
      if (restoredMessageIds.has(row.id)) continue;
      restoredMessageIds.add(row.id);
      const seen = row.kind === "agent" ? deliveredReplies : displayedUserOperations;
      if (seen.has(row.operation_id)) continue;
      seen.add(row.operation_id);
      const item = message(row.kind, row.text + (row.truncated ? "\n[Captured text is truncated.]" : ""));
      item.append(node("small", "Saved local capture", "message-origin"));
    }
  } catch (error) {
    notice("Saved conversation capture is unavailable. Engineering state can still reconnect; no request was replayed.", "conversation-capture");
  }
}

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

function showProviderFields() {
  const local = elements["provider-kind"].value === "ollama";
  elements["provider-openai-settings"].hidden = local;
  elements["provider-ollama-settings"].hidden = !local;
  elements["provider-model"].required = !local;
  elements["provider-spend"].required = !local;
  elements["provider-ollama-model"].required = local;
}
elements["provider-kind"].addEventListener("change", showProviderFields);

async function loadProviderSettings(resetFields = false) {
  try {
    const settings = await api("/api/provider");
    providerRevision = settings.revision;
    if (!providerFieldsLoaded || resetFields) {
      empty(elements["provider-model"]);
      for (const item of settings.models) {
        const option = node("option", item.id + " · $" + item.input_usd_per_million +
          "/$" + item.output_usd_per_million + " per million tokens");
        option.value = item.id;
        elements["provider-model"].append(option);
      }
      elements["provider-kind"].value = settings.selected_provider || "openai";
      if (settings.selected_provider === "ollama")
        elements["provider-ollama-model"].value = settings.selected_model || "";
      else elements["provider-model"].value = settings.selected_model || "gpt-5.6-terra";
      elements["provider-spend"].value = settings.max_spend_usd ?
        Number(settings.max_spend_usd).toFixed(2) : "";
      elements["provider-enabled"].checked = settings.enabled;
      elements["provider-timeout"].value = String(settings.timeout_seconds ?? 120);
      elements["provider-detailed-capture"].checked = settings.detailed_capture === true;
      showProviderFields();
      providerFieldsLoaded = true;
    }
    elements["provider-form"].querySelectorAll("input,select,button").forEach(item => {
      item.disabled = !settings.editable || settings.uncertain;
    });
    elements["provider-recovery"].hidden = !settings.uncertain;
    elements["provider-reconcile-button"].disabled = !settings.editable || providerRevision === null;
    elements["provider-status"].textContent = settings.selected_provider === "ollama" ?
      "Local Ollama model: " + (settings.selected_model || "not selected") +
        " · fixed loopback endpoint · no API key or OpenRTL USD estimate" +
        (settings.estimated_spend_usd !== "0.000000" ?
          " · historical OpenAI estimate $" + settings.estimated_spend_usd : "") :
      "Estimated OpenAI spend: $" + settings.estimated_spend_usd + (settings.max_spend_usd ?
        " of $" + settings.max_spend_usd : " · no ceiling selected") +
        (settings.uncertain ? " · uncertain call; further calls blocked" : "") +
        (settings.prior_unpriced_calls ? " · " + settings.prior_unpriced_calls +
          " earlier calls have no price record" : "") +
        (settings.key_present ? " · key in server memory" : " · no key stored in server memory");
    elements["provider-status"].append(node("p", "Request deadline: " + (settings.timeout_seconds ?? 120) +
      " seconds · detailed local capture " + (settings.detailed_capture ? "on" : "off") +
      ". Capture consent resets when this server restarts.", "muted"));
    elements["composer-status"].textContent = settings.enabled ?
      (settings.selected_provider === "ollama" ? "Local Ollama enabled for this server." :
        "OpenAI enabled. Project spend estimate is capped locally.") +
      (settings.detailed_capture ? " Detailed text is captured locally." : " Conversation prose is not saved.") :
      "Provider unavailable until explicitly enabled here or at launch.";
  } catch (error) {
    elements["provider-status"].textContent = "Provider settings unavailable. No provider action was retried.";
  }
}

elements["provider-form"].addEventListener("submit", async event => {
  event.preventDefault();
  const key = elements["provider-key"].value;
  const local = elements["provider-kind"].value === "ollama";
  const timeout = Number(elements["provider-timeout"].value);
  if (!Number.isInteger(timeout) || timeout < 1 || timeout > 300) {
    elements["provider-key"].value = "";
    notice("Request deadline must be a whole number from 1 to 300 seconds.");
    return;
  }
  try {
    await post("/api/provider", {provider: elements["provider-kind"].value,
      model: local ? elements["provider-ollama-model"].value : elements["provider-model"].value,
      max_spend_usd: local ? null : elements["provider-spend"].value,
      api_key: local ? null : key || null, enabled: elements["provider-enabled"].checked,
      timeout_seconds: timeout, detailed_capture: elements["provider-detailed-capture"].checked});
    elements["provider-key"].value = "";
    await loadProviderSettings(true);
    notice(local ? "Local Ollama settings saved. Future calls use the displayed installed model." :
      "OpenAI settings saved. Future calls use the displayed model and remaining project ceiling.");
    refresh();
  } catch (error) {
    elements["provider-key"].value = "";
    notice(error.message === "provider_model_incompatible" ?
      "That model is not in OpenRTL's compatible catalog." :
      error.message === "pinned_optional_ollama_sdk_required" ?
      "Ollama support needs OpenRTL's pinned optional Ollama SDK." :
      "Provider settings were not saved. Check the model, spend ceiling and operation state.");
  }
});

elements["provider-reconcile-button"].addEventListener("click", async () => {
  if (providerRevision === null) return;
  elements["provider-reconcile-button"].disabled = true;
  try {
    await post("/api/provider/reconcile", {expected_revision: providerRevision,
      decision: "retain_full_reservation"});
    await loadProviderSettings(true);
    notice("Full reservation retained. Review the provider bill and remaining ceiling " +
      "before a new request. No call was retried.");
    refresh();
  } catch (error) {
    await loadProviderSettings();
    notice("Reconciliation was not saved. Refresh the project and review the current operation state.");
  }
});

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
    if (row.phase === "failed") item.append(node("small", operationFailure(row.error_code)));
    if (["queued", "active"].includes(row.phase)) {
      const cancel = node("button", "Request cancellation");
      cancel.type = "button";
      cancel.addEventListener("click", async () => {
        try {
          const result = await post("/api/operations/" + id + "/cancel", {});
          reconcileOperation(result);
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
    button.type = "button";
    button.disabled = row.revision === 0;
    button.addEventListener("click", () => {
      if (row.revision > 0) loadHistory(row.revision);
    });
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
  const request = ++workbenchRequest;
  try {
    const result = await api("/api/workbench?revision=" + revision);
    if (request === workbenchRequest) renderWorkbench(result);
  } catch (error) { if (request === workbenchRequest) notice("That engineering revision is unavailable."); }
}

const svgNamespace = "http://www.w3.org/2000/svg";
function svgNode(tag, attributes, label) {
  const item = document.createElementNS(svgNamespace, tag);
  for (const [key, value] of Object.entries(attributes)) item.setAttribute(key, String(value));
  if (label !== undefined) item.textContent = label;
  return item;
}
function waveControls(enabled) {
  for (const id of ["waveform-search", "waveform-start", "waveform-end", "waveform-radix",
                    "waveform-load", "waveform-zoom-in", "waveform-zoom-out", "waveform-pan-left",
                    "waveform-pan-right", "waveform-cursor-a", "waveform-cursor-b", "waveform-save",
                    "waveform-attach"]) elements[id].disabled = !enabled;
}
function waveSelectionKey() { return "openrtl.waveform-selection.v1." + waveProjectId; }
function selectedWaveRun() { return selectedRun?.run_id || null; }
function waveStatus(value) { elements["waveform-status"].textContent = value; }
function waveError(error) {
  const messages = {
    waveform_trace_missing: "This run has no retained VCD trace.",
    waveform_trace_changed: "The retained trace changed after the run; it cannot be displayed.",
    waveform_trace_unsupported: "This retained VCD uses a form the viewer cannot inspect.",
    waveform_browser_limit: "This trace exceeds the bounded viewer limit.",
    waveform_trace_metadata_invalid: "This run has invalid trace metadata.",
    waveform_run_unknown: "This recorded run is no longer available.",
    waveform_trace_identity_stale: "The selected trace identity changed. Reopen the run.",
    waveform_response_limit: "This window exceeds the response limit. Narrow the interval."
  };
  return messages[error.message] || "The waveform request was rejected. Refresh the run and try again.";
}

async function loadRuns(revision) {
  try {
    const response = await api("/api/runs");
    if (state?.revision !== revision) return;
    waveRunsRevision = revision;
    if (selectedRun) selectedRun = response.runs.find(run => run.run_id === selectedRun.run_id) || null;
    empty(elements["waveform-run-list"]);
    if (!response.runs.length) {
      elements["waveform-run-list"].append(node("div", "No recorded runs yet.", "muted"));
      return;
    }
    for (const run of response.runs) {
      const button = node("button", "r" + run.revision + " · " + run.status +
        " · " + (run.current_input ? "current input" : "historical input") +
        " · trace " + run.trace_status);
      button.type = "button";
      button.disabled = run.trace_status !== "available";
      button.setAttribute("aria-pressed", run.run_id === selectedWaveRun() ? "true" : "false");
      button.addEventListener("click", () => openWaveRun(run));
      elements["waveform-run-list"].append(button);
    }
    if (!selectedRun) {
      let saved = null;
      try { saved = JSON.parse(localStorage.getItem(waveSelectionKey()) || "null"); } catch (error) { /* browser storage unavailable */ }
      const match = response.runs.find(run => run.run_id === saved?.run_id && run.trace_status === "available");
      if (match) await openWaveRun(match, saved);
    }
  } catch (error) { waveStatus("Run history is unavailable. The saved project was not changed."); }
}

async function loadWaveCatalog(search = "") {
  if (!selectedRun) return;
  try {
    const catalog = await api("/api/waveform/catalog?" + new URLSearchParams({
      run_id: selectedRun.run_id, search}));
    if (catalog.run_id !== selectedWaveRun()) return;
    waveCatalog = catalog;
    empty(elements["waveform-signal-list"]);
    for (const name of catalog.signal_names) {
      const button = node("button", name);
      button.type = "button";
      button.setAttribute("aria-pressed", waveSignals.includes(name) ? "true" : "false");
      button.addEventListener("click", () => {
        if (waveSignals.includes(name)) waveSignals = waveSignals.filter(value => value !== name);
        else if (waveSignals.length < 8) waveSignals.push(name);
        else { waveStatus("Select at most eight signals."); return; }
        button.setAttribute("aria-pressed", waveSignals.includes(name) ? "true" : "false");
        waveStatus(waveSignals.length + " signals selected. Load a bounded time window to inspect them.");
      });
      elements["waveform-signal-list"].append(button);
    }
    if (!catalog.signal_names.length) elements["waveform-signal-list"].append(node("div", "No matching signals.", "muted"));
    if (catalog.truncated) elements["waveform-signal-list"].append(node("div", "Search narrowed to the first 128 matches.", "muted"));
  } catch (error) { waveStatus(waveError(error)); }
}

async function openWaveRun(run, saved = null) {
  selectedRun = run;
  waveCatalog = null;
  waveSignals = [];
  waveWindow = null;
  empty(elements["waveform-chart"]);
  empty(elements["waveform-values"]);
  elements["waveform-search"].value = "";
  await loadWaveCatalog();
  if (!waveCatalog || waveCatalog.run_id !== run.run_id) return;
  elements["waveform-start"].value = "0";
  elements["waveform-end"].value = String(waveCatalog.end_fs);
  elements["waveform-cursor-a"].value = "0";
  elements["waveform-cursor-b"].value = String(waveCatalog.end_fs);
  waveControls(true);
  waveStatus("Run " + run.run_id + " · r" + run.revision + " · " + run.status +
    (run.current_input ? " · current input" : " · historical input") +
    (run.status === "failed" ? ". No exact failure time anchor was recorded; choose an interval." : "."));
  if (saved?.trace_digest === waveCatalog.trace_digest && Array.isArray(saved.signals) &&
      saved.signals.length <= 8 && saved.signals.every(name => typeof name === "string")) {
    waveSignals = saved.signals;
    elements["waveform-start"].value = String(saved.start_fs);
    elements["waveform-end"].value = String(saved.end_fs);
    await loadWaveCatalog();
    await loadWaveWindow();
  }
  for (const button of elements["waveform-run-list"].querySelectorAll("button")) {
    button.setAttribute("aria-pressed", button.textContent.includes("r" + run.revision + " ·") ? "true" : "false");
  }
}

function safeWaveTime(value) {
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 0) throw new Error("Invalid waveform time");
  return number;
}
async function loadWaveWindow() {
  if (!selectedRun || !waveCatalog || !waveSignals.length) {
    waveStatus("Select one to eight signals first."); return;
  }
  try {
    const start = safeWaveTime(elements["waveform-start"].value);
    const end = safeWaveTime(elements["waveform-end"].value);
    const result = await post("/api/waveform/query", {run_id: selectedRun.run_id,
      trace_digest: waveCatalog.trace_digest, signals: waveSignals,
      start_fs: start, end_fs: end, limit: Math.floor(128 / waveSignals.length)});
    if (result.run_id !== selectedWaveRun()) return;
    waveWindow = result;
    elements["waveform-cursor-a"].value = String(start);
    elements["waveform-cursor-b"].value = String(end);
    drawWaveWindow();
    waveStatus("Showing " + result.selected_signals.length + " signals from " + start + " to " + end +
      " fs." + (result.selected_signals.some(row => row.truncated) ?
      " At least one signal is truncated; zoom in for its later values." : ""));
  } catch (error) { waveStatus(waveError(error)); }
}

function waveValueAt(row, timestamp) {
  let value = row.value_at_start;
  for (const transition of row.transitions) {
    if (transition.timestamp_fs > timestamp) break;
    value = transition.value;
  }
  if (row.truncated && timestamp > row.transitions.at(-1)?.timestamp_fs) return "window truncated";
  return value === null ? "not sampled" : value;
}
function waveRadix(value) {
  if (!/^[01]+$/.test(value)) return value;
  if (elements["waveform-radix"].value === "bin") return "0b" + value;
  const number = BigInt("0b" + value);
  return elements["waveform-radix"].value === "hex" ? "0x" + number.toString(16) : number.toString(10);
}
function renderWaveValues() {
  if (!waveWindow) return;
  let a, b;
  try {
    a = safeWaveTime(elements["waveform-cursor-a"].value);
    b = safeWaveTime(elements["waveform-cursor-b"].value);
    if (a < waveWindow.start_fs || b > waveWindow.end_fs || b < a) throw new Error("cursor outside window");
  } catch (error) { elements["waveform-values"].textContent = "Cursors must lie in the displayed window, with B at or after A."; return; }
  empty(elements["waveform-values"]);
  elements["waveform-values"].append(node("div", "A " + a + " fs · B " + b + " fs · Δ " + (b - a) + " fs"));
  for (const row of waveWindow.selected_signals) {
    elements["waveform-values"].append(node("div", row.name + ": A " + waveRadix(waveValueAt(row, a)) +
      " · B " + waveRadix(waveValueAt(row, b)), "nav-item"));
  }
}
function drawWaveWindow() {
  if (!waveWindow) return;
  empty(elements["waveform-chart"]);
  const width = 760, left = 150, right = 745, lane = 58;
  const height = 28 + waveWindow.selected_signals.length * lane;
  const svg = svgNode("svg", {viewBox: `0 0 ${width} ${height}`, role: "presentation"});
  const span = Math.max(1, waveWindow.end_fs - waveWindow.start_fs);
  const x = time => left + (time - waveWindow.start_fs) / span * (right - left);
  for (const [index, row] of waveWindow.selected_signals.entries()) {
    const y = 26 + index * lane;
    svg.append(svgNode("text", {x: 9, y: y + 15}, row.name.length > 21 ? row.name.slice(0, 19) + "…" : row.name));
    svg.append(svgNode("line", {x1: left, y1: y + 16, x2: right, y2: y + 16, class: "grid"}));
    let previousTime = waveWindow.start_fs;
    let previousValue = row.value_at_start;
    for (const transition of row.transitions) {
      const yy = previousValue === "1" ? y + 4 : previousValue === "0" ? y + 26 : y + 16;
      const kind = previousValue === "1" ? "wave-high" : previousValue === "0" ? "wave-low" : "wave-unknown";
      svg.append(svgNode("line", {x1: x(previousTime), y1: yy, x2: x(transition.timestamp_fs), y2: yy, class: kind}));
      svg.append(svgNode("line", {x1: x(transition.timestamp_fs), y1: y + 4,
        x2: x(transition.timestamp_fs), y2: y + 27, class: "grid"}));
      previousTime = transition.timestamp_fs;
      previousValue = transition.value;
    }
    if (!row.truncated) {
      const yy = previousValue === "1" ? y + 4 : previousValue === "0" ? y + 26 : y + 16;
      svg.append(svgNode("line", {x1: x(previousTime), y1: yy, x2: right, y2: yy,
        class: previousValue === "1" ? "wave-high" : previousValue === "0" ? "wave-low" : "wave-unknown"}));
    }
  }
  for (const [id, name] of [["waveform-cursor-a", "cursor-a"], ["waveform-cursor-b", "cursor-b"]]) {
    const time = Number(elements[id].value);
    if (Number.isSafeInteger(time) && time >= waveWindow.start_fs && time <= waveWindow.end_fs)
      svg.append(svgNode("line", {x1: x(time), y1: 0, x2: x(time), y2: height, class: name}));
  }
  svg.addEventListener("click", event => {
    const bounds = svg.getBoundingClientRect();
    const position = (event.clientX - bounds.left) / bounds.width * width;
    const time = Math.round(waveWindow.start_fs + Math.max(0, Math.min(1,
      (position - left) / (right - left))) * (waveWindow.end_fs - waveWindow.start_fs));
    elements[event.shiftKey ? "waveform-cursor-b" : "waveform-cursor-a"].value = String(time);
    drawWaveWindow();
  });
  elements["waveform-chart"].append(svg);
  renderWaveValues();
}

function moveWaveWindow(action) {
  if (!waveCatalog || !waveWindow) return;
  const start = waveWindow.start_fs, end = waveWindow.end_fs;
  const span = Math.max(1, end - start), half = Math.max(1, Math.round(span / 2));
  let nextStart = start, nextEnd = end;
  if (action === "in") { nextStart = start + Math.floor(span / 4); nextEnd = end - Math.floor(span / 4); }
  if (action === "out") { nextStart = Math.max(0, start - half); nextEnd = Math.min(waveCatalog.end_fs, end + half); }
  if (action === "left") { nextStart = Math.max(0, start - half); nextEnd = Math.min(waveCatalog.end_fs, nextStart + span); }
  if (action === "right") { nextEnd = Math.min(waveCatalog.end_fs, end + half); nextStart = Math.max(0, nextEnd - span); }
  elements["waveform-start"].value = String(nextStart);
  elements["waveform-end"].value = String(nextEnd);
  loadWaveWindow();
}

function renderSnapshot(snapshot) {
  state = snapshot.state;
  waveProjectId = snapshot.project_id;
  cursor = snapshot.next_cursor;
  elements["revision-label"].textContent = "Revision " + state.revision;
  elements["status-label"].textContent = state.active ? "Operation active" : state.status.replaceAll("_", " ");
  elements["digest-label"].textContent = state.approved_spec || "No approved design";
  elements["composer-status"].textContent = state.active ? "An operation is active. Refresh keeps its identity." :
    snapshot.capabilities.provider ? "Messages are shown only in this browser session." :
    "Provider unavailable. Configure and enable a compatible model above to chat.";
  elements["chat-kind"].disabled = state.status === "discovery";
  elements["chat-kind"].options[1].disabled = state.stage !== 6 || !state.manifest;
  if (elements["chat-kind"].options[1].disabled) elements["chat-kind"].value = "question";
  renderMemory(state.engineering_memory || []);
  renderOperations(state.workspace_operations || {});
  for (const [id, operation] of Object.entries(state.workspace_operations || {}).slice(-20)) {
    const active = ["active", "cancellation_requested"].includes(operation.phase);
    const progress = active && snapshot.progress?.operation_id === state.active?.id ? snapshot.progress : null;
    reconcileOperation({id, ...operation}, progress);
  }
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
  if (snapshot.progress?.stage === "simulation" && state.active && snapshot.progress.operation_id === state.active.id) {
    elements["simulation-result"].append(node("div", "Simulation " + snapshot.progress.phase +
      " · " + snapshot.progress.elapsed_ms + " ms", "muted"));
  }
  if (shownRevision === null || (shownRevision === latestRevision && latestRevision !== state.revision))
    loadWorkbench(state.revision);
  latestRevision = state.revision;
  if (waveRunsRevision !== state.revision) loadRuns(state.revision);
  empty(elements["evidence-list"]);
  elements["evidence-list"].append(node("div", state.simulation ?
    state.simulation.status + " · " + state.simulation.evidence_kind : "No simulation yet.", "muted"));
  for (const event of snapshot.events) {
    if (observedEvents.has(event.sequence)) continue;
    observedEvents.add(event.sequence);
    const button = node("button", "r" + event.sequence + " · " + event.event, "event-item");
    button.type = "button";
    button.addEventListener("click", () => loadHistory(event.sequence));
    elements["activity-list"].prepend(button);
  }
  while (elements["activity-list"].children.length > 64) elements["activity-list"].lastChild.remove();
  if (snapshot.more_events) setTimeout(refresh, 0);
}

async function refresh() {
  if (!projectReady) return;
  if (refreshPromise) {
    refreshRequested = true;
    return refreshPromise;
  }
  refreshPromise = refreshOnce();
  try { await refreshPromise; }
  finally {
    refreshPromise = null;
    if (refreshRequested) { refreshRequested = false; setTimeout(refresh, 0); }
  }
}

async function refreshOnce() {
  try {
    renderSnapshot(await api("/api/snapshot?cursor=" + cursor));
    await loadProviderSettings();
    for (const id of new Set([...pending, ...replyQueries])) {
      const job = await api("/api/operations/" + id);
      if (terminalPhases.has(job.phase)) {
        inspectedReplies.add(id);
        replyQueries.delete(id);
      }
      reconcileOperation(job);
    }
    if (systemMessages.has("notice:connection")) notice("Connected. Saved project state is current; no operation was retried.", "connection");
  } catch (error) {
    notice("Connection unavailable. Reconnect will read the saved project state; no operation is retried.", "connection");
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
    displayedUserOperations.add(job.id);
    elements["chat-input"].value = "";
    selectedAttachment = null;
    reconcileOperation(job);
    refresh();
  } catch (error) {
    notice("The request could not be confirmed. Reconnect and inspect the operation state before submitting again; no request was retried.");
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
    reconcileOperation(job);
    simulationPlan = null;
    elements["simulation-run-button"].hidden = true;
    refresh();
  } catch (error) { notice("The simulation submission could not be confirmed. Inspect the saved operation before submitting another run."); }
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

let waveSearchTimer = null;
elements["waveform-search"].addEventListener("input", () => {
  clearTimeout(waveSearchTimer);
  waveSearchTimer = setTimeout(() => loadWaveCatalog(elements["waveform-search"].value), 250);
});
elements["waveform-load"].addEventListener("click", loadWaveWindow);
for (const [id, action] of [["waveform-zoom-in", "in"], ["waveform-zoom-out", "out"],
                           ["waveform-pan-left", "left"], ["waveform-pan-right", "right"]]) {
  elements[id].addEventListener("click", () => moveWaveWindow(action));
}
for (const id of ["waveform-cursor-a", "waveform-cursor-b"]) {
  elements[id].addEventListener("change", () => { drawWaveWindow(); });
}
elements["waveform-radix"].addEventListener("change", renderWaveValues);
elements["waveform-save"].addEventListener("click", () => {
  if (!waveWindow || !waveProjectId) { waveStatus("Load a waveform window before saving it."); return; }
  const selection = {run_id: waveWindow.run_id, trace_digest: waveWindow.trace_digest,
    signals: waveWindow.selected_signals.map(row => row.name),
    start_fs: waveWindow.start_fs, end_fs: waveWindow.end_fs};
  try {
    localStorage.setItem(waveSelectionKey(), JSON.stringify(selection));
    waveStatus("This exact run and signal selection was saved in this browser.");
  } catch (error) { waveStatus("Browser storage is unavailable; this selection was not saved."); }
});
elements["waveform-attach"].addEventListener("click", () => {
  if (!waveWindow) { waveStatus("Load a waveform window before attaching it."); return; }
  if (state?.status === "discovery") { waveStatus("Finish the discovery review before attaching run evidence to a question."); return; }
  selectedAttachment = {kind: "waveform", run_id: waveWindow.run_id,
    trace_digest: waveWindow.trace_digest,
    signals: waveWindow.selected_signals.map(row => row.name),
    start_fs: waveWindow.start_fs, end_fs: waveWindow.end_fs};
  notice("Attached run " + waveWindow.run_id + " and its exact recorded interval to the next question.");
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
    loadProviderSettings(true);
    refresh();
  } catch (error) { notice("Project creation failed. Check the selected path and server state."); }
});

elements["upgrade-project-button"].addEventListener("click", async () => {
  try {
    await post("/api/project/upgrade", {});
    elements["upgrade-project-button"].hidden = true;
    projectReady = true;
    notice("Project session upgraded. Review its current engineering state before continuing.");
    loadProviderSettings(true);
    refresh();
  } catch (error) { notice("Project upgrade failed. The saved project remains available for inspection."); }
});

async function bootstrap() {
  try {
    const project = await api("/api/project");
    elements["project-label"].textContent = project.name;
    projectReady = project.ready;
    if (!project.created) {
      loadProviderSettings(true);
      elements["create-project-button"].hidden = false;
      elements["status-label"].textContent = "Project not created";
      notice("Create the selected local project to begin. The server cannot select other filesystem paths.");
      return;
    }
    if (!projectReady) {
      loadProviderSettings(true);
      elements["upgrade-project-button"].hidden = false;
      elements["status-label"].textContent = "Session upgrade required";
      notice("This project uses an older session schema. Review and explicitly upgrade it to continue.");
      return;
    }
    loadProviderSettings(true);
    await loadConversation();
    refresh();
  } catch (error) { notice("Project service unavailable. Reconnect will inspect saved state."); }
}

bootstrap();
setInterval(refresh, 2000);
