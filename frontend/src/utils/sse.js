/*
 * Incremental Server-Sent Events parser (https://html.spec.whatwg.org/multipage/server-sent-events.html).
 *
 * push(text) accepts any slice of the stream - a chunk may end in the middle of a
 * line, of a field, or between "\r" and "\n" - and returns the events completed so
 * far: { id, event, data }. Comment lines (": heartbeat") are ignored. Text that has
 * not reached its blank-line terminator stays buffered for the next push.
 */
export function createSseParser() {
  let buffer = "";
  let eventName = "";
  let id = null;
  let dataLines = [];

  function handleLine(line, out) {
    if (line === "") {
      if (dataLines.length) {
        out.push({ id, event: eventName || "message", data: dataLines.join("\n") });
      }
      eventName = "";
      id = null;
      dataLines = [];
      return;
    }
    if (line.startsWith(":")) return; // comment / heartbeat

    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);

    if (field === "data") dataLines.push(value);
    else if (field === "event") eventName = value;
    else if (field === "id") id = value;
    // "retry" and unknown fields are ignored
  }

  return {
    push(text) {
      buffer += text;
      const out = [];
      let start = 0;

      for (let i = 0; i < buffer.length; i += 1) {
        const char = buffer[i];
        if (char !== "\n" && char !== "\r") continue;
        if (char === "\r" && i + 1 >= buffer.length) break; // could be "\r\n": wait for more
        const line = buffer.slice(start, i);
        if (char === "\r" && buffer[i + 1] === "\n") i += 1;
        handleLine(line, out);
        start = i + 1;
      }

      buffer = buffer.slice(start);
      return out;
    },
  };
}
