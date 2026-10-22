import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { ApiError, authedFetch, detailToMessage, fallbackMessage, normalizeBase } from "./authedFetch.js";
import { publicRequest } from "./api.js";

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

function mockFetch(status, body) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return {
      ok: status >= 200 && status < 300,
      status,
      headers: { get: () => null },
      text: async () => (body === undefined ? "" : typeof body === "string" ? body : JSON.stringify(body)),
    };
  };
  return calls;
}

const VALIDATION = [
  { loc: ["body", "email"], msg: "value is not a valid email address", type: "value_error" },
  { loc: ["body", "password"], msg: "String should have at least 8 characters", type: "string_too_short" },
];

test("normalizeBase strips trailing slashes and whitespace", () => {
  assert.equal(normalizeBase("http://h/api"), "http://h/api");
  assert.equal(normalizeBase("http://h/api/"), "http://h/api");
  assert.equal(normalizeBase(" http://h/api/// "), "http://h/api");
  assert.equal(normalizeBase(undefined), "");
});

test("detailToMessage handles strings, 422 arrays and unusable detail", () => {
  assert.equal(detailToMessage("Invalid credentials", "x"), "Invalid credentials");
  assert.equal(
    detailToMessage(VALIDATION, "x"),
    "value is not a valid email address; String should have at least 8 characters"
  );
  assert.equal(detailToMessage([], "fallback"), "fallback");
  assert.equal(detailToMessage(undefined, "fallback"), "fallback");
});

test("authedFetch never builds a double slash from a trailing-slash base", async () => {
  const calls = mockFetch(200, { ok: true });
  await authedFetch("/auth/login", { method: "POST", json: { a: 1 }, auth: false, base: "http://h/api/" });
  assert.equal(calls[0].url, "http://h/api/auth/login");
});

test("authedFetch reports a 422 array as a readable message and keeps the detail", async () => {
  mockFetch(422, { detail: VALIDATION });
  await assert.rejects(
    authedFetch("/auth/register", { method: "POST", json: {}, auth: false, base: "http://h/api" }),
    (err) => {
      assert.ok(err instanceof ApiError);
      assert.equal(err.status, 422);
      assert.match(err.message, /not a valid email address; String should have at least 8/);
      assert.deepEqual(err.detail, VALIDATION);
      return true;
    }
  );
});

test("a network failure is an ApiError with status 0, distinct from HTTP errors", async () => {
  globalThis.fetch = async () => {
    throw new TypeError("Failed to fetch");
  };
  await assert.rejects(authedFetch("/auth/login", { auth: false, base: "http://h/api" }), (err) => {
    assert.ok(err instanceof ApiError);
    assert.equal(err.status, 0);
    assert.match(err.message, /Cannot reach the backend/);
    return true;
  });
});

test("an unauthenticated 401 (wrong password) does not touch the session", async () => {
  // No window/localStorage exist under node: a session wipe here would throw a ReferenceError.
  mockFetch(401, { detail: "Invalid email or password" });
  await assert.rejects(
    authedFetch("/auth/login", { method: "POST", json: {}, auth: false, base: "http://h/api" }),
    (err) => err.status === 401 && err.message === "Invalid email or password"
  );
});

test("publicRequest sends auth:false and returns the parsed body", async () => {
  let seen;
  const data = await publicRequest("/auth/login", "Login", { method: "POST" }, async (path, options) => {
    seen = { path, options };
    return { access_token: "t", user: {} };
  });
  assert.equal(seen.path, "/auth/login");
  assert.equal(seen.options.auth, false);
  assert.equal(data.access_token, "t");
});

test("publicRequest keeps 422 and string-detail errors unchanged", async () => {
  const validation = new ApiError("value is not a valid email address", 422, VALIDATION);
  await assert.rejects(publicRequest("/x", "Registration", {}, async () => { throw validation; }), (err) => err === validation);

  const wrong = new ApiError("Invalid email or password", 401, "Invalid email or password");
  await assert.rejects(publicRequest("/x", "Login", {}, async () => { throw wrong; }), (err) => err === wrong);
});

