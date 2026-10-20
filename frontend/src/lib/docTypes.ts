import {
  FileSpreadsheet,
  FileText,
  FileType2,
  Presentation,
  Table2,
  Braces,
  FileCode2,
  ImageIcon,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

const DOC_ICONS: Record<string, LucideIcon> = {
  pdf: FileText,
  docx: FileType2,
  pptx: Presentation,
  xlsx: FileSpreadsheet,
  xlsm: FileSpreadsheet,
  csv: Table2,
  tsv: Table2,
  txt: FileText,
  md: FileText,
  markdown: FileText,
  html: FileCode2,
  htm: FileCode2,
  json: Braces,
  jsonl: Braces,
  image: ImageIcon,
  png: ImageIcon,
  jpg: ImageIcon,
  jpeg: ImageIcon,
  webp: ImageIcon,
  gif: ImageIcon,
  bmp: ImageIcon,
  tiff: ImageIcon,
};

const FALLBACK_ICON = FileText;

export function iconForDocType(docType?: string): LucideIcon {
  if (!docType) return FALLBACK_ICON;
  return DOC_ICONS[docType.toLowerCase()] ?? FALLBACK_ICON;
}

export function shortLabel(docType?: string): string {
  if (!docType) return "FILE";
  const known: Record<string, string> = { jsonl: "JSON", markdown: "MD" };
  return known[docType.toLowerCase()] ?? docType.toUpperCase().slice(0, 5);
}

export function acceptAttribute(extensions: string[]): string {
  if (!extensions.length) return "";
  const mimeByExt: Record<string, string> = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".html": "text/html",
    ".htm": "text/html",
  };
  return extensions
    .map((ext) => [ext, mimeByExt[ext.toLowerCase()]].filter(Boolean).join(","))
    .join(",");
}

const UNIT_WORDS: Record<string, string> = {
  pdf: "page",
  docx: "block",
  pptx: "slide",
  xlsx: "section",
  xlsm: "section",
  csv: "section",
  tsv: "section",
  txt: "section",
  md: "section",
  markdown: "section",
  html: "section",
  htm: "section",
  json: "section",
  jsonl: "section",
};

export function unitLabel(docType: string | undefined, count: number): string {
  const word = UNIT_WORDS[(docType ?? "pdf").toLowerCase()] ?? "section";
  return `${count} ${word}${count > 1 ? "s" : ""}`;
}


export function isTranscribed(docType: string | undefined): boolean {
  return (docType ?? "").toLowerCase() === "image";
}

export type Confidence = "" | "high" | "medium" | "low";

export function confidenceIsWorthy(docType: string | undefined, confidence: Confidence): boolean {
  return isTranscribed(docType) && (confidence === "low" || confidence === "medium");
}
