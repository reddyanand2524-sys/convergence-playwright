import assert from "node:assert/strict";
import { test } from "node:test";

import { FORBIDDEN_MESSAGE, NOT_FOUND_MESSAGE, pollRun } from "./pollRun.js";

const ACTIVE = ["QUEUED", "RUNNING"];
const err = (status, name = "ApiError") => Object.assign(new Error(`HTTP ${status}`), { status, name });

// Drives pollRun with a scripted fetcher; sleep resolves at once and a fake clock advances per sleep.
function harness(script, { maxMs = 1000, intervalMs = 100, controller = new AbortController() } = {}) {
  const log = { updates: [], errors: [], fetches: 0, sleeps: 0 };
  let clock = 0;
  const promise = pollRun({
    fetcher: async () => {
      const step = script[Math.min(log.fetches, script.length - 1)];
      log.fetches += 1;
      if (typeof step === "function") return step(controller);
      if (step instanceof Error) throw step;
      return step;
    },
    onUpdate: (run) => log.updates.push(run.status),
    onError: (message) => log.errors.push(message),
    isActive: (status) => ACTIVE.includes(status),
    statusOf: (run) => run.status,
    signal: controller.signal,
    intervalMs,
    maxMs,
    sleep: async () => {
      log.sleeps += 1;
      clock += intervalMs;
    },
    now: () => clock,
  });
  return { promise, log, controller };
}

test("stops on a terminal status after delivering it", async () => {
  const { promise, log } = harness([{ status: "RUNNING" }, { status: "COMPLETED" }, { status: "RUNNING" }]);
  assert.deepEqual(await promise, { reason: "terminal" });
  assert.deepEqual(log.updates, ["RUNNING", "COMPLETED"]);
  assert.equal(log.fetches, 2);
});

test("CANCELLED is terminal too", async () => {
  const { promise, log } = harness([{ status: "RUNNING" }, { status: "CANCELLED" }]);
  assert.deepEqual(await promise, { reason: "terminal" });
  assert.equal(log.fetches, 2);
});

test("an unknown status is not active, so polling stops rather than looping forever", async () => {
  const { promise, log } = harness([{ status: "SOMETHING_NEW" }]);
  assert.deepEqual(await promise, { reason: "terminal" });
  assert.equal(log.fetches, 1);
});

test("403 stops polling at once with a clear message", async () => {
  const { promise, log } = harness([{ status: "RUNNING" }, err(403), { status: "RUNNING" }]);
  assert.deepEqual(await promise, { reason: "forbidden" });
  assert.equal(log.fetches, 2);
  assert.equal(log.errors.at(-1), FORBIDDEN_MESSAGE);
});

test("404 stops polling with the not-found message", async () => {
  const { promise, log } = harness([err(404), { status: "RUNNING" }]);
  assert.deepEqual(await promise, { reason: "not-found" });
  assert.equal(log.fetches, 1);
  assert.equal(log.errors.at(-1), NOT_FOUND_MESSAGE);
});

test("401 stops polling silently (the session redirect handles it)", async () => {
  const { promise, log } = harness([err(401), { status: "RUNNING" }]);
  assert.deepEqual(await promise, { reason: "unauthorized" });
  assert.equal(log.fetches, 1);
  assert.deepEqual(log.errors, []);
});

test("transient errors (network, 500, 502, 409, 422) keep polling and recover", async () => {
  const { promise, log } = harness([err(0), err(500), err(502), err(409), err(422), { status: "RUNNING" }, { status: "FAILED" }]);
  assert.deepEqual(await promise, { reason: "terminal" });
  assert.equal(log.fetches, 7);
  assert.equal(log.errors[0], "Unable to connect to the backend. Retrying...");
  assert.equal(log.errors[1], "Could not refresh run status. Retrying...");
  assert.equal(log.errors.at(-1), "", "the error is cleared by the next good response");
  assert.deepEqual(log.updates, ["RUNNING", "FAILED"]);
});

test("gives up with 'timeout' when the run stays active past maxMs", async () => {
  const { promise, log } = harness([{ status: "RUNNING" }], { maxMs: 500, intervalMs: 100 });
  assert.deepEqual(await promise, { reason: "timeout" });
  assert.equal(log.fetches, 5);
});

test("a terminal status wins over the time limit", async () => {
  const { promise } = harness([{ status: "RUNNING" }, { status: "COMPLETED" }], { maxMs: 200, intervalMs: 100 });
  assert.deepEqual(await promise, { reason: "terminal" });
});

test("aborting before the first request makes no request and no update", async () => {
  const controller = new AbortController();
  controller.abort();
  const { promise, log } = harness([{ status: "RUNNING" }], { controller });
  assert.deepEqual(await promise, { reason: "aborted" });
  assert.equal(log.fetches, 0);
  assert.deepEqual(log.updates, []);
});

test("unmount while a request is in flight drops its result and ends the loop", async () => {
  const { promise, log } = harness([
    (controller) => {
      controller.abort(); // component unmounted during the request
      return { status: "COMPLETED" }; // the late reply must be ignored
    },
  ]);
  assert.deepEqual(await promise, { reason: "aborted" });
  assert.deepEqual(log.updates, []);
  assert.equal(log.sleeps, 1);
});

test("an AbortError from the fetcher ends the loop without an error message", async () => {
  const { promise, log } = harness([err(0, "AbortError")]);
  assert.deepEqual(await promise, { reason: "aborted" });
  assert.deepEqual(log.errors, []);
  assert.equal(log.fetches, 1);
});
