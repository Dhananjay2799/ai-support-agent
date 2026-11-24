import { describe, expect, it } from "vitest";

import { SSEParser } from "./sse";

describe("SSEParser", () => {
  it("parses a complete message", () => {
    const parser = new SSEParser();
    const out = parser.feed('event: token\nid: 1\ndata: {"type":"token","text":"hi"}\n\n');
    expect(out).toEqual([{ event: "token", id: "1", data: '{"type":"token","text":"hi"}' }]);
  });

  it("handles messages split across arbitrary chunk boundaries", () => {
    const parser = new SSEParser();
    const wire = "event: step\ndata: {\"a\":1}\n\nevent: done\ndata: {\"b\":2}\n\n";
    const out = [...wire].flatMap((char) => parser.feed(char));
    expect(out.map((m) => m.event)).toEqual(["step", "done"]);
    expect(out[1].data).toBe('{"b":2}');
  });

  it("supports CRLF line endings, including a split \\r\\n", () => {
    const parser = new SSEParser();
    const first = parser.feed("event: token\r\ndata: x\r");
    const second = parser.feed("\n\r\n");
    expect(first).toEqual([]);
    expect(second).toEqual([{ event: "token", data: "x", id: null }]);
  });

  it("joins multi-line data and ignores comments", () => {
    const parser = new SSEParser();
    const out = parser.feed(": keep-alive\ndata: line one\ndata: line two\n\n");
    expect(out).toEqual([{ event: "message", data: "line one\nline two", id: null }]);
  });

  it("does not dispatch blocks without data", () => {
    const parser = new SSEParser();
    expect(parser.feed("event: ping\n\n")).toEqual([]);
  });
});
