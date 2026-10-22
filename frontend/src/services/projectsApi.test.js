import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError } from "./authedFetch.js";
import { normalizeProjectList } from "./projectsApi.js";

const project = (id, framework = "selenium") => ({
  id,
  name: `P${id}`,
  frequency: "Daily",
  browser: "chrome",
  framework,
  owner_id: 1,
  is_active: true,
});

test("a bare project array is returned as is, framework included", () => {
  const data = [project(1), project(2, "playwright")];
  const result = normalizeProjectList(data);
  assert.equal(result, data);
  assert.deepEqual(result.map((p) => p.framework), ["selenium", "playwright"]);
});

test("an empty project list is a valid, empty result", () => {
  assert.deepEqual(normalizeProjectList([]), []);
});

test("a malformed project reply is an error, never silently 'no projects'", () => {
  for (const bad of [null, undefined, "oops", 7, {}, { items: [project(1)] }, { detail: "x" }]) {
    assert.throws(
      () => normalizeProjectList(bad),
      (err) => err instanceof ApiError && err.status === 500 && /unexpected response/.test(err.message),
      `input: ${JSON.stringify(bad)}`
    );
  }
});
