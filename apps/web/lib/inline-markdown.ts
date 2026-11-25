export type InlinePart = { kind: "text" | "bold" | "code"; text: string };

const INLINE = /(\*\*[^*\n]+?\*\*|`[^`\n]+`)/g;

/** Splits answer text into plain, **bold** and `code` runs. Anything else stays literal text. */
export function inlineMarkdown(text: string): InlinePart[] {
  return text
    .split(INLINE)
    .filter((part) => part !== "")
    .map((part) => {
      if (part.length > 4 && part.startsWith("**") && part.endsWith("**"))
        return { kind: "bold", text: part.slice(2, -2) };
      if (part.length > 2 && part.startsWith("`") && part.endsWith("`"))
        return { kind: "code", text: part.slice(1, -1) };
      return { kind: "text", text: part };
    });
}
