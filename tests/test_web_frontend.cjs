"use strict";

// Provider-free browser-contract checks, using only Node's standard library.
// Run with: node --test tests/test_web_frontend.cjs
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../src/openrtl/web/app.js"), "utf8");

class Element {
  constructor(tag = "div") {
    this.tagName = tag;
    this.children = [];
    this.listeners = {};
    this.className = "";
    this.value = "";
    this.checked = false;
    this.options = [{}, {}];
    this.classList = {remove() {}};
    this._text = "";
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(item => item.textContent).join(""); }
  set innerHTML(value) { throw new Error("Untrusted content must never be rendered as HTML"); }
  append(...items) { this.children.push(...items); }
  prepend(...items) { this.children.unshift(...items); }
  replaceChildren(...items) { this._text = ""; this.children = items; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  setAttribute(name, value) { this[name] = value; }
  querySelector(selector) {
    for (const child of this.children) {
      if (child.className.split(" ").includes(selector.slice(1))) return child;
      const descendant = child.querySelector(selector);
      if (descendant) return descendant;
    }
    return null;
  }
  querySelectorAll() { return []; }
  scrollIntoView() {}
}

function browser() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const context = vm.createContext({
    document: {getElementById: element, createElement: tag => new Element(tag),
      createElementNS: (_, tag) => new Element(tag)},
    fetch: () => new Promise(() => {}), // Bootstrap never opens a real connection.
    setInterval() {}, setTimeout() {}, clearTimeout() {},
    URLSearchParams, crypto: {randomUUID: () => "a".repeat(32)},
  });
  vm.runInContext(source, context);
  return {context, element, run: code => vm.runInContext(code, context)};
}

function history(sequence, overrides = {}) {
  return {event: {sequence, event: "operation.received", fields: {}}, trace: [],
    state: {}, evidence: null, visibility: {}, details: [], files: [], ...overrides};
}

test("notices and operation progress reconcile as identified System messages", () => {
  const app = browser();
  app.context.notice("Connection unavailable", "connection");
  app.context.notice("Connection unavailable", "connection");
  app.context.notice("Connected", "connection");
  assert.equal(app.element("conversation-list").children.length, 1);
  assert.equal(app.element("conversation-list").textContent, "SystemConnected");
  assert.equal(app.element("notice").textContent, "");
  const job = {id: "a".repeat(32), phase: "active"};
  app.context.reconcileOperation(job, {stage: "discovery", phase: "waiting", elapsed_ms: 6000});
  app.context.reconcileOperation(job);
  assert.match(app.element("conversation-list").textContent, /6000 ms elapsed/);
  const complete = {...job, phase: "completed", result_revision: 5, reply: "Reviewed proposal"};
  app.context.reconcileOperation(complete);
  app.context.reconcileOperation(complete);
  app.context.reconcileOperation(job); // A late active response cannot reopen completion.
  assert.equal(app.element("conversation-list").children.length, 3);
  assert.equal(app.element("conversation-list").textContent.match(/Reviewed proposal/g).length, 1);
  assert.match(app.element("conversation-list").textContent, /completed/);
  assert.doesNotMatch(app.element("conversation-list").textContent, /6000 ms elapsed/);
});

test("discovery validation category appears in Conversation without raw provider text", () => {
  const app = browser();
  app.context.reconcileOperation({id: "b".repeat(32), phase: "failed",
    error_code: "expert_output_readiness_invalid", result_revision: 7});
  assert.match(app.element("conversation-list").textContent, /readiness checklist failed local validation/);
  assert.match(app.element("conversation-list").textContent, /Open History for the exact rule/);
});

test("history navigation inspects details without changing the workbench revision", async () => {
  const app = browser();
  app.run("state = {spec: null}");
  let selected;
  app.context.loadHistory = async revision => { selected = revision; };
  app.context.renderWorkbench({revision: 9, current_revision: 9, files: [],
    planned: {top: null, test_modules: []}, elaborated: {status: "unavailable", reason: "no_index"},
    requirements: [], history: [{revision: 4, event: "operation.received"}], proposal: null});
  app.element("history-list").children[0].listeners.click();
  assert.equal(selected, 4);
  assert.equal(app.run("shownRevision"), 9);
});

