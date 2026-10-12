/*
 * Client for GET /projects/{pid}/runs/{rid}/events (Server-Sent Events).
 *
 * streamRunEvents  - one authenticated fetch-streaming connection.
 * followRunEvents  - keeps following a run across disconnects: reconnects with the
 *                    last processed seq (?after=), stops at the run's terminal event,
 *                    on a rejected request (401/403/404/422), when aborted, or after
 *                    too many consecutive failures.
 *
 * Both take injectable collaborators so they can be tested without a browser.
 */
import { authedStream } from "./authedFetch.js";
import { createSseParser } from "../utils/sse.js";
import { TERMINAL_EVENT_TYPES } from "../utils/runTimeline.js";

export async function streamRunEvents(
  projectId,
  runId,
  { after = 0, signal, onOpen, onEvent, onGap, fetcher = authedStream } = {}
) {
  const cursor = Number.isInteger(after) && after > 0 ? after : 0;
  const response = await fetcher(`/projects/${projectId}/runs/${runId}/events?after=${cursor}`, { signal });
  const reader = response.body?.getReader?.();
  if (!reader) throw new Error("This browser cannot stream live updates.");

  onOpen?.();
  const decoder = new TextDecoder();
  const parser = createSseParser();

  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return { terminal: false };

      for (const message of parser.push(decoder.decode(value, { stream: true }))) {
        let payload;
        try {
          payload = JSON.parse(message.data);
        } catch {
          continue; // a malformed message never ends the stream
        }
        if (!payload || typeof payload !== "object") continue;

        if (message.event === "gap") {
          onGap?.(payload);
          continue;
        }
        onEvent?.(payload);
        if (TERMINAL_EVENT_TYPES.includes(payload.type)) return { terminal: true };
      }
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // already closed
    }
  }
}

export function abortableSleep(ms, signal) {
  return new Promise((resolve) => {
    if (signal?.aborted) return resolve();
    const timer = setTimeout(done, ms);
    function done() {
      clearTimeout(timer);
      signal?.removeEventListener?.("abort", done);
      resolve();
    }
    signal?.addEventListener?.("abort", done, { once: true });
  });
}

const REJECTED = [401, 403, 404, 422];

export async function followRunEvents(
  projectId,
  runId,
  {
    signal,
    startAfter = 0,
    onEvent,
    onGap,
    onConnection,
    streamFn = streamRunEvents,
    fetcher,
    sleep = abortableSleep,
    maxFailures = 6,
    maxQuietCloses = 20,
  } = {}
) {
  let lastSeq = startAfter;
  let failures = 0;
  let quietCloses = 0;
  let first = true;

  while (!signal?.aborted) {
    onConnection?.(first ? "connecting" : "reconnecting");
    let gotEvent = false;

    try {
      const result = await streamFn(projectId, runId, {
        after: lastSeq,
        signal,
        fetcher,
        onOpen() {
          failures = 0;
          onConnection?.("live");
        },
        onEvent(event) {
          gotEvent = true;
          if (Number.isInteger(event?.seq) && event.seq > lastSeq) lastSeq = event.seq;
          onEvent?.(event);
        },
        onGap,
      });

      if (signal?.aborted) return { reason: "aborted", lastSeq };
      if (result?.terminal) {
        onConnection?.("ended");
        return { reason: "terminal", lastSeq };
      }
      // Closed without a terminal event (proxy or server time limit): resume.
      quietCloses = gotEvent ? 0 : quietCloses + 1;
      if (quietCloses > maxQuietCloses) {
        onConnection?.("error");
        return { reason: "gave-up", lastSeq };
      }
    } catch (error) {
      if (signal?.aborted || error?.name === "AbortError") return { reason: "aborted", lastSeq };
      if (REJECTED.includes(error?.status)) {
        onConnection?.("error");
        return { reason: "rejected", error, lastSeq };
      }
      failures += 1;
      if (failures > maxFailures) {
        onConnection?.("error");
        return { reason: "gave-up", error, lastSeq };
      }
    }

    first = false;
    await sleep(failures > 0 ? Math.min(500 * 2 ** (failures - 1), 8000) : 1000, signal);
  }

  return { reason: "aborted", lastSeq };
}
