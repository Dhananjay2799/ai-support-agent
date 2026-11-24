/**
 * Incremental Server-Sent Events parser for `fetch` streams.
 *
 * `EventSource` only supports GET, but each chat turn is a POST, so we read the
 * response body ourselves. Follows the WHATWG event-stream rules: lines end in
 * \n, \r\n or \r; `data` lines are joined with \n; a blank line dispatches.
 */

export interface SSEMessage {
  event: string;
  data: string;
  id: string | null;
}

export class SSEParser {
  private buffer = "";
  private event = "";
  private data: string[] = [];
  private id: string | null = null;

  /** Feed a decoded text chunk; returns every message completed by it. */
  feed(chunk: string): SSEMessage[] {
    this.buffer += chunk;
    const messages: SSEMessage[] = [];

    for (;;) {
      const match = /\r\n|\r|\n/.exec(this.buffer);
      if (!match) break;
      // A lone \r at the very end may be the first half of \r\n: wait for more.
      if (match[0] === "\r" && match.index === this.buffer.length - 1) break;

      const line = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      const message = this.processLine(line);
      if (message) messages.push(message);
    }
    return messages;
  }

  private processLine(line: string): SSEMessage | null {
    if (line === "") return this.dispatch();
    if (line.startsWith(":")) return null; // comment / keep-alive

    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);

    if (field === "event") this.event = value;
    else if (field === "data") this.data.push(value);
    else if (field === "id" && !value.includes("\0")) this.id = value;
    return null;
  }

  private dispatch(): SSEMessage | null {
    if (this.data.length === 0) {
      this.event = "";
      return null;
    }
    const message: SSEMessage = {
      event: this.event || "message",
      data: this.data.join("\n"),
      id: this.id,
    };
    this.event = "";
    this.data = [];
    return message;
  }
}
