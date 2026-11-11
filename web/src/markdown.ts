// The small subset of Markdown the chat answers use, parsed into blocks; rendering stays in React (no HTML injection).
export type Block =
  | { kind: "h"; text: string }
  | { kind: "p"; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "table"; headers: string[]; rows: string[][] };

const isRow = (t: string) => t.startsWith("|");
const isRule = (t: string) => /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$/.test(t);
const cells = (t: string) => t.replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

export function parseBlocks(text: string): Block[] {
  const lines = text.split("\n").map((l) => l.trim());
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const t = lines[i];
    if (!t) { i++; continue; }
    // A table: a header row, then a |---| rule. Rows may be separated by blank lines.
    if (isRow(t)) {
      let j = i + 1;
      while (j < lines.length && !lines[j]) j++;
      if (j < lines.length && isRule(lines[j])) {
        const headers = cells(t);
        const rows: string[][] = [];
        i = j + 1;
        for (;;) {
          let k = i;
          while (k < lines.length && !lines[k]) k++;
          if (k >= lines.length || !isRow(lines[k])) break;
          rows.push(cells(lines[k]).slice(0, headers.length));
          i = k + 1;
        }
        blocks.push({ kind: "table", headers, rows });
        continue;
      }
    }
    const list = /^[-*]\s+/.test(t) ? "ul" : /^\d+[.)]\s+/.test(t) ? "ol" : null;
    if (list) {
      const marker = list === "ul" ? /^[-*]\s+/ : /^\d+[.)]\s+/;
      const items: string[] = [];
      while (i < lines.length && marker.test(lines[i])) items.push(lines[i++].replace(marker, ""));
      blocks.push({ kind: list, items });
      continue;
    }
    if (/^#{1,6}\s/.test(t)) blocks.push({ kind: "h", text: t.replace(/^#+\s*/, "") });
    else blocks.push({ kind: "p", text: t });
    i++;
  }
  return blocks;
}

// Inline spans: **bold** and `code`; everything else is plain text.
export function parseInline(text: string): { kind: "text" | "bold" | "code"; text: string }[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean).map((part) =>
    part.startsWith("**") && part.endsWith("**") ? { kind: "bold", text: part.slice(2, -2) }
      : part.startsWith("`") && part.endsWith("`") ? { kind: "code", text: part.slice(1, -1) }
      : { kind: "text", text: part });
}
