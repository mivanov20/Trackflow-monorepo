"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { createLoader } = require("./load-ts.cjs");

function loadKnowledge(apiRequest = async () => ({ answer: "ok" })) {
  return createLoader({ "api-client": { apiRequest } })("lib/knowledge.ts");
}

test("an empty question is rejected before any request, and surrounding spaces are trimmed", () => {
  const { cleanQuestion } = loadKnowledge();
  assert.notEqual(cleanQuestion("").problem, "");
  assert.notEqual(cleanQuestion("   \n ").problem, "");
  assert.deepEqual(cleanQuestion("  What is the SLA?  "), { question: "What is the SLA?", problem: "" });
});

test("a question over the limit of the endpoint is rejected in the browser", () => {
  const { cleanQuestion, MAX_QUESTION_LENGTH } = loadKnowledge();
  assert.equal(MAX_QUESTION_LENGTH, 1000);
  assert.equal(cleanQuestion("x".repeat(1000)).problem, "");
  assert.match(cleanQuestion("x".repeat(1001)).problem, /too long/);
});

test("asking posts the question to /knowledge/query with the session token and returns the answer text", async () => {
  const calls = [];
  const { knowledgeApi } = loadKnowledge(async (path, options) => {
    calls.push([path, options]);
    return { answer: "30 days from delivery.\nSource: returns-policy" };
  });
  const result = await knowledgeApi.ask("token-1", "What's the standard return window?");
  assert.equal(result.answer, "30 days from delivery.\nSource: returns-policy");
  const [path, options] = calls[0];
  assert.equal(path, "/knowledge/query");
  assert.equal(options.method, "POST");
  assert.equal(options.token, "token-1");
  assert.deepEqual(options.body, { question: "What's the standard return window?" });
  assert.ok(options.timeoutMs >= 30_000, "generating an answer can take several seconds");
});

test("a failed call rejects instead of resolving to an empty answer", async () => {
  const { knowledgeApi } = loadKnowledge(async () => {
    throw new Error("503");
  });
  await assert.rejects(() => knowledgeApi.ask("token", "anything"), /503/);
});

test("the example questions cover the four documents of the knowledge base", () => {
  const { EXAMPLE_QUESTIONS } = loadKnowledge();
  const text = EXAMPLE_QUESTIONS.join(" ").toLowerCase();
  for (const topic of ["return", "carrier", "black friday", "storage"]) assert.ok(text.includes(topic), topic);
});
