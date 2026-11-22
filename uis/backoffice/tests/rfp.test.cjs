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

test("the page keeps polling only while a ticket is still being analysed", () => {
  const { hasPending, isFinal } = loadRfp();
  assert.equal(hasPending([{ status: "intake_complete" }, { status: "discarded" }, { status: "failed" }]), false);
  assert.equal(hasPending([{ status: "intake_complete" }, { status: "analyzing" }]), true);
  assert.equal(hasPending([]), false);
  assert.equal(isFinal("analyzing"), false);
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
