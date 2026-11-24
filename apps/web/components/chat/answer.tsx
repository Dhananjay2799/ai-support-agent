import { BookOpen } from "lucide-react";

import { segmentAnswer } from "@/lib/citations";
import type { Citation } from "@/lib/generated/agent-events";

/** Answer text with numbered citation markers, followed by the cited sources. */
export function Answer({
  text,
  citations,
  streaming,
}: {
  text: string;
  citations?: Citation[];
  streaming: boolean;
}) {
  const segments = segmentAnswer(text, citations, streaming);
  const cited = new Map(
    segments.flatMap((s) => (s.kind === "cite" ? [[s.n, s.citation] as const] : [])),
  );

  return (
    <>
      <p className="leading-relaxed whitespace-pre-wrap">
        {segments.map((segment, i) =>
          segment.kind === "text" ? (
            segment.text
          ) : (
            <sup key={i} className="ml-0.5">
              <a
                href={`#source-${segment.n}`}
                className="rounded px-0.5 font-mono text-[10px] font-medium text-primary hover:bg-primary/10"
                title={`${segment.citation.doc_title} › ${segment.citation.section}`}
              >
                {segment.n}
              </a>
            </sup>
          ),
        )}
      </p>
      {cited.size > 0 && (
        <ol className="mt-3 flex flex-wrap gap-1.5" aria-label="Sources">
          {[...cited.entries()]
            .sort(([a], [b]) => a - b)
            .map(([n, citation]) => (
              <li
                key={citation.id}
                id={`source-${n}`}
                className="flex items-center gap-1.5 rounded-md border bg-muted/40 px-2 py-1 text-[11px] text-muted-foreground"
              >
                <span className="font-mono font-medium text-primary">{n}</span>
                <BookOpen className="size-3" aria-hidden />
                <span>
                  {citation.doc_title}
                  <span className="text-muted-foreground/70"> › {citation.section.split(" > ").at(-1)}</span>
                </span>
              </li>
            ))}
        </ol>
      )}
    </>
  );
}
