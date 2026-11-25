import { describe, expect, it } from "vitest";

import { segmentAnswer } from "./citations";

const refunds = { id: "refunds-and-billing#5", doc_title: "Refunds and billing", section: "How refunds are paid" };
const plans = { id: "plans-and-limits#2", doc_title: "Plans and limits", section: "Pro" };

describe("segmentAnswer", () => {
  it("numbers known markers and attaches them to the preceding text", () => {
    const segments = segmentAnswer(
      "Refunds take 5 to 10 days. [refunds-and-billing#5] Pro has 30 days. [plans-and-limits#2]",
      [refunds, plans],
      false,
    );
    expect(segments).toEqual([
      { kind: "text", text: "Refunds take 5 to 10 days." },
      { kind: "cite", n: 1, citation: refunds },
      { kind: "text", text: " Pro has 30 days." },
      { kind: "cite", n: 2, citation: plans },
    ]);
  });

  it("drops markers the server didn't confirm", () => {
    const segments = segmentAnswer("Made up. [billing-faq#9] Real.", [refunds], false);
    expect(segments).toEqual([{ kind: "text", text: "Made up. Real." }]);
  });

  it("hides markers, including a half-streamed one, before citations arrive", () => {
    const segments = segmentAnswer(
      "Refunds take 5 to 10 days. [refunds-and-billing#5] Also [refunds-and",
      undefined,
      true,
    );
    expect(segments).toEqual([{ kind: "text", text: "Refunds take 5 to 10 days. Also" }]);
  });

  it("collapses a repeated marker", () => {
    const segments = segmentAnswer("A. [refunds-and-billing#5] [refunds-and-billing#5]", [refunds], false);
    expect(segments.filter((s) => s.kind === "cite")).toHaveLength(1);
  });
});
