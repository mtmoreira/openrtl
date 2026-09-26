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
  scrollIntoView() { this.scrolled = true; }
  focus() { this.focused = true; }
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

function workflowSnapshot(action, {revision = 10, status = "discovery", stage = null, plan = null, ...changes} = {}) {
  return {state: {revision, active: null, status, approved_spec: status === "discovery" ? null : "approved-digest",
    stage: 0, manifest: null, simulation: null, spec: null, engineering_memory: [], workspace_operations: {}},
    project_id: "unit-project", next_cursor: revision, capabilities: {provider: true, simulation: false}, events: [],
    workflow: {schema: "openrtl.web-workflow.v1", action, message: "Saved next-step guidance", stage, plan}, ...changes};
}
function generationSnapshot(stage = "architecture", changes = {}) {
  const revision = changes.revision ?? 10;
  return workflowSnapshot("generate", {status: "building", stage, revision,
    plan: {schema: "openrtl.web-generation-plan.v1", revision, input_digest: "input-digest",
      approved_spec: "approved-digest", status: "building", stage, plan_digest: "plan-" + stage}, ...changes});
}
function prepareWorkflowApp() {
  const app = browser();
  app.context.loadWorkbench = async () => {};
  app.context.loadRuns = async () => {};
  app.context.refresh = async () => {};
  return app;
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

test("full specification review renders authoritative fields and safe structured sections", () => {
  const app = browser();
  const marker = "<img src=x onerror=throw-new-error>";
  const sectionIds = ["purpose_scope", "parameters", "interfaces", "clock_reset_cdc",
    "functional_operation", "timing_performance", "exceptional_behavior", "integration"];
  const ports = [{name: "clk", direction: "input", width: 1},
    {name: "data", direction: "input", width: 32}];
  const requirement = {id: "req.transfer", text: "Transfer accepted data",
    acceptance: "A scoreboard checks every accepted word"};
  const spec = {title: "Review fixture", top: "review_top", behavior: "Move data", clock_reset: "One clock",
    hardware_specification: {schema: "openrtl.hardware-specification.v1",
      parameters: [{name: "WIDTH", type: "integer", default: "32", legal_values: "1 through 64",
        description: "Data width"}],
      sections: sectionIds.map((id, index) => ({id, status: id === "integration" ? "not_applicable" : "specified",
        content: index === 0 ? marker : "Reviewed " + id}))},
    requirements: [requirement], ports, questions: [{id: "q.mode", text: "Select mode"}],
    assumptions: [{id: "a.sync", text: "Single domain", rationale: "No CDC was requested"}],
    readiness: {schema: "openrtl.design-readiness.v1", items: [
      {category: "interfaces", status: "specified", decision: "Ports are explicit",
        requirement_ids: [requirement.id], ports: ports.map(row => row.name)},
      {category: "widths_signedness", status: "specified", decision: "All values are unsigned",
        requirement_ids: [requirement.id], ports: ports.map(row => row.name)},
      ...["clock_reset", "timing_latency", "handshake", "exceptional_behavior", "acceptance"].map(category =>
        ({category, status: "specified", decision: "Reviewed " + category,
          requirement_ids: [requirement.id], ports: []}))]}};
  app.context.renderSpecification(spec);
  const rendered = app.element("specification").textContent;
  for (const expected of ["openrtl.hardware-specification.v1", "completeness: unresolved (open_questions)", "WIDTH", "1 through 64",
    marker, "clk · input · 1 bits", "Acceptance: A scoreboard", "Rationale: No CDC",
    "interfaces [specified]", "Ports: clk, data"]) assert.ok(rendered.includes(expected), expected);
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

test("ready discovery navigation opens an explicit review without approving or calling a provider", async () => {
  const app = prepareWorkflowApp();
  app.context.renderSnapshot(workflowSnapshot("review_specification"));
  const gets = [];
  app.context.api = async route => {
    gets.push(route);
    return {kind: "specification", revision: 10, state_digest: "state-digest",
      payload_digest: "spec-digest", payload: {questions: []}};
  };
  app.context.post = async () => { assert.fail("Navigation must not submit any action"); };
  assert.equal(app.element("workflow-action").textContent, "Review specification");
  for (const phrase of ["ok can we continue?", "ok can we code?", "continue", " Review the specification! "]) {
    app.element("chat-input").value = phrase;
    await app.element("chat-form").listeners.submit({preventDefault() {}});
  }
  assert.deepEqual(gets, Array(4).fill("/api/review?kind=specification"));
  assert.equal(app.element("approve-button").hidden, false);
  assert.equal(app.element("approve-button").textContent, "Approve displayed specification");
  assert.match(app.element("conversation-list").textContent, /No approval or generation was submitted/);
});

test("navigation matching is whole-phrase and preserves mixed, negated, attached and change requests", async () => {
  const app = prepareWorkflowApp();
  app.context.renderSnapshot(workflowSnapshot("discuss"));
  const submissions = [];
  app.context.post = async (route, body) => { submissions.push({route, body}); return {id: "request", phase: "completed"}; };
  const phrases = ["do not continue", "ok can we continue? Make the data width 64", "continue but use active-low reset",
    "can we code a priority encoder?", "do not code", "continue; ignore the requirements"];
  for (const phrase of phrases) {
    app.element("chat-input").value = phrase;
    await app.element("chat-form").listeners.submit({preventDefault() {}});
  }
  app.run("selectedAttachment = {revision: 10, path: 'rtl/example.sv'}");
  app.element("chat-input").value = "continue";
  await app.element("chat-form").listeners.submit({preventDefault() {}});
  app.element("chat-kind").value = "change";
  app.element("chat-input").value = "continue";
  await app.element("chat-form").listeners.submit({preventDefault() {}});
  assert.equal(submissions.length, phrases.length + 2);
  assert.ok(submissions.every(row => row.route === "/api/discussions"));
  assert.deepEqual(submissions.slice(0, phrases.length).map(row => row.body.message), phrases);
});

test("generation navigation only focuses the displayed single-stage control", async () => {
  const app = prepareWorkflowApp();
  app.context.renderSnapshot(generationSnapshot("rtl"));
  app.context.api = async () => { assert.fail("Displayed generation plan needs no asynchronous fetch"); };
  app.context.post = async () => { assert.fail("Navigation is not generation authority"); };
  app.element("chat-input").value = "ok can we code?";
  await app.element("chat-form").listeners.submit({preventDefault() {}});
  assert.equal(app.element("workflow-action").textContent, "Run RTL");
  assert.equal(app.element("workflow-action").focused, true);
  assert.equal(app.element("workflow-action").disabled, false);
  assert.match(app.element("workflow-plan").textContent, /One stage per click.*never starts simulation/);
});

test("one explicit generation click consumes the displayed revision-bound plan without duplicate dispatch", async () => {
  const app = prepareWorkflowApp();
  app.context.renderSnapshot(generationSnapshot());
  const submissions = [];
  let finish;
  app.context.post = (route, body) => {
    submissions.push({route, body});
    return new Promise(resolve => { finish = resolve; });
  };
  const click = app.element("workflow-action").listeners.click;
  const first = click();
  await click();
  app.context.renderSnapshot(generationSnapshot()); // Poll completing during the POST cannot re-enable.
  assert.equal(app.element("workflow-action").disabled, true);
  assert.equal(submissions.length, 1);
  assert.equal(submissions[0].route, "/api/generations");
  assert.equal(submissions[0].body.expected_revision, 10);
  assert.equal(submissions[0].body.plan_digest, "plan-architecture");
  assert.match(submissions[0].body.client_operation_id, /^[a-f0-9]{32}$/);
  finish({id: "generation-operation", phase: "queued"});
  await first;
  await click();
  assert.equal(submissions.length, 1);
  assert.equal(app.run("generationPlan"), null);
});

test("generation plans fail closed for stale revision, approval, stage or status", () => {
  for (const change of [{revision: 9}, {approved_spec: "changed"}, {stage: "simulation"}, {status: "discovery"}]) {
    const app = prepareWorkflowApp();
    const snapshot = generationSnapshot();
    Object.assign(snapshot.workflow.plan, change);
    app.context.renderSnapshot(snapshot);
    assert.equal(app.element("workflow-action").disabled, true);
    assert.equal(app.run("generationPlan"), null);
  }
});

test("missing or unknown workflow schemas never retain a previous generation plan", () => {
  const app = prepareWorkflowApp();
  app.context.renderSnapshot(generationSnapshot());
  for (const value of [null, {schema: "unknown", action: "generate", plan: generationSnapshot().workflow.plan}]) {
    const snapshot = generationSnapshot();
    snapshot.workflow = value;
    app.context.renderSnapshot(snapshot);
    assert.equal(app.run("generationPlan"), null);
    assert.equal(app.element("workflow-action").hidden, true);
  }
});

test("failed generation submission remains consumed until a fresh snapshot and never retries itself", async () => {
  const app = prepareWorkflowApp();
  app.context.renderSnapshot(generationSnapshot());
  let submissions = 0, refreshes = 0;
  app.context.post = async () => { submissions++; throw new Error("connection lost"); };
  app.context.refresh = async () => { refreshes++; };
  await app.element("workflow-action").listeners.click();
  await app.element("workflow-action").listeners.click();
  assert.equal(submissions, 1);
  assert.equal(refreshes, 1);
  assert.equal(app.element("workflow-action").disabled, true);
  assert.match(app.element("conversation-list").textContent, /no stage was retried/);
});

test("specification approval posts only displayed authority and refreshes the next stage", async () => {
  const app = prepareWorkflowApp();
  app.context.renderSnapshot(workflowSnapshot("review_specification"));
  app.context.api = async () => ({kind: "specification", revision: 10,
    state_digest: "reviewed-state", payload_digest: "reviewed-spec", payload: {ports: []}});
  await app.context.showReview("specification");
  const submissions = [];
  app.context.post = async (route, body) => { submissions.push({route, body}); };
  app.context.refresh = async () => app.context.renderSnapshot(generationSnapshot("architecture", {revision: 11}));
  await app.element("approve-button").listeners.click();
  assert.equal(submissions.length, 1);
  assert.equal(submissions[0].route, "/api/approve");
  assert.deepEqual(JSON.parse(JSON.stringify(submissions[0].body)), {kind: "specification", expected_revision: 10,
    state_digest: "reviewed-state", payload_digest: "reviewed-spec"});
  assert.equal(app.element("approve-button").hidden, true);
  assert.equal(app.element("workflow-action").textContent, "Run architecture");
  assert.equal(app.element("workflow-action").disabled, false);
});

test("late review responses cannot restore approval at a newer revision", async () => {
  const app = prepareWorkflowApp();
  app.context.renderSnapshot(workflowSnapshot("review_specification"));
  let finish;
  app.context.api = () => new Promise(resolve => { finish = resolve; });
  const reviewing = app.context.showReview("specification");
  app.context.renderSnapshot(workflowSnapshot("discuss", {revision: 11}));
  finish({kind: "specification", revision: 10, payload: {}, state_digest: "old", payload_digest: "old"});
  await reviewing;
  assert.equal(app.element("approve-button").hidden, true);
  assert.equal(app.run("card"), null);
});

test("a newer snapshot invalidates displayed approval and ignores older snapshots", async () => {
  const app = prepareWorkflowApp();
  app.context.renderSnapshot(workflowSnapshot("review_specification"));
  app.context.api = async () => ({kind: "specification", revision: 10, payload: {}, state_digest: "old", payload_digest: "old"});
  await app.context.showReview("specification");
  app.context.renderSnapshot(workflowSnapshot("discuss", {revision: 11}));
  app.context.renderSnapshot(workflowSnapshot("review_specification", {revision: 10}));
  assert.equal(app.element("approve-button").hidden, true);
  assert.equal(app.run("state.revision"), 11);
  assert.equal(app.element("workflow-action").textContent, "Describe or clarify the design");
});

test("completed generation exposes the next stage without automatically dispatching it", () => {
  const app = prepareWorkflowApp();
  app.context.post = async () => { assert.fail("Rendering a completed operation cannot dispatch work"); };
  const active = generationSnapshot();
  active.state.workspace_operations = {work: {phase: "active"}};
  app.context.renderSnapshot(active);
  assert.equal(app.element("workflow-action").disabled, true);
  const completed = generationSnapshot("verification_plan", {revision: 15});
  completed.state.workspace_operations = {work: {phase: "completed", result_revision: 14}};
  app.context.renderSnapshot(completed);
  assert.equal(app.element("workflow-action").disabled, false);
  assert.equal(app.element("workflow-action").textContent, "Run verification plan");
});

test("simulation navigation reviews but never executes and stale plans cannot reappear", async () => {
  const app = prepareWorkflowApp();
  app.context.renderSnapshot(workflowSnapshot("simulate", {status: "building"}));
  const gets = [];
  app.context.api = async route => { gets.push(route); return {revision: 10, plan_digest: "run-plan"}; };
  app.context.post = async () => { assert.fail("Reviewing is not simulation authority"); };
  await app.element("workflow-action").listeners.click();
  assert.deepEqual(gets, ["/api/simulation/plan"]);
  assert.equal(app.element("simulation-run-button").hidden, false);
  let finish;
  app.context.api = () => new Promise(resolve => { finish = resolve; });
  const reviewing = app.context.showSimulationPlan();
  app.context.renderSnapshot(workflowSnapshot("blocked", {status: "building", revision: 11}));
  finish({revision: 10, plan_digest: "old-run-plan"});
  await reviewing;
  assert.equal(app.element("simulation-run-button").hidden, true);
  assert.equal(app.run("simulationPlan"), null);
});

test("simulation needs its own explicit displayed-plan click and cannot double-dispatch", async () => {
  const app = prepareWorkflowApp();
  app.context.renderSnapshot(workflowSnapshot("simulate", {status: "building"}));
  app.context.api = async () => ({revision: 10, plan_digest: "run-plan"});
  await app.context.showSimulationPlan();
  let finish;
  const posts = [];
  app.context.post = (route, body) => {
    posts.push({route, body});
    return new Promise(resolve => { finish = resolve; });
  };
  const first = app.element("simulation-run-button").listeners.click();
  await app.element("simulation-run-button").listeners.click();
  assert.equal(posts.length, 1);
  assert.equal(posts[0].route, "/api/simulations");
  assert.equal(posts[0].body.expected_revision, 10);
  assert.equal(posts[0].body.plan_digest, "run-plan");
  finish({id: "simulation-operation", phase: "queued"});
  await first;
  assert.equal(app.element("simulation-run-button").hidden, true);
});

test("repair and signoff use individual generation stages, never implicit simulation or acceptance", async () => {
  for (const [stage, status, label] of [["diagnosis", "needs_repair", "Run diagnosis and repair"],
    ["signoff", "needs_signoff", "Run signoff review"]]) {
    const app = prepareWorkflowApp();
    const snapshot = generationSnapshot(stage, {status});
    snapshot.workflow.plan.status = status;
    app.context.renderSnapshot(snapshot);
    assert.equal(app.element("workflow-action").textContent, label);
    assert.equal(app.element("workflow-action").disabled, false);
    const posts = [];
    app.context.post = async (route, body) => { posts.push({route, body}); return {id: stage, phase: "queued"}; };
    await app.element("workflow-action").listeners.click();
    assert.equal(posts.length, 1);
    assert.equal(posts[0].route, "/api/generations");
    assert.equal(posts[0].body.plan_digest, "plan-" + stage);
  }
});

test("acceptance and change handoffs open exact review kinds with distinct explicit approvals", async () => {
  for (const [action, kind] of [["review_acceptance", "acceptance"], ["review_change", "change"]]) {
    const app = prepareWorkflowApp();
    app.context.renderSnapshot(workflowSnapshot(action, {status: "awaiting_acceptance"}));
    const gets = [], posts = [];
    app.context.api = async route => { gets.push(route); return {kind, revision: 10, payload: {evidence: "saved"},
      state_digest: "state-digest", payload_digest: "payload-digest"}; };
    app.context.post = async (route, body) => { posts.push({route, body}); };
    await app.element("workflow-action").listeners.click();
    assert.deepEqual(gets, ["/api/review?kind=" + kind]);
    assert.equal(posts.length, 0);
    assert.equal(app.element("approve-button").textContent,
      kind === "acceptance" ? "Accept displayed design evidence" : "Approve displayed change");
    await app.element("approve-button").listeners.click();
    assert.equal(posts.length, 1);
    assert.equal(posts[0].route, "/api/approve");
    assert.equal(posts[0].body.kind, kind);
    if (kind === "acceptance") assert.match(app.element("conversation-list").textContent, /not a release or publication/);
  }
});

test("incomplete discovery, blocked capability and completed state provide safe next-step guidance", async () => {
  for (const action of ["discuss", "blocked", "complete"]) {
    const app = prepareWorkflowApp();
    const snapshot = workflowSnapshot(action);
    snapshot.workflow.message = "<img src=x> Safe authoritative guidance for " + action;
    app.context.renderSnapshot(snapshot);
    app.context.api = async () => { assert.fail("Guidance must not fetch a generation or approval action"); };
    app.context.post = async () => { assert.fail("Guidance must not cause effects"); };
    await app.element("workflow-navigation").listeners.click();
    assert.equal(app.element("workflow-message").textContent, snapshot.workflow.message);
    assert.equal(app.element("workflow-action").hidden, action !== "discuss");
    assert.equal(app.run("generationPlan"), null);
  }
});

test("failed review navigation never claims that an approval or simulation plan was displayed", async () => {
  for (const [action, errorText] of [["review_specification", "No specification is available"],
    ["review_change", "No change is available"], ["review_acceptance", "No acceptance is available"],
    ["simulate", "Simulation is unavailable"]]) {
    const app = prepareWorkflowApp();
    app.context.renderSnapshot(workflowSnapshot(action));
    app.context.api = async () => { throw new Error("stale request"); };
    app.context.post = async () => { assert.fail("Failed review navigation cannot submit an action"); };
    await app.element("workflow-navigation").listeners.click();
    assert.ok(app.element("conversation-list").textContent.includes(errorText));
    assert.doesNotMatch(app.element("conversation-list").textContent, /Review the displayed/);
    assert.equal(app.run("card"), null);
    assert.equal(app.run("simulationPlan"), null);
  }
});

test("late navigation reviews do not announce a plan invalidated by a newer snapshot", async () => {
  for (const action of ["review_specification", "simulate"]) {
    const app = prepareWorkflowApp();
    app.context.renderSnapshot(workflowSnapshot(action));
    let finish;
    app.context.api = () => new Promise(resolve => { finish = resolve; });
    app.context.post = async () => { assert.fail("Stale navigation cannot submit an action"); };
    const navigation = app.element("workflow-navigation").listeners.click();
    app.context.renderSnapshot(workflowSnapshot("blocked", {revision: 11}));
    finish({kind: "specification", revision: 10, payload: {}, state_digest: "old", payload_digest: "old", plan_digest: "old"});
    await navigation;
    assert.doesNotMatch(app.element("conversation-list").textContent, /Review the displayed/);
    assert.equal(app.run("card"), null);
    assert.equal(app.run("simulationPlan"), null);
  }
});

test("blocked signoff renders saved findings safely without private capture or provider dispatch", async () => {
  const app = prepareWorkflowApp();
  const snapshot = workflowSnapshot("blocked", {status: "review_blocked"});
  snapshot.state.review = {verdict: "revise", summary: "Reset behavior needs a reviewed correction.",
    findings: ["<img src=x onerror=throw-new-error>", "Clarify the output during reset."]};
  snapshot.workflow.message = "Signoff requested changes. Inspect the saved findings, then use Propose a reviewed change to propose a correction.";
  app.context.api = async () => { assert.fail("Findings must come from accepted state, not private capture"); };
  app.context.post = async () => { assert.fail("Displaying findings cannot propose, approve or generate changes"); };
  app.context.renderSnapshot(snapshot);
  await app.element("workflow-navigation").listeners.click();
  for (const value of ["Saved signoff findings", snapshot.state.review.summary, ...snapshot.state.review.findings]) {
    assert.ok(app.element("workflow-plan").textContent.includes(value), value);
  }
  assert.match(app.element("workflow-message").textContent, /Propose a reviewed change/);
  assert.equal(app.element("workflow-action").hidden, true);
  const newer = workflowSnapshot("generate", {revision: 11, status: "building"});
  newer.state.review = snapshot.state.review; // Historical findings must not masquerade as a current blocker.
  app.context.renderSnapshot(newer);
  assert.doesNotMatch(app.element("workflow-plan").textContent, /Saved signoff findings/);
});
