import type { Citation } from "@/lib/generated/agent-events";

export type Segment = { kind: "text"; text: string } | { kind: "cite"; n: number; citation: Citation };

const MARKER = /\s*\[([a-z0-9-]+#\d+)\]/g;
// A marker still arriving mid-stream, e.g. "…window. [refunds-and-bil"
const PARTIAL_MARKER = /\s*\[[a-z0-9-]*#?\d*$/;

/**
 * Split answer text into prose and numbered citation markers.
 *
 * `citations` is the server's checked list (only passages actually retrieved
 * this turn), numbered in order. Markers that aren't on it are dropped, and
 * while streaming (before the list arrives) all markers are hidden.
 */
export function segmentAnswer(text: string, citations: Citation[] | undefined, streaming: boolean): Segment[] {
  const source = streaming ? text.replace(PARTIAL_MARKER, "") : text;
  const numbers = new Map((citations ?? []).map((c, i) => [c.id, i + 1]));
  const segments: Segment[] = [];
  let last = 0;

  const pushText = (value: string) => {
    if (!value) return;
    const previous = segments.at(-1);
    if (previous?.kind === "text") previous.text += value;
    else segments.push({ kind: "text", text: value });
  };

  for (const match of source.matchAll(MARKER)) {
    pushText(source.slice(last, match.index));
    last = match.index + match[0].length;
    const n = numbers.get(match[1]);
    const alreadyCited = segments.at(-1)?.kind === "cite" && (segments.at(-1) as { n: number }).n === n;
    if (n !== undefined && !alreadyCited) {
      segments.push({ kind: "cite", n, citation: citations![n - 1] });
    }
  }
  pushText(source.slice(last));
  return segments;
}
