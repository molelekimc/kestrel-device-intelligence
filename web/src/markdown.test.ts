import { describe, expect, it } from "vitest";
import { parseBlocks, parseInline } from "./markdown";

describe("chat markdown", () => {
  it("parses a table, including rows separated by blank lines", () => {
    const text = "Service desk:\n\n| Plant | Tickets |\n\n|---|---|\n\n| Durban | 249 |\n| Rosslyn | 515 |\n\nAfter.";
    expect(parseBlocks(text)).toEqual([
      { kind: "p", text: "Service desk:" },
      { kind: "table", headers: ["Plant", "Tickets"], rows: [["Durban", "249"], ["Rosslyn", "515"]] },
      { kind: "p", text: "After." },
    ]);
  });

  it("leaves a lone pipe line as text when there is no rule row", () => {
    expect(parseBlocks("| not a table")).toEqual([{ kind: "p", text: "| not a table" }]);
  });

  it("parses headings and both list kinds", () => {
    expect(parseBlocks("## Next\n1. Check W42\n2. Ask about backfill\n- a\n- b")).toEqual([
      { kind: "h", text: "Next" },
      { kind: "ol", items: ["Check W42", "Ask about backfill"] },
      { kind: "ul", items: ["a", "b"] },
    ]);
  });

  it("parses bold and code spans", () => {
    expect(parseInline("see `telemetry_outage` and **No.**")).toEqual([
      { kind: "text", text: "see " }, { kind: "code", text: "telemetry_outage" },
      { kind: "text", text: " and " }, { kind: "bold", text: "No." },
    ]);
  });
});
