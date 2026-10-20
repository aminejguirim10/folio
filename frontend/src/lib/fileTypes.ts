export type PreviewKind =
  | "pdf"
  | "image"
  | "markdown"
  | "csv"
  | "json"
  | "html"
  | "text"
  | "none";

const BY_EXTENSION: Record<string, PreviewKind> = {
  ".pdf": "pdf",

  ".png": "image",
  ".jpg": "image",
  ".jpeg": "image",
  ".webp": "image",
  ".gif": "image",
  ".bmp": "image",
  ".tif": "image",
  ".tiff": "image",

  ".md": "markdown",
  ".markdown": "markdown",

  ".csv": "csv",
  ".tsv": "csv",

  ".json": "json",
  ".jsonl": "json",

  ".html": "html",
  ".htm": "html",

  ".txt": "text",

  ".docx": "none",
  ".pptx": "none",
  ".xlsx": "none",
  ".xlsm": "none",
};

export function extensionOf(source: string): string {
  const dot = source.lastIndexOf(".");
  return dot > 0 ? source.slice(dot).toLowerCase() : "";
}

export function previewKindFor(source: string): PreviewKind {
  return BY_EXTENSION[extensionOf(source)] ?? "none";
}

export const UNRENDERABLE_HINT: Record<string, string> = {
  docx: "Word documents have no browser preview. Here is the extracted text, or download the file to open it in Word.",
  pptx: "Slide decks have no browser preview. Here is the extracted text, or download the file to open it in PowerPoint.",
  xlsx: "Spreadsheets have no browser preview. Here is the extracted text, or download the file to open it in Excel.",
  xlsm: "Spreadsheets have no browser preview. Here is the extracted text, or download the file to open it in Excel.",
};