import type { Span } from "@/lib/generated/agent-events";
import { formatCost, formatMs } from "@/lib/trace";

type Attributes = Record<string, unknown>;

/** Attributes shown in the summary grid, in this order; the rest get their own sections. */
const FIELDS: [string, string][] = [
  ["model", "Model"],
  ["tool", "Tool"],
  ["attempt", "Attempt"],
  ["status", "Status"],
  ["error", "Error"],
  ["recovery", "Then"],
  ["detail", "Result"],
  ["input_tokens", "Input tokens"],
  ["cached_tokens", "Cached"],
  ["output_tokens", "Output tokens"],
  ["reasoning_tokens", "Reasoning"],
  ["cost_usd", "Cost"],
  ["ttft_ms", "First token"],
  ["query", "Query"],
  ["question", "Question"],
  ["tier", "Plan"],
  ["best_cosine", "Best match"],
  ["decision", "Decision"],
  ["band", "Band"],
  ["confidence", "Confidence"],
  ["reason", "Reason"],
  ["utterance", "Heard"],
  ["offset_ms", "Delegated at"],
  ["channel", "Channel"],
  ["message", "Message"],
  ["simulated", "Simulated"],
  ["model_calls", "Model calls"],
];
const SECTIONS = new Set([
  "prompt",
  "output",
  "arguments",
  "chunks",
  "failures",
  "verdict",
  "thresholds",
  "supporting",
]);

function display(key: string, value: unknown): string {
  if (key === "cost_usd" && typeof value === "number") return formatCost(value);
  if ((key === "ttft_ms" || key === "offset_ms") && typeof value === "number") return formatMs(value);
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function Code({ children }: { children: string }) {
  return (
    <pre className="max-h-56 overflow-auto rounded-md bg-muted/60 p-2 font-mono text-[10.5px] leading-relaxed whitespace-pre-wrap break-words">
      {children}
    </pre>
  );
}

function Section({ title, children, open = false }: { title: string; children: React.ReactNode; open?: boolean }) {
  return (
    <details open={open} className="group">
      <summary className="cursor-pointer text-[11px] font-medium text-muted-foreground select-none hover:text-foreground">
        {title}
      </summary>
      <div className="mt-1.5">{children}</div>
    </details>
  );
}

export function SpanDetails({ span }: { span: Span }) {
  const a = span.attributes as Attributes;
  const fields = FIELDS.filter(([key]) => a[key] !== undefined && a[key] !== null && a[key] !== "");
  const known = new Set([
    ...FIELDS.map(([k]) => k),
    ...SECTIONS,
    "fallback",
    "effort",
    "passages",
    "texts",
    "attempts",
    "kept",
    "dropped",
    "persona",
    "delegation_id",
  ]);
  const extra = Object.fromEntries(Object.entries(a).filter(([k]) => !known.has(k)));
  const prompt = a.prompt as { instructions?: string; input?: unknown[] } | undefined;
  const output = a.output as { text?: string; tool_calls?: { name: string; arguments: string }[] } | string | undefined;
  const chunks = a.chunks as
    | { id: string; section: string; cosine: number; semantic_rank: number | null; lexical_rank: number | null }[]
    | undefined;

  return (
    <div className="space-y-2.5 border-l-2 border-muted py-2 pr-1 pl-3 text-xs">
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="text-muted-foreground">Duration</dt>
        <dd className="font-mono">{formatMs((span.end_ms ?? span.start_ms) - span.start_ms)}</dd>
        {fields.map(([key, label]) => (
          <div key={key} className="contents">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className={key === "error" ? "text-destructive" : "break-words"}>{display(key, a[key])}</dd>
          </div>
        ))}
        {typeof a.fallback === "boolean" && a.fallback && (
          <>
            <dt className="text-muted-foreground">Fallback</dt>
            <dd>yes: the main model failed every attempt</dd>
          </>
        )}
      </dl>

      {prompt && (
        <Section title="Prompt">
          <div className="space-y-2">
            {prompt.instructions && (
              <Section title="Instructions">
                <Code>{prompt.instructions}</Code>
              </Section>
            )}
            {prompt.input && (
              <Section title={`Input (${prompt.input.length} items)`} open>
                <Code>{prompt.input.map((item) => JSON.stringify(item)).join("\n")}</Code>
              </Section>
            )}
          </div>
        </Section>
      )}
      {output !== undefined && (
        <Section title="Output" open>
          {typeof output === "string" ? (
            <Code>{output}</Code>
          ) : (
            <Code>
              {[output.text, ...(output.tool_calls ?? []).map((call) => `→ ${call.name}(${call.arguments})`)]
                .filter(Boolean)
                .join("\n")}
            </Code>
          )}
        </Section>
      )}
      {a.arguments !== undefined && (
        <Section title="Arguments" open>
          <Code>{JSON.stringify(a.arguments, null, 2)}</Code>
        </Section>
      )}
      {Array.isArray(a.failures) && (
        <Section title="Failed attempts" open>
          <ul className="space-y-1 text-destructive">
            {(a.failures as string[]).map((failure) => (
              <li key={failure}>{failure}</li>
            ))}
          </ul>
        </Section>
      )}
      {chunks && (
        <Section title={`Passages (${chunks.length})`} open>
          <table className="w-full text-[11px]">
            <thead className="text-muted-foreground">
              <tr>
                <th className="text-left font-normal">Passage</th>
                <th className="text-right font-normal">Cosine</th>
                <th className="text-right font-normal" title="Semantic / keyword rank">
                  Ranks
                </th>
              </tr>
            </thead>
            <tbody>
              {chunks.map((chunk) => (
                <tr key={chunk.id} title={chunk.section}>
                  <td className="max-w-0 truncate py-0.5 pr-2 font-mono">{chunk.id}</td>
                  <td className="text-right font-mono">{chunk.cosine.toFixed(3)}</td>
                  <td className="text-right font-mono">
                    {chunk.semantic_rank ?? "–"}/{chunk.lexical_rank ?? "–"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}
      {a.verdict !== undefined && (
        <Section title="Judge verdict" open>
          <Code>{JSON.stringify(a.verdict, null, 2)}</Code>
        </Section>
      )}
      {Object.keys(extra).length > 0 && (
        <Section title="Other attributes">
          <Code>{JSON.stringify(extra, null, 2)}</Code>
        </Section>
      )}
    </div>
  );
}