test("publicRequest keeps a network failure distinguishable (status 0)", async () => {
  const offline = new ApiError("Cannot reach the backend.", 0);
  await assert.rejects(publicRequest("/x", "Login", {}, async () => { throw offline; }), (err) => err === offline && err.status === 0);
});

test("publicRequest explains a proxy/HTML error reply that carries no detail", async () => {
  const bad = new ApiError("Request failed (HTTP 502).", 502, undefined);
  await assert.rejects(publicRequest("/x", "Login", {}, async () => { throw bad; }), (err) => {
    assert.equal(err.status, 502);
    assert.match(err.message, /^Login failed: the backend returned an invalid response \(HTTP 502\)/);
    return true;
  });

  const other = new ApiError("Request failed (HTTP 429).", 429, undefined);
  await assert.rejects(publicRequest("/x", "Login", {}, async () => { throw other; }), (err) => err.message === "Login failed (HTTP 429).");
});

test("publicRequest rejects an empty success body", async () => {
  await assert.rejects(publicRequest("/x", "Registration", {}, async () => null), (err) => {
    assert.equal(err.status, 502);
    assert.match(err.message, /^Registration failed: the backend returned an empty or invalid response/);
    return true;
  });
});

test("error messages never contain the request body (passwords)", async () => {
  mockFetch(422, { detail: [{ loc: ["body", "password"], msg: "too short", input: "hunter2-secret" }] });
  await assert.rejects(
    publicRequest("/auth/login", "Login", { method: "POST", json: { email: "a@b.c", password: "hunter2-secret" }, base: "http://h/api" }),
    (err) => !err.message.includes("hunter2-secret") && err.message === "too short"
  );
});

// ---- backend refusals (403 / 409 / 422) keep their own message ---------------------------

for (const status of [403, 409]) {
  test(`HTTP ${status} shows the backend detail as sent`, async () => {
    const detail = status === 403 ? "Playwright is not enabled for this account" : "Cannot change framework while runs are active";
    mockFetch(status, { detail });
    await assert.rejects(authedFetch("/projects/1", { method: "PATCH", json: {}, auth: false, base: "http://h/api" }), (err) => {
      assert.equal(err.status, status);
      assert.equal(err.message, detail);
      assert.equal(err.detail, detail);
      return true;
    });
  });
}

test("different 409 messages stay different (they are never collapsed into one text)", async () => {
  const messages = [];
  for (const detail of ["Playwright runtime is not available", "Project has enabled schedules"]) {
    mockFetch(409, { detail });
    await authedFetch("/projects/1/runs/2/start", { method: "POST", auth: false, base: "http://h/api" }).catch((err) => messages.push(err.message));
  }
  assert.deepEqual(messages, ["Playwright runtime is not available", "Project has enabled schedules"]);
});

test("a 422 from a framework change lists the validation messages", async () => {
  mockFetch(422, { detail: [{ loc: ["body", "browser"], msg: "Playwright supports Chrome only", type: "value_error" }] });
  await assert.rejects(
    authedFetch("/projects/1", { method: "PATCH", json: { framework: "playwright" }, auth: false, base: "http://h/api" }),
    (err) => err.status === 422 && err.message === "Playwright supports Chrome only"
  );
});

test("non-JSON or empty error bodies fall back to a sensible message", async () => {
  for (const [status, body, expected] of [
    [403, "<html>Forbidden</html>", "You do not have permission to do this (HTTP 403)."],
    [409, "", "The request conflicts with the current state (HTTP 409)."],
    [422, "not json", "The request was rejected as invalid (HTTP 422)."],
    [500, "<html>oops</html>", "Request failed (HTTP 500)."],
  ]) {
    mockFetch(status, body);
    await assert.rejects(authedFetch("/x", { auth: false, base: "http://h/api" }), (err) => err.status === status && err.message === expected);
  }
  assert.equal(fallbackMessage(404), "Request failed (HTTP 404).");
});
