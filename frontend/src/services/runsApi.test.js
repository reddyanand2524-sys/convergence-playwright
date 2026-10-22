import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError } from "./authedFetch.js";
import { buildRunsQuery, normalizeRunsPage } from "./runsApi.js";

test("the default query is page and page_size only (no framework)", () => {
  assert.equal(buildRunsQuery().toString(), "page=1&page_size=20");
});

test("a framework filter is added next to every other filter without disturbing them", () => {
  const query = buildRunsQuery({
    page: 3,
    pageSize: 50,
    status: "FAILED",
    browser: "chrome",
    framework: "playwright",
    runId: "42",
    createdFrom: "2026-10-01",
    createdTo: "2026-10-31",
    search: "login",
  });

  assert.deepEqual(Object.fromEntries(query), {
    page: "3",
    page_size: "50",
    status: "FAILED",
    browser: "chrome",
    framework: "playwright",
    run_id: "42",
    created_from: "2026-10-01",
    created_to: "2026-10-31",
    search: "login",
  });
});

test("an empty or blank framework is omitted, like the other empty filters", () => {
  for (const framework of ["", "   ", undefined, null]) {
    assert.equal(buildRunsQuery({ framework }).has("framework"), false);
  }
  assert.equal(buildRunsQuery({ framework: "selenium" }).get("framework"), "selenium");
});

// ---- normalizeRunsPage ---------------------------------------------------------------

const row = (id, framework = "selenium") => ({
  id,
  framework,
  status: "COMPLETED",
  test_count: 3,
  passed_count: 3,
  failed_count: 0,
  skipped_count: 0,
  screenshot_count: 1,
});

test("a full envelope keeps its items and pagination (framework and counts untouched)", () => {
  const items = [row(2, "playwright"), row(1)];
  const page = normalizeRunsPage({ items, page: 2, page_size: 20, total: 41, total_pages: 3 }, 1, 20);
  assert.deepEqual(page, { items, page: 2, page_size: 20, total: 41, total_pages: 3 });
  assert.equal(page.items[0].framework, "playwright");
  assert.equal(page.items[0].screenshot_count, 1);
});

test("an empty items list is a valid empty page", () => {
  const page = normalizeRunsPage({ items: [], page: 1, page_size: 20, total: 0, total_pages: 0 }, 1, 20);
  assert.deepEqual(page.items, []);
  assert.equal(page.total, 0);
  assert.equal(page.total_pages, 0);
});

test("missing pagination fields fall back to the request and the item count", () => {
  const items = [row(1), row(2), row(3)];
  const page = normalizeRunsPage({ items }, 4, 50);
  assert.deepEqual(page, { items, page: 4, page_size: 50, total: 3, total_pages: 1 });
});

test("non-numeric pagination fields fall back the same way", () => {
  const page = normalizeRunsPage({ items: [row(1)], page: "abc", page_size: {}, total: undefined, total_pages: "x" }, 2, 10);
  assert.deepEqual([page.page, page.page_size, page.total, page.total_pages], [2, 10, 1, 1]);
});

test("numeric strings are accepted as numbers", () => {
  const page = normalizeRunsPage({ items: [], page: "2", page_size: "20", total: "0", total_pages: "0" }, 1, 20);
  assert.deepEqual([page.page, page.page_size, page.total, page.total_pages], [2, 20, 0, 0]);
});

test("the old bare-array reply is wrapped as a single page", () => {
  const items = [row(1), row(2)];
  assert.deepEqual(normalizeRunsPage(items, 3, 20), { items, page: 1, page_size: 2, total: 2, total_pages: 1 });
});

test("a malformed reply is rejected", () => {
  for (const bad of [null, undefined, "oops", 7, {}, { items: "nope" }, { detail: "x" }]) {
    assert.throws(
      () => normalizeRunsPage(bad, 1, 20),
      (err) => err instanceof ApiError && err.status === 500,
      `input: ${JSON.stringify(bad)}`
    );
  }
});
