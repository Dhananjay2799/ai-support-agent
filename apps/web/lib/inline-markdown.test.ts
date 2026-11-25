import { describe, expect, it } from "vitest";

import { inlineMarkdown } from "./inline-markdown";

describe("inlineMarkdown", () => {
  it("formats bold and code runs", () => {
    expect(inlineMarkdown("Open **Settings > Storage** to fix `E-507`.")).toEqual([
      { kind: "text", text: "Open " },
      { kind: "bold", text: "Settings > Storage" },
      { kind: "text", text: " to fix " },
      { kind: "code", text: "E-507" },
      { kind: "text", text: "." },
    ]);
  });

  it("leaves unfinished markers literal while streaming", () => {
    expect(inlineMarkdown("Open **Settings")).toEqual([{ kind: "text", text: "Open **Settings" }]);
    expect(inlineMarkdown("a `b")).toEqual([{ kind: "text", text: "a `b" }]);
  });

  it("returns plain text untouched", () => {
    expect(inlineMarkdown("Refunds take 5 to 10 business days.")).toEqual([
      { kind: "text", text: "Refunds take 5 to 10 business days." },
    ]);
  });
});
