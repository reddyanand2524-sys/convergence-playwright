import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const page = readFileSync(new URL("./RunResultsPage.jsx", import.meta.url), "utf8");

test("a finished run without results shows the backend's explanation instead of the generic line", () => {
  assert.match(page, /data\.issue\s*\?\s*\(\s*<RunIssueNotice issue=\{data\.issue\} \/>/);
  // the generic message remains only as the fallback when no explanation exists
  assert.match(page, /No test results were captured for this run\./);
});

test("partial results (a timed-out run) show the explanation above the recorded tests", () => {
  assert.match(page, /tests\.length > 0 && data\.issue && <RunIssueNotice issue=\{data\.issue\} \/>/);
});

test("the explanation shows title, detail, hints, the real exit code and the output tail as plain text", () => {
  for (const field of ["issue.title", "issue.detail", "issue.hints", "issue.exit_code", "issue.output_tail"]) {
    assert.ok(page.includes(field), field);
  }
  assert.ok(!/dangerouslySetInnerHTML|innerHTML/.test(page));
});
