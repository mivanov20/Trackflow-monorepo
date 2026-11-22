"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { createLoader } = require("./load-ts.cjs");

function loadRfp(apiRequest = async () => ({})) {
  return createLoader({ "api-client": { apiRequest } })("lib/rfp.ts");
}

test("only a PDF within the size limit is accepted in the browser", () => {
  const { checkPdf, MAX_UPLOAD_MB } = loadRfp();
  assert.equal(checkPdf({ name: "rfp.PDF", size: 1000 }), "");
  assert.match(checkPdf({ name: "notes.txt", size: 1000 }), /PDF/);
  assert.match(checkPdf({ name: "empty.pdf", size: 0 }), /empty/);
  assert.match(checkPdf({ name: "huge.pdf", size: MAX_UPLOAD_MB * 1024 * 1024 + 1 }), /too large/);
});

test("the page keeps polling only while the intake or the drafting is still running", () => {
  const { hasPending, inProgress } = loadRfp();
  const done = [
    { status: "discarded", proposal_state: "not_started" },
    { status: "failed", proposal_state: "not_started" },
    { status: "intake_complete", proposal_state: "not_started" },
    { status: "intake_complete", proposal_state: "failed" },
    { status: "needs_human_review", proposal_state: "ready_for_part_3" },
    { status: "under_evaluation", proposal_state: "ready_for_part_3" }, // every section passed: resting state, no Part 3 status exists yet
  ];
  assert.equal(hasPending(done), false);
  for (const running of [
    { status: "analyzing", proposal_state: "not_started" },
    { status: "intake_complete", proposal_state: "queued" },
    { status: "drafting", proposal_state: "in_progress" },
    { status: "under_evaluation", proposal_state: "in_progress" },
  ]) assert.equal(inProgress(running), true, JSON.stringify(running));
  assert.equal(hasPending([]), false);
});

test("a proposal whose sections all passed reads as ready for approval, and the others keep their status name", () => {
  const { statusLabel } = loadRfp();
  assert.equal(statusLabel({ status: "under_evaluation", proposal_state: "ready_for_part_3" }), "Ready for approval");
  assert.equal(statusLabel({ status: "under_evaluation", proposal_state: "in_progress" }), "Under evaluation");
  assert.equal(statusLabel({ status: "needs_human_review", proposal_state: "ready_for_part_3" }), "Needs human review");
  assert.equal(statusLabel({ status: "drafting", proposal_state: "in_progress" }), "Drafting");
});

test("the proposal can be started only from a finished intake that is not already running", () => {
  const { canStartProposal } = loadRfp();
  assert.equal(canStartProposal({ status: "intake_complete", proposal_state: "not_started" }), true);
  assert.equal(canStartProposal({ status: "intake_complete", proposal_state: "failed" }), true);
  assert.equal(canStartProposal({ status: "intake_complete", proposal_state: "queued" }), false);
  assert.equal(canStartProposal({ status: "discarded", proposal_state: "not_started" }), false);
  assert.equal(canStartProposal({ status: "under_evaluation", proposal_state: "ready_for_part_3" }), false);
});

test("starting the proposal posts to the ticket's proposal endpoint with the session token", async () => {
  const calls = [];
  const { rfpApi } = loadRfp(async (path, options) => {
    calls.push([path, options.method, options.token]);
    return {};
  });
  await rfpApi.startProposal("token-1", "t-1");
  assert.deepEqual(calls[0], ["/rfp/tickets/t-1/proposal", "POST", "token-1"]);
});

test("an upload is a multipart request with the PDF, the session token and a generous timeout", async () => {
  const calls = [];
  const { rfpApi } = loadRfp(async (path, options) => {
    calls.push([path, options]);
    return { ticket_id: "t-1", status: "analyzing" };
  });
  const file = new File([new Uint8Array([37, 80, 68, 70])], "rfp.pdf", { type: "application/pdf" });
  const ticket = await rfpApi.upload("token-1", file);
  assert.equal(ticket.status, "analyzing");
  const [path, options] = calls[0];
  assert.equal(path, "/rfp/tickets");
  assert.equal(options.method, "POST");
  assert.equal(options.token, "token-1");
  assert.ok(options.body instanceof FormData && options.body.get("file").name === "rfp.pdf");
  assert.ok(options.timeoutMs >= 30_000);
});

