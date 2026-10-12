import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { followRunEvents, streamRunEvents } from "./runEventsApi.js";

const sse = (e) => `id: ${e.seq}\nevent: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`;
const E = (seq, type = "step_started") => ({ seq, run_id: 1, type, step_id: `t1-s${seq}` });

function fakeFetcher(chunks, log = {}) {
  return async (path, options) => {
    log.path = path;
    log.options = options;
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      pull(controller) {
        if (chunks.length === 0) controller.close();
        else controller.enqueue(encoder.encode(chunks.shift()));
      },
      cancel() {
        log.cancelled = true;
      },
    });
    return { body: stream };
  };
}

test("streamRunEvents parses messages split across chunk boundaries and uses ?after=", async () => {
  const text = sse(E(1)) + ": heartbeat\n\n" + sse(E(2, "step_passed"));
  const chunks = [text.slice(0, 17), text.slice(17, 60), text.slice(60)];
  const log = {};
  const got = [];
  const result = await streamRunEvents(5, 12, { after: 0, onEvent: (e) => got.push(e.seq), fetcher: fakeFetcher(chunks, log) });
  assert.deepEqual(got, [1, 2]);
  assert.equal(result.terminal, false); // the server closed without a terminal event
  assert.equal(log.path, "/projects/5/runs/12/events?after=0");
  assert.ok(!/token|bearer/i.test(log.path)); // never a credential in the URL
});

test("streamRunEvents stops reading at the terminal event and releases the stream", async () => {
  const log = {};
  const got = [];
  const chunks = [sse(E(1)) + sse(E(2, "run_completed")), sse(E(3, "step_started"))];
  const result = await streamRunEvents(1, 1, { onEvent: (e) => got.push(e.seq), fetcher: fakeFetcher(chunks, log) });
  assert.deepEqual(got, [1, 2]); // seq 3 is never read
  assert.equal(result.terminal, true);
  assert.equal(log.cancelled, true);
});

test("a malformed message is skipped and a gap message goes to onGap", async () => {
  const chunks = ["data: {oops\n\n", 'event: gap\ndata: {"first_seq":4}\n\n', sse(E(5, "run_failed"))];
  const events = [];
  const gaps = [];
  await streamRunEvents(1, 1, {
    onEvent: (e) => events.push(e.seq),
    onGap: (g) => gaps.push(g.first_seq),
    fetcher: fakeFetcher(chunks),
  });
  assert.deepEqual(events, [5]);
  assert.deepEqual(gaps, [4]);
});

test("HTTP errors from the fetcher propagate with their status", async () => {
  const fetcher = async () => {
    throw Object.assign(new Error("nope"), { status: 404 });
  };
  await assert.rejects(streamRunEvents(1, 1, { fetcher }), { status: 404 });
});

const noSleep = async () => {};

test("followRunEvents reconnects from the last processed seq and stops at the terminal event", async () => {
  const calls = [];
  const streamFn = async (_p, _r, { after, onOpen, onEvent }) => {
    calls.push(after);
    onOpen();
    if (calls.length === 1) {
      onEvent(E(1));
      onEvent(E(2));
      return { terminal: false }; // the connection dropped
    }
    onEvent(E(3));
    onEvent(E(4, "run_completed"));
    return { terminal: true };
  };
  const seen = [];
  const states = [];
  const out = await followRunEvents(1, 1, {
    signal: new AbortController().signal,
    streamFn,
    sleep: noSleep,
    onEvent: (e) => seen.push(e.seq),
    onConnection: (s) => states.push(s),
  });
  assert.deepEqual(calls, [0, 2]); // resumed after seq 2, not from 0
  assert.deepEqual(seen, [1, 2, 3, 4]);
  assert.equal(out.reason, "terminal");
  assert.deepEqual(states, ["connecting", "live", "reconnecting", "live", "ended"]);
});

test("a rejected request (401/403/404/422) is not retried", async () => {
  for (const status of [401, 403, 404, 422]) {
    let calls = 0;
    const streamFn = async () => {
      calls += 1;
      throw Object.assign(new Error("x"), { status });
    };
    const states = [];
    const out = await followRunEvents(1, 1, {
      signal: new AbortController().signal,
      streamFn,
      sleep: noSleep,
      onConnection: (s) => states.push(s),
    });
    assert.equal(calls, 1);
    assert.equal(out.reason, "rejected");
    assert.equal(states.at(-1), "error");
  }
});

test("transient failures retry with backoff and recover; endless failure gives up", async () => {
  const delays = [];
  let n = 0;
  const flaky = async (_p, _r, { onOpen, onEvent }) => {
    n += 1;
    if (n < 3) throw Object.assign(new Error("down"), { status: 0 });
    onOpen();
    onEvent(E(1, "run_failed"));
    return { terminal: true };
  };
  const out = await followRunEvents(1, 1, {
    signal: new AbortController().signal,
    streamFn: flaky,
    sleep: async (ms) => delays.push(ms),
  });
  assert.equal(out.reason, "terminal");
  assert.deepEqual(delays, [500, 1000]);

  let attempts = 0;
  const dead = async () => {
    attempts += 1;
    throw Object.assign(new Error("down"), { status: 500 });
  };
  const gaveUp = await followRunEvents(1, 1, {
    signal: new AbortController().signal,
    streamFn: dead,
    sleep: noSleep,
    maxFailures: 3,
  });
  assert.equal(gaveUp.reason, "gave-up");
  assert.equal(attempts, 4);
});

test("aborting (unmount / switching runs) stops the loop without another connection", async () => {
  const controller = new AbortController();
  let calls = 0;
  const streamFn = async () => {
    calls += 1;
    controller.abort();
    return { terminal: false };
  };
  const out = await followRunEvents(1, 1, { signal: controller.signal, streamFn, sleep: noSleep });
  assert.equal(out.reason, "aborted");
  assert.equal(calls, 1);
});

test("the run page keeps LiveBrowserViewer in a fixed slot with no key, untouched by the new code", () => {
  const page = readFileSync(new URL("../pages/projects/RunDetailsPage.jsx", import.meta.url), "utf8");
  assert.equal(page.match(/<LiveBrowserViewer\b[^>]*>/g).length, 1);
  assert.ok(page.includes("<LiveBrowserViewer run={run} />")); // same props, no key
  assert.ok(page.indexOf("<StepTimeline") < page.indexOf("<LiveBrowserViewer"));
  for (const file of ["../hooks/useRunEvents.js", "../components/StepTimeline.jsx", "./runEventsApi.js"]) {
    assert.ok(!readFileSync(new URL(file, import.meta.url), "utf8").includes("LiveBrowserViewer"));
  }
});
