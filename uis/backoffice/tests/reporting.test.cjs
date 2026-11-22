"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { createLoader } = require("./load-ts.cjs");

globalThis.window = globalThis.window ?? {};

// The "KPIs to Measure" of the company CONTEXT, and the columns of its destination table that carry them.
const CONTEXT_KPIS = [
  ["Inbound Volume", "inbound_units_count"],
  ["Outbound Throughput", "outbound_orders_count"],
  ["Stockout Frequency", "stockout_events_count"],
  ["Discrepancy Rate", "discrepancy_rate"],
];

function loadReporting(extra = {}) {
  return createLoader({ telemetry: { track() {}, newRequestId: () => "id", withRequestId: (_id, run) => run(), sessionAgeSeconds: () => null }, ...extra })("lib/reporting.ts");
}

test("the dashboard shows every KPI of the company context under its exact name and column", () => {
  const { KPIS } = loadReporting();
  assert.deepEqual(KPIS.map((kpi) => [kpi.name, kpi.key]), CONTEXT_KPIS);
  for (const kpi of KPIS) assert.ok(kpi.meaning.length > 30 && kpi.useFor.length > 20, `${kpi.name} is explained in plain words`);
});

test("a week is labelled as the Monday to Sunday it covers", () => {
  const { weekLabel, shiftWeek } = loadReporting();
  assert.equal(weekLabel("2026-07-13"), "Monday, July 13, 2026 to Sunday, July 19, 2026");
  assert.equal(weekLabel("2026-12-28"), "Monday, December 28, 2026 to Sunday, January 3, 2027");
  assert.equal(shiftWeek("2026-07-13", 1), "2026-07-20");
  assert.equal(shiftWeek("2026-07-13", -1), "2026-07-06");
  assert.equal(shiftWeek("2026-12-28", 1), "2027-01-04");
});

test("clients, warehouses and rates are written the way a person reads them", () => {
  const { clientName, warehouseName, percent } = loadReporting();
  assert.equal(clientName("fashion-co"), "Fashion Co");
  assert.equal(clientName("purestep-footwear"), "Purestep Footwear");
  assert.equal(warehouseName("los_angeles"), "Los Angeles");
  assert.equal(warehouseName("zaragoza"), "Zaragoza");
  assert.equal(percent(0.25), "25%");
  assert.equal(percent(0.002), "0.2%");
  assert.equal(percent(0), "0%");
  assert.equal(percent(1), "100%");
});

test("warehouse totals add the clients of one warehouse and never mix warehouses", () => {
  const { warehouseTotals } = loadReporting();
  const entry = (warehouse, client_id, inbound) => ({ warehouse, client_id, inbound_units_count: inbound, outbound_orders_count: 0, stockout_events_count: 0, discrepancy_events_count: 0, discrepancy_rate: 0 });
  const totals = warehouseTotals([entry("zaragoza", "a", 5), entry("los_angeles", "a", 4200), entry("los_angeles", "b", 30)], "inbound_units_count");
  assert.deepEqual(totals, [{ warehouse: "los_angeles", total: 4230 }, { warehouse: "zaragoza", total: 5 }]);
});

test("the page asks the reporting endpoints with the token and the requested week", async () => {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), auth: init.headers.get("Authorization") });
    return { ok: true, status: 200, text: async () => JSON.stringify({ week_start: "2026-07-13", entries: [] }) };
  };
  const { reportingApi } = loadReporting();
  await reportingApi.weekly("token-1");
  await reportingApi.weekly("token-1", "2026-07-13");
  await reportingApi.latestRun("token-1");
  assert.deepEqual(calls.map((call) => call.url.replace(/^.*(?=\/reporting)/, "")), [
    "/reporting/weekly-warehouse-client-performance",
    "/reporting/weekly-warehouse-client-performance?week_start=2026-07-13",
    "/reporting/pipeline-runs/latest",
  ]);
  assert.ok(calls.every((call) => call.auth === "Bearer token-1"));
});
