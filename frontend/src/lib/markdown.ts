const escapeHtml = (raw: string): string =>
  raw
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

export const citationId = (scope: string, n: number): string => `cite-${scope}-${n}`;

const inline = (text: string, scope: string): string =>
  escapeHtml(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, "<em>$1</em>")
    // [3] or [2, 5] -> clickable citation chip. Multi-citation tags link to
    // the first one listed; the rest are still visible in the label.
    .replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, (_m, group: string) => {
      const ids = group.split(",").map((n) => n.trim());
      return `<a class="cite" href="#${citationId(scope, Number(ids[0]))}" title="Jump to source ${group}">${group}</a>`;
    });

export function renderMarkdown(source: string, scope = "0"): string {
  const lines = source.split("\n");
  const out: string[] = [];
  let listType: "ul" | "ol" | null = null;
  let inParagraph = false;

  const closeList = () => {
    if (listType) {
      out.push(`</${listType}>`);
      listType = null;
    }
  };
  const closeParagraph = () => {
    if (inParagraph) {
      out.push("</p>");
      inParagraph = false;
    }
  };

  for (const line of lines) {
    const trimmed = line.trim();

    if (!trimmed) {
      closeList();
      closeParagraph();
      continue;
    }

    const heading = trimmed.match(/^(#{1,4})\s+(.*)$/);
    if (heading?.[2] !== undefined) {
      closeList();
      closeParagraph();
      const level = Math.min(heading[1]!.length + 1, 4);
      out.push(`<h${level}>${inline(heading[2], scope)}</h${level}>`);
      continue;
    }

    const unordered = trimmed.match(/^[-*+]\s+(.*)$/);
    if (unordered?.[1] !== undefined) {
      closeParagraph();
      if (listType !== "ul") {
        closeList();
        out.push("<ul>");
        listType = "ul";
      }
      out.push(`<li>${inline(unordered[1], scope)}</li>`);
      continue;
    }

    const ordered = trimmed.match(/^\d+[.)]\s+(.*)$/);
    if (ordered?.[1] !== undefined) {
      closeParagraph();
      if (listType !== "ol") {
        closeList();
        out.push("<ol>");
        listType = "ol";
      }
      out.push(`<li>${inline(ordered[1], scope)}</li>`);
      continue;
    }

    closeList();
    if (!inParagraph) {
      out.push("<p>");
      inParagraph = true;
    }
    out.push(`${inline(trimmed, scope)}`);
    // Use a space instead of <br/> â€” paragraphs flow more naturally.
  }

  closeList();
  closeParagraph();
  return out.join("\n");
}