test("a ticket is read by its id, encoded", async () => {
  const calls = [];
  const { rfpApi } = loadRfp(async (path, options) => {
    calls.push([path, options.token]);
    return {};
  });
  await rfpApi.get("token-1", "a/b c");
  assert.deepEqual(calls[0], ["/rfp/tickets/a%2Fb%20c", "token-1"]);
});

test("the figures a department found are written for people", () => {
  const { figureLines } = loadRfp();
  assert.deepEqual(figureLines({ skus: 15000, preferred_location: "Zaragoza", carrier_selection_requested: true, process_steps_requested: ["inspection", "restock"] }), [
    "SKUs: 15,000",
    "Preferred location: Zaragoza",
    "Carrier selection requested: yes",
    "Steps requested: inspection, restock",
  ]);
});

test("a missing readability score is said, not shown as zero", () => {
  const { scoreText } = loadRfp();
  assert.equal(scoreText(null), "not available");
  assert.equal(scoreText(undefined), "not available");
  assert.equal(scoreText(10.054), "10.1");
});

test("the page keeps asking while a ticket waits for its owners and through the moment the gates open, and stops when the ticket is done or blocked", () => {
  const { hasPending, inProgress } = loadRfp();
  assert.equal(hasPending([{ status: "waiting_for_approval", proposal_state: "ready_for_part_3", approval_state: "waiting" }]), true);
  assert.equal(inProgress({ status: "under_evaluation", proposal_state: "ready_for_part_3", approval_state: "queued" }), true); // Part 2 finished, the gates are about to open
  assert.equal(hasPending([{ status: "done", proposal_state: "ready_for_part_3", approval_state: "complete" }, { status: "needs_human_review", proposal_state: "ready_for_part_3", approval_state: "blocked" }]), false);
});

test("Part 3 can be started by hand only from ready sections whose approvals have not begun", () => {
  const { canStartApproval, statusLabel } = loadRfp();
  assert.equal(canStartApproval({ status: "under_evaluation", proposal_state: "ready_for_part_3", approval_state: "not_started" }), true);
  assert.equal(canStartApproval({ status: "needs_human_review", proposal_state: "ready_for_part_3", approval_state: "not_started" }), true);
  assert.equal(canStartApproval({ status: "waiting_for_approval", proposal_state: "ready_for_part_3", approval_state: "waiting" }), false);
  assert.equal(canStartApproval({ status: "under_evaluation", proposal_state: "in_progress", approval_state: "not_started" }), false);
  assert.equal(statusLabel({ status: "waiting_for_approval", proposal_state: "ready_for_part_3", approval_state: "waiting" }), "Waiting for approval");
  assert.equal(statusLabel({ status: "done", proposal_state: "ready_for_part_3", approval_state: "complete" }), "Done");
});

test("a decision that asks for changes or rejects needs a comment, and a capacity must be a whole number", () => {
  const { checkDecision } = loadRfp();
  assert.equal(checkDecision({ decision: "approve", comment: "" }), "");
  assert.match(checkDecision({ decision: "reject", comment: "no" }), /comment is required/);
  assert.match(checkDecision({ decision: "request_changes", comment: "   " }), /comment is required/);
  assert.equal(checkDecision({ decision: "request_changes", comment: "Add the onboarding assumptions" }), "");
  assert.match(checkDecision({ decision: "approve", comment: "", capacity: "3.5" }), /whole number/);
  assert.match(checkDecision({ decision: "approve", comment: "", capacity: "0" }), /whole number/);
  assert.equal(checkDecision({ decision: "approve", comment: "", capacity: "3000" }), "");
});

test("a decision, the document and the trace are read and written at the endpoints of the department and the ticket", async () => {
  const calls = [];
  const { rfpApi } = loadRfp(async (path, options) => {
    calls.push([path, options.method ?? "GET", options.token, options.body]);
    return {};
  });
  const body = { decision: "approve", approver: "Ana Whitfield", comment: "" };
  await rfpApi.decide("token-1", "t-1", "warehouse", body);
  await rfpApi.document("token-1", "t-1");
  await rfpApi.trace("token-1", "t-1");
  await rfpApi.startApproval("token-1", "t-1");
  assert.deepEqual(calls, [
    ["/rfp/tickets/t-1/sections/warehouse/decision", "POST", "token-1", body],
    ["/rfp/tickets/t-1/document", "GET", "token-1", undefined],
    ["/rfp/tickets/t-1/trace", "GET", "token-1", undefined],
    ["/rfp/tickets/t-1/approval", "POST", "token-1", undefined],
  ]);
});
