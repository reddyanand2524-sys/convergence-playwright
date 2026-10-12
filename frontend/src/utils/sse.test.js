import assert from "node:assert/strict";
import { test } from "node:test";

import { createSseParser } from "./sse.js";

const STREAM =
  ": connected\n\n" +
  'id: 1\nevent: run_started\ndata: {"seq":1}\n\n' +
  ": heartbeat\n\n" +
  'id: 2\nevent: step_started\ndata: {"seq":2,"a":\ndata: 1}\n\n' +
  'event: gap\ndata: {"first_seq":9}\n\n';

function parseWhole(text) {
  return createSseParser().push(text);
}

test("parses ids, event names, multi-line data and ignores comments", () => {
  const events = parseWhole(STREAM);
  assert.deepEqual(events.map((e) => [e.id, e.event]), [["1", "run_started"], ["2", "step_started"], [null, "gap"]]);
  assert.equal(events[1].data, '{"seq":2,"a":\n1}');
  assert.deepEqual(JSON.parse(events[0].data), { seq: 1 });
});

test("gives the same result for every possible chunk split, including one character at a time", () => {
  const expected = parseWhole(STREAM);
  for (let cut = 0; cut <= STREAM.length; cut += 1) {
    const parser = createSseParser();
    const got = [...parser.push(STREAM.slice(0, cut)), ...parser.push(STREAM.slice(cut))];
    assert.deepEqual(got, expected, `split at ${cut}`);
  }
  const parser = createSseParser();
  const bytewise = [...STREAM].flatMap((char) => parser.push(char));
  assert.deepEqual(bytewise, expected);
});

test("handles CRLF line endings, even when \\r and \\n arrive in different chunks", () => {
  const text = 'id: 5\r\nevent: step_passed\r\ndata: {"seq":5}\r\n\r\n';
  const parser = createSseParser();
  const got = [...parser.push(text.slice(0, text.length - 3)), ...parser.push(text.slice(text.length - 3))];
  assert.deepEqual(got, [{ id: "5", event: "step_passed", data: '{"seq":5}' }]);
  assert.deepEqual(parseWhole(text), got);
});

test("an event is held back until its blank line arrives", () => {
  const parser = createSseParser();
  assert.deepEqual(parser.push('id: 1\nevent: x\ndata: {"seq":1}\n'), []);
  assert.equal(parser.push("\n").length, 1);
});

test("an id does not leak into the next event", () => {
  const events = parseWhole("id: 1\ndata: a\n\ndata: b\n\n");
  assert.deepEqual(events.map((e) => e.id), ["1", null]);
});