test("telemetry content remains text and unknown values remain distinct from zero", async () => {
  const app = browser();
  const malicious = "<script>throw 'must never execute'</script>";
  app.context.api = async () => history(3, {
    metrics: {input_tokens: 0, output_tokens: null, elapsed_ms: 120000, cost: null},
    capture: {enabled: true, available: true},
    details: [{category: "provider_trace", payload: {content: malicious,
      reasoning: "Provider returned this text"}, truncated: false}],
  });
  await app.context.loadHistory(3);
  const target = app.element("history-detail");
  assert.match(target.textContent, /input_tokens0/);
  assert.match(target.textContent, /output_tokensUnavailable/);
  assert.ok(target.textContent.includes(malicious));
  const record = target.children.find(item => item.tagName === "details");
  assert.ok(record);
  assert.match(record.textContent, /provider_trace/);
  assert.match(target.textContent, /Inaccessible model internals are unavailable/);
});

test("latest history selection wins even when earlier responses arrive late", async () => {
  const app = browser();
  const responses = new Map();
  app.context.api = route => new Promise(resolve => responses.set(route, resolve));
  const earlier = app.context.loadHistory(1);
  const later = app.context.loadHistory(2);
  responses.get("/api/history/2")(history(2));
  await later;
  responses.get("/api/history/1")(history(1));
  await earlier;
  assert.match(app.element("history-detail").textContent, /Saved execution trace · r2/);
  assert.doesNotMatch(app.element("history-detail").textContent, /Saved execution trace · r1/);
});

test("provider settings submit explicit timeout and capture choice, rejecting invalid deadlines", async () => {
  const app = browser();
  const submissions = [];
  app.context.post = async (route, body) => { submissions.push({route, body}); return {}; };
  app.context.loadProviderSettings = async () => {};
  app.context.refresh = async () => {};
  app.element("provider-kind").value = "ollama";
  app.element("provider-ollama-model").value = "example:local";
  app.element("provider-enabled").checked = true;
  app.element("provider-timeout").value = "240";
  app.element("provider-detailed-capture").checked = true;
  app.element("provider-key").value = "synthetic-key-not-sent-to-local-provider";
  const submit = app.element("provider-form").listeners.submit;
  await submit({preventDefault() {}});
  assert.equal(submissions.length, 1);
  assert.equal(submissions[0].body.timeout_seconds, 240);
  assert.equal(submissions[0].body.detailed_capture, true);
  assert.equal(submissions[0].body.api_key, null);
  assert.equal(app.element("provider-key").value, "");
  for (const value of ["0", "301", "1.5", "bad"]) {
    app.element("provider-timeout").value = value;
    await submit({preventDefault() {}});
  }
  assert.equal(submissions.length, 1);
  assert.match(app.element("conversation-list").textContent, /1 to 300 seconds/);
});

test("concurrent refresh requests share one snapshot request", async () => {
  const app = browser();
  let calls = 0;
  let resolveSnapshot;
  app.run("projectReady = true");
  app.context.api = () => {
    calls++;
    return new Promise(resolve => { resolveSnapshot = resolve; });
  };
  app.context.renderSnapshot = () => {};
  app.context.loadProviderSettings = async () => {};
  const first = app.context.refresh();
  const second = app.context.refresh();
  assert.equal(calls, 1);
  resolveSnapshot({});
  await Promise.all([first, second]);
  assert.equal(calls, 1);
});

test("repeated snapshot events render only once", () => {
  const app = browser();
  app.context.loadWorkbench = async () => {};
  app.context.loadRuns = async () => {};
  const snapshot = {state: {revision: 3, active: null, status: "discovery",
    approved_spec: null, stage: 0, manifest: null, simulation: null, spec: null,
    engineering_memory: [], workspace_operations: {}}, project_id: "unit-project",
    next_cursor: 3, capabilities: {provider: false, simulation: false},
    events: [{sequence: 3, event: "operation.completed"}]};
  app.context.renderSnapshot(snapshot);
  app.context.renderSnapshot(snapshot);
  assert.equal(app.element("activity-list").children.length, 1);
});

test("captured conversation restores once and does not duplicate terminal replies", async () => {
  const app = browser();
  const operation = "a".repeat(32);
  app.context.api = async () => ({messages: [
    {id: 1, operation_id: operation, kind: "user", text: "Synthetic circuit request", truncated: false},
    {id: 2, operation_id: operation, kind: "agent", text: "Captured reply", truncated: true},
  ], capture: {enabled: false, available: true}});
  await app.context.loadConversation();
  await app.context.loadConversation();
  app.context.reconcileOperation({id: operation, phase: "completed", result_revision: 6, reply: "Captured reply"});
  assert.equal(app.element("conversation-list").children.length, 3);
  assert.equal(app.element("conversation-list").textContent.match(/Captured reply/g).length, 1);
  assert.match(app.element("conversation-list").textContent, /Captured text is truncated/);
  assert.match(app.element("conversation-list").textContent, /Saved local capture/);
});
