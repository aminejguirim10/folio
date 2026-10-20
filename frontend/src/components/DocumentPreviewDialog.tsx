import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { AnimatePresence, motion } from "framer-motion";
import {
  ChevronDown,
  ChevronUp,
  Download,
  ExternalLink,
  FileSearch,
  FileText,
  Loader2,
  Maximize2,
  Minimize2,
  Search,
  TriangleAlert,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  api,
  type DocumentRecord,
  type PreviewChunk,
} from "../lib/api";
import {
  confidenceIsWorthy,
  iconForDocType,
  shortLabel,
  unitLabel,
} from "../lib/docTypes";
import {
  UNRENDERABLE_HINT,
  extensionOf,
  previewKindFor,
  type PreviewKind,
} from "../lib/fileTypes";
import { renderMarkdown } from "../lib/markdown";
import { cn } from "../lib/utils";
import { Button } from "./ui/button";

const PAGE_SIZE = 60;
const CSV_MAX_ROWS = 400;

type Props = {
  doc: DocumentRecord | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

type Status = "loading" | "loading-more" | "ready" | "error";
type View = "file" | "text";
type Match = { chunkIndex: number; start: number; end: number };

export function DocumentPreviewDialog({ doc, open, onOpenChange }: Props) {
  const [view, setView] = useState<View>("file");
  const [chunks, setChunks] = useState<PreviewChunk[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [status, setStatus] = useState<Status>("loading");
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [fileAvailable, setFileAvailable] = useState<boolean | null>(null);
  const [renderable, setRenderable] = useState<boolean | null>(null);

  const searchRef = useRef<HTMLInputElement>(null);
  const requestId = useRef(0);

  const kind: PreviewKind = doc ? previewKindFor(doc.source) : "none";

  useEffect(() => {
    if (!open || !doc) return;
    let alive = true;
    setFileAvailable(null);
    setRenderable(null);

    const docId = doc.doc_id;
    const kindHere = previewKindFor(doc.source);

    void api.documentFileExists(docId).then(
      (ok) => alive && setFileAvailable(ok),
      () => alive && setFileAvailable(false),
    );

    const needsRender = kindHere === "none" || kindHere === "pdf";
    if (needsRender) {
      void api.documentRenderable(docId).then(
        (ok) => alive && setRenderable(ok),
        () => alive && setRenderable(false),
      );
    } else {
      setRenderable(true);
    }

    return () => {
      alive = false;
    };
  }, [open, doc?.doc_id, doc?.source]);


  useEffect(() => {
    if (!open || !doc) return;
    requestId.current += 1;
    const id = requestId.current;
    setChunks([]);
    setTotal(0);
    setPages(0);
    setHasMore(false);
    setStatus("loading");
    setError(null);
    setQuery("");
    setActive(0);
    setView("file");

    api
      .previewDocument(doc.doc_id, { offset: 0, limit: PAGE_SIZE })
      .then((data) => {
        if (requestId.current !== id) return;
        setChunks(data.items);
        setTotal(data.chunks);
        setPages(data.pages);
        setHasMore(data.has_more);
        setStatus("ready");
      })
      .catch((e: unknown) => {
        if (requestId.current !== id) return;
        setError(e instanceof Error ? e.message : "Could not load this document");
        setStatus("error");
      });
  }, [open, doc?.doc_id]);

  const loadMore = useCallback(() => {
    if (!doc || status !== "ready" || !hasMore) return;
    requestId.current += 1;
    const id = requestId.current;
    const offset = chunks.length;
    setStatus("loading-more");

    api
      .previewDocument(doc.doc_id, { offset, limit: PAGE_SIZE })
      .then((data) => {
        if (requestId.current !== id) return;
        setChunks((prev) => [...prev, ...data.items].sort((a, b) => a.index - b.index));
        setTotal(data.chunks);
        setHasMore(data.has_more);
        setStatus("ready");
      })
      .catch((e: unknown) => {
        if (requestId.current !== id) return;
        setError(e instanceof Error ? e.message : "Could not load more");
        setStatus("error");
      });
  }, [doc, status, hasMore, chunks.length]);

  const needle = query.trim().toLowerCase();

  const matches = useMemo<Match[]>(() => {
    if (needle.length < 2) return [];
    const found: Match[] = [];
    for (const chunk of chunks) {
      const haystack = chunk.text.toLowerCase();
      let from = 0;
      for (;;) {
        const at = haystack.indexOf(needle, from);
        if (at === -1) break;
        found.push({ chunkIndex: chunk.index, start: at, end: at + needle.length });
        from = at + needle.length;
      }
    }
    return found;
  }, [chunks, needle]);

  const matchesByChunk = useMemo(() => {
    const map = new Map<number, Match[]>();
    for (const match of matches) {
      const list = map.get(match.chunkIndex);
      if (list) list.push(match);
      else map.set(match.chunkIndex, [match]);
    }
    return map;
  }, [matches]);

  useEffect(() => {
    if (active > matches.length) setActive(0);
  }, [matches.length, active]);

  const goToMatch = useCallback(
    (direction: 1 | -1) => {
      if (!matches.length) return;
      const next = (active + direction + matches.length) % matches.length;
      const target = matches[next];
      if (!target) return;
      setActive(next);
      document
        .getElementById(`preview-hit-${target.chunkIndex}-${target.start}`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    },
    [active, matches],
  );

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const typing =
        el?.tagName === "INPUT" ||
        el?.tagName === "TEXTAREA" ||
        el?.isContentEditable;
      if (e.key === "/" && !typing) {
        e.preventDefault();
        searchRef.current?.focus();
      }
      if ((e.key === "Enter" || e.key === "F3") && needle && view === "text") {
        e.preventDefault();
        goToMatch(e.shiftKey ? -1 : 1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, needle, view, goToMatch]);

  if (!doc) return null;

  const Icon = iconForDocType(doc.doc_type);
  const badge = shortLabel(doc.doc_type);
  const fileUrl = api.documentFileUrl(doc.doc_id);
  const viewUrl = kind === "pdf" || kind === "none" ? api.documentRenderedUrl(doc.doc_id) : fileUrl;
  const canShowFile = fileAvailable === true && renderable === true;
  const hint =
    kind === "none" && renderable === false
      ? `${formatLabel(doc.doc_type)} files are previewed as extracted text here: this server has no document converter installed. Download the file to open it in ${formatLabel(doc.doc_type)}.`
      : UNRENDERABLE_HINT[extensionOf(doc.source).replace(".", "")];

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className={cn(
            "fixed inset-0 z-50 bg-ink/55 backdrop-blur-md",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
          )}
        />

        <DialogPrimitive.Content
          onOpenAutoFocus={(e) => e.preventDefault()}
          className={cn(
            "fixed z-50 flex flex-col overflow-hidden bg-card",
            "inset-0 h-[100dvh] w-full",
            "sm:inset-auto sm:left-1/2 sm:top-1/2 sm:h-[min(88vh,900px)] sm:w-[min(94vw,1020px)]",
            "sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-2xl",
            "sm:border sm:border-border/80",
            "shadow-[0_40px_120px_-20px_hsl(var(--ink)/0.55)]",
            "duration-200",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
            "data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95",
          )}
        >
          <DialogPrimitive.Title className="sr-only">
            Preview of {doc.source}
          </DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">
            The original file {doc.source}, and the text extracted from it.
          </DialogPrimitive.Description>

          <header className="shrink-0 border-b border-border/70 bg-gradient-to-b from-background/80 to-transparent">
            <div className="flex items-start gap-3 px-4 py-4 sm:gap-4 sm:px-6 sm:py-5">
              <div
                className={cn(
                  "grid shrink-0 place-items-center rounded-xl bg-ink text-paper",
                  "shadow-[0_8px_22px_-8px_hsl(var(--ink)/0.65)]",
                  "h-11 w-11 sm:h-12 sm:w-12",
                )}
              >
                <Icon className="h-5 w-5 sm:h-[22px] sm:w-[22px]" />
              </div>

              <div className="min-w-0 flex-1">
                <h2 className="font-display text-[15px] leading-tight tracking-tight text-ink sm:text-lg">
                  {doc.source}
                </h2>
                <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11px] text-muted-foreground">
                  <span className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[10px] font-medium uppercase tracking-wider text-ink/70">
                    {badge}
                  </span>
                  <Dot />
                  <span className="tabular-nums">
                    {unitLabel(doc.doc_type, pages || doc.pages)}
                  </span>
                  <Dot />
                  <span className="tabular-nums">{total || doc.chunks} chunks</span>
                  <Dot />
                  <span>{formatDate(doc.indexed_at)}</span>
                  {confidenceIsWorthy(doc.doc_type, doc.confidence ?? "") && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 font-medium text-amber-700 dark:text-amber-400">
                      <TriangleAlert className="h-3 w-3" />
                      {doc.confidence === "low" ? "Uncertain read" : "Partly inferred"}
                    </span>
                  )}
                </div>
              </div>

              <DialogPrimitive.Close asChild>
                <Button
                  variant="outline"
                  size="icon"
                  className="h-9 w-9 shrink-0 rounded-lg"
                  aria-label="Close preview"
                >
                  <X className="h-4 w-4" />
                </Button>
              </DialogPrimitive.Close>
            </div>

            <div className="flex flex-wrap items-center gap-2 px-4 pb-4 sm:px-6">
              <div
                role="tablist"
                aria-label="Preview mode"
                className="flex shrink-0 items-center gap-0.5 rounded-lg border border-border/80 bg-background/60 p-0.5"
              >
                <TabButton
                  active={view === "file"}
                  onClick={() => setView("file")}
                  disabled={!canShowFile}
                  title={
                    canShowFile
                      ? undefined
                      : fileAvailable === null || renderable === null
                        ? "Checking whether the file can be previewed…"
                        : !fileAvailable
                          ? "The original file is no longer stored on the server"
                          : kind === "none"
                            ? "This server has no document converter — showing the extracted text"
                            : "This file cannot be previewed"
                  }
                >
                  Document
                </TabButton>
                <TabButton
                  active={view === "text"}
                  onClick={() => setView("text")}
                >
                  Extracted text
                </TabButton>
              </div>

              <div className="ml-auto flex shrink-0 items-center gap-1.5">
                {fileAvailable === true && (
                  <>
                    <Button
                      variant="outline"
                      size="icon"
                      className="h-9 w-9 rounded-lg"
                      asChild
                    >
                      <a
                        href={fileUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        aria-label="Open the original file in a new tab"
                        title="Open in a new tab"
                      >
                        <ExternalLink className="h-4 w-4" />
                      </a>
                    </Button>
                    <Button
                      variant="outline"
                      size="icon"
                      className="h-9 w-9 rounded-lg"
                      asChild
                    >
                      <a
                        href={fileUrl}
                        download={doc.source}
                        aria-label="Download the original file"
                        title="Download"
                      >
                        <Download className="h-4 w-4" />
                      </a>
                    </Button>
                  </>
                )}
              </div>
            </div>

            {view === "text" && (
              <div className="flex items-center gap-2 px-4 pb-4 sm:px-6">
                <div className="relative min-w-0 flex-1">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground/70" />
                  <input
                    ref={searchRef}
                    value={query}
                    onChange={(e) => {
                      setQuery(e.target.value);
                      setActive(0);
                    }}
                    type="search"
                    placeholder="Search the extracted text…  (press / )"
                    aria-label="Search the extracted text"
                    className={cn(
                      "h-10 w-full rounded-lg border border-border/80 bg-background/70 pl-9 pr-3",
                      "text-[13px] text-ink placeholder:text-muted-foreground/70",
                      "transition-colors focus:border-ink/30 focus:outline-none focus:ring-2 focus:ring-ring/25",
                      "[&::-webkit-search-cancel-button]:appearance-none",
                    )}
                  />
                </div>
                <AnimatePresence initial={false}>
                  {matches.length > 0 && (
                    <motion.div
                      initial={{ opacity: 0, scale: 0.94 }}
                      animate={{ opacity: 1, scale: 1 }}
                      exit={{ opacity: 0, scale: 0.94 }}
                      transition={{ duration: 0.16 }}
                      className="flex shrink-0 items-center gap-1"
                    >
                      <span className="min-w-[3.25rem] text-center font-mono text-[11px] tabular-nums text-muted-foreground">
                        {active + 1}/{matches.length}
                      </span>
                      <Button
                        variant="outline"
                        size="icon"
                        className="h-9 w-9 rounded-lg"
                        onClick={() => goToMatch(-1)}
                        aria-label="Previous match"
                      >
                        <ChevronUp className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="outline"
                        size="icon"
                        className="h-9 w-9 rounded-lg"
                        onClick={() => goToMatch(1)}
                        aria-label="Next match"
                      >
                        <ChevronDown className="h-4 w-4" />
                      </Button>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            )}
          </header>

          <div className="min-h-0 flex-1 overflow-hidden">
            {view === "file" ? (
              <FileView
                doc={doc}
                kind={kind}
                viewUrl={viewUrl}
                fileUrl={fileUrl}
                available={fileAvailable}
                renderable={renderable}
                hint={hint}
              />
            ) : status === "loading" ? (
              <PreviewSkeleton />
            ) : status === "error" && chunks.length === 0 ? (
              <EmptyState
                icon={TriangleAlert}
                title="Preview unavailable"
                body={error ?? "Something went wrong."}
                action={
                  <Button variant="outline" size="sm" onClick={() => setView("file")}>
                    Back to the document
                  </Button>
                }
              />
            ) : chunks.length === 0 ? (
              <EmptyState
                icon={FileSearch}
                title="No extracted text"
                body="Nothing readable was indexed from this document."
              />
            ) : (
              <SectionedBody
                chunks={chunks}
                matchesByChunk={matchesByChunk}
                activeMatch={
                  matches[active]
                    ? `${matches[active].chunkIndex}-${matches[active].start}`
                    : null
                }
              />
            )}
          </div>

          {view === "text" && chunks.length > 0 && (
            <footer className="shrink-0 border-t border-border/70 bg-background/60 px-4 py-3 sm:px-6">
              <div className="flex items-center justify-between gap-3">
                <p className="text-[11px] tabular-nums text-muted-foreground">
                  Showing <span className="font-medium text-ink">{chunks.length}</span> of{" "}
                  <span className="font-medium text-ink">{total || doc.chunks}</span> chunks
                </p>
                {hasMore && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8 rounded-lg text-[12px]"
                    onClick={loadMore}
                    disabled={status === "loading-more"}
                  >
                    {status === "loading-more" ? (
                      <>
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        Loading
                      </>
                    ) : (
                      "Load more"
                    )}
                  </Button>
                )}
              </div>
            </footer>
          )}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}


function TabButton({
  active,
  onClick,
  disabled,
  title,
  children,
}: {
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      disabled={disabled}
      title={title}
      onClick={onClick}
      className={cn(
        "rounded-[6px] px-3 py-1.5 text-[12px] font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
        active
          ? "bg-ink text-paper shadow-sm"
          : "text-muted-foreground hover:bg-accent hover:text-ink",
        disabled && "cursor-not-allowed opacity-40 hover:bg-transparent",
      )}
    >
      {children}
    </button>
  );
}

function FileView({
  doc,
  kind,
  viewUrl,
  fileUrl,
  available,
  renderable,
  hint,
}: {
  doc: DocumentRecord;
  kind: PreviewKind;
  viewUrl: string;
  fileUrl: string;
  available: boolean | null;
  renderable: boolean | null;
  hint?: string;
}) {
  if (available === null || renderable === null) {
    return (
      <div className="grid h-full place-items-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const canView = available && renderable;

  if (!canView) {
    const noConverter = kind === "none" && available && !renderable;
    return (
      <EmptyState
        icon={kind === "none" ? FileText : FileSearch}
        title={
          kind === "none"
            ? noConverter
              ? "Showing the extracted text"
              : "No browser preview for this format"
            : "The original file is no longer stored"
        }
        body={
          hint ??
          "The uploaded file was removed from the server. The extracted text is still available in the other tab."
        }
        action={
          available ? (
            <Button variant="outline" size="sm" asChild>
              <a href={fileUrl} download={doc.source}>
                <Download className="h-3.5 w-3.5" />
                Download the file
              </a>
            </Button>
          ) : undefined
        }
      />
    );
  }

  if (kind === "pdf" || kind === "none") {
    return <PdfView fileUrl={viewUrl} title={doc.source} />;
  }
  if (kind === "image") return <ImageView fileUrl={viewUrl} title={doc.source} />;
  return <TextView doc={doc} kind={kind} />;
}

function formatLabel(docType?: string): string {
  const key = (docType ?? "").toLowerCase();
  const names: Record<string, string> = {
    docx: "Word",
    pptx: "PowerPoint",
    xlsx: "Excel",
    xlsm: "Excel",
  };
  return names[key] ?? "Office";
}

function PdfView({ fileUrl, title }: { fileUrl: string; title: string }) {
  return (
    <iframe
      src={`${fileUrl}#view=FitH`}
      title={`Preview of ${title}`}
      className="h-full w-full border-0 bg-muted/40"
    />
  );
}

function ImageView({ fileUrl, title }: { fileUrl: string; title: string }) {
  const [zoom, setZoom] = useState<"fit" | number>("fit");
  const [failed, setFailed] = useState(false);

  if (failed) {
    return (
      <EmptyState
        icon={TriangleAlert}
        title="This image could not be displayed"
        body="The file is stored on the server but the browser refused to render it. It may be in a format the browser does not support."
      />
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center justify-end gap-1 border-b border-border/60 px-4 py-2">
        <Button
          variant="outline"
          size="icon"
          className="h-8 w-8 rounded-lg"
          onClick={() => setZoom((z) => Math.max(0.1, (typeof z === "number" ? z : 1) - 0.25))}
          aria-label="Zoom out"
        >
          <ZoomOut className="h-3.5 w-3.5" />
        </Button>
        <span className="min-w-[3.5rem] text-center font-mono text-[11px] tabular-nums text-muted-foreground">
          {zoom === "fit" ? "Fit" : `${Math.round(zoom * 100)}%`}
        </span>
        <Button
          variant="outline"
          size="icon"
          className="h-8 w-8 rounded-lg"
          onClick={() => setZoom((z) => Math.min(5, (typeof z === "number" ? z : 1) + 0.25))}
          aria-label="Zoom in"
        >
          <ZoomIn className="h-3.5 w-3.5" />
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="ml-1 h-8 rounded-lg text-[12px]"
          onClick={() => setZoom("fit")}
        >
          <Maximize2 className="h-3.5 w-3.5" />
          Fit
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-8 rounded-lg text-[12px]"
          onClick={() => setZoom(1)}
        >
          <Minimize2 className="h-3.5 w-3.5" />
          100%
        </Button>
      </div>

      <div
        className="min-h-0 flex-1 overflow-auto p-4"
        style={{
          backgroundImage:
            "linear-gradient(45deg, hsl(var(--ink)/0.04) 25%, transparent 25%), linear-gradient(-45deg, hsl(var(--ink)/0.04) 25%, transparent 25%), linear-gradient(45deg, transparent 75%, hsl(var(--ink)/0.04) 75%), linear-gradient(-45deg, transparent 75%, hsl(var(--ink)/0.04) 75%)",
          backgroundSize: "16px 16px",
          backgroundPosition: "0 0, 0 8px, 8px -8px, -8px 0",
        }}
      >
        <img
          src={fileUrl}
          alt={title}
          onError={() => setFailed(true)}
          style={{ width: zoom === "fit" ? undefined : `${zoom * 100}%` }}
          className="mx-auto rounded-lg shadow-[0_8px_30px_-12px_hsl(var(--ink)/0.4)]"
        />
      </div>
    </div>
  );
}

function TextView({
  doc,
  kind,
}: {
  doc: DocumentRecord;
  kind: PreviewKind;
}) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setText(null);
    setError(null);
    api
      .documentFileText(doc.doc_id)
      .then((value) => {
        if (alive) setText(value);
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : "Could not read this file");
      });
    return () => {
      alive = false;
    };
  }, [doc.doc_id]);

  if (error) {
    return (
      <EmptyState
        icon={TriangleAlert}
        title="Could not read this file"
        body={error}
      />
    );
  }
  if (text === null) {
    return (
      <div className="grid h-full place-items-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (kind === "html") {
    return (
      <iframe
        srcDoc={text}
        title={`Preview of ${doc.source}`}
        sandbox=""
        referrerPolicy="no-referrer"
        className="h-full w-full border-0 bg-white"
      />
    );
  }

  if (kind === "csv") {
    const rows = parseDelimited(text, doc.source.endsWith(".tsv") ? "\t" : ",");
    return <CsvTable rows={rows} title={doc.source} />;
  }

  if (kind === "json") return <JsonView text={text} />;
  if (kind === "markdown") {
    return (
      <ScrollFrame>
        <article
          className="prose-doc max-w-none text-[15px]"
          dangerouslySetInnerHTML={{ __html: renderMarkdown(text) }}
        />
      </ScrollFrame>
    );
  }

  return (
    <ScrollFrame>
      <pre className="whitespace-pre-wrap break-words font-mono text-[13px] leading-[1.7] text-ink/90">
        {text}
      </pre>
    </ScrollFrame>
  );
}

function ScrollFrame({ children }: { children: React.ReactNode }) {
  return (
    <div className="h-full overflow-y-auto overscroll-contain">
      <div className="mx-auto w-full max-w-3xl px-4 py-5 sm:px-8 sm:py-7">{children}</div>
    </div>
  );
}

function parseDelimited(text: string, delimiter: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;

  for (let i = 0; i < text.length; i += 1) {
    const char = text[i]!;
    if (quoted) {
      if (char === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 1;
        } else {
          quoted = false;
        }
      } else {
        field += char;
      }
      continue;
    }
    if (char === '"' && field === "") quoted = true;
    else if (char === delimiter) {
      row.push(field);
      field = "";
    } else if (char === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else if (char !== "\r") {
      field += char;
    }
  }
  if (field !== "" || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

function CsvTable({ rows, title }: { rows: string[][]; title: string }) {
  if (!rows.length) {
    return <EmptyState icon={FileText} title="Empty file" body="This file has no rows." />;
  }

  const header = rows[0]!;
  const body = rows.slice(1, CSV_MAX_ROWS);
  const truncated = rows.length - 1 > CSV_MAX_ROWS;
  const width = Math.max(...rows.slice(0, 50).map((r) => r.length), 1);

  return (
    <div className="h-full overflow-auto">
      <table className="w-full border-collapse text-left text-[13px]">
        <thead className="sticky top-0 z-10 bg-card/95 backdrop-blur">
          <tr>
            <th className="w-10 border-b border-border/80 px-3 py-2 font-mono text-[10px] font-normal text-muted-foreground">
              #
            </th>
            {Array.from({ length: width }, (_, i) => (
              <th
                key={i}
                className="border-b border-border/80 px-3 py-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink/80"
              >
                {header[i] || <span className="text-muted-foreground/50">—</span>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, rowIndex) => (
            <tr
              key={rowIndex}
              className="transition-colors hover:bg-ember/[0.04]"
            >
              <td className="border-b border-border/40 px-3 py-1.5 font-mono text-[10px] tabular-nums text-muted-foreground/70">
                {rowIndex + 2}
              </td>
              {Array.from({ length: width }, (_, i) => (
                <td
                  key={i}
                  className="max-w-[22rem] truncate border-b border-border/40 px-3 py-1.5 text-ink/90"
                  title={row[i] ?? ""}
                >
                  {row[i] ?? ""}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {truncated && (
        <p className="px-4 py-4 text-center text-[12px] text-muted-foreground">
          Showing the first {CSV_MAX_ROWS} rows of {rows.length - 1} in{" "}
          {title}. Download the file for the rest.
        </p>
      )}
    </div>
  );
}

function JsonView({ text }: { text: string }) {
  let body = text;
  try {
    body = JSON.stringify(JSON.parse(text), null, 2);
  } catch {
   
  }
  return (
    <ScrollFrame>
      <pre className="whitespace-pre-wrap break-words font-mono text-[12.5px] leading-[1.7] text-ink/90">
        {body}
      </pre>
    </ScrollFrame>
  );
}

function ChunkBody({
  chunk,
  matches,
  activeMatch,
}: {
  chunk: PreviewChunk;
  matches: Match[] | undefined;
  activeMatch: string | null;
}) {
  const { text } = chunk;

  if (!matches?.length) {
    return (
      <p className="whitespace-pre-wrap break-words font-serif text-[14.5px] leading-[1.75] text-ink/90 sm:text-[15px]">
        {text}
      </p>
    );
  }

  const nodes: React.ReactNode[] = [];
  let cursor = 0;
  for (const match of matches) {
    if (match.start > cursor) nodes.push(text.slice(cursor, match.start));
    const key = `${match.chunkIndex}-${match.start}`;
    nodes.push(
      <mark
        key={key}
        id={`preview-hit-${key}`}
        className={cn(
          "rounded-[3px] px-0.5 text-inherit",
          key === activeMatch
            ? "bg-ember/45 shadow-[0_0_0_2px_hsl(var(--ember)/0.3)]"
            : "bg-ember/20",
        )}
      >
        {text.slice(match.start, match.end)}
      </mark>,
    );
    cursor = match.end;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));

  return (
    <p className="whitespace-pre-wrap break-words font-serif text-[14.5px] leading-[1.75] text-ink/90 sm:text-[15px]">
      {nodes}
    </p>
  );
}

function SectionedBody({
  chunks,
  matchesByChunk,
  activeMatch,
}: {
  chunks: PreviewChunk[];
  matchesByChunk: Map<number, Match[]>;
  activeMatch: string | null;
}) {
  const sections = useMemo(() => {
    const out: { page: number; locator: string; items: PreviewChunk[] }[] = [];
    for (const chunk of chunks) {
      const last = out[out.length - 1];
      if (last && last.page === chunk.page) last.items.push(chunk);
      else out.push({ page: chunk.page, locator: chunk.locator, items: [chunk] });
    }
    return out;
  }, [chunks]);

  return (
    <div className="h-full overflow-y-auto overscroll-contain">
      <div className="mx-auto w-full max-w-3xl px-4 py-5 sm:px-8 sm:py-7">
        {sections.map((section) => (
          <section key={`${section.page}-${section.locator}`} className="mb-7 last:mb-0">
            <div className="sticky top-0 z-10 -mx-1 mb-3 flex items-center gap-2.5 bg-card/92 px-1 py-2 backdrop-blur-sm">
              <span className="grid h-6 min-w-[1.75rem] place-items-center rounded-md bg-ink px-1.5 font-mono text-[11px] font-medium tabular-nums text-paper">
                {section.page > 0 ? section.page : "·"}
              </span>
              {section.locator ? (
                <span className="truncate text-[11px] uppercase tracking-[0.12em] text-muted-foreground">
                  {section.locator}
                </span>
              ) : (
                <span className="h-px flex-1 bg-border/70" />
              )}
            </div>

            <div className="space-y-4">
              {section.items.map((chunk) => (
                <ChunkBody
                  key={chunk.index}
                  chunk={chunk}
                  matches={matchesByChunk.get(chunk.index)}
                  activeMatch={activeMatch}
                />
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

function PreviewSkeleton() {
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-8">
        <div className="mb-5 h-5 w-24 animate-pulse rounded-md bg-muted" />
        <div className="space-y-3">
          {[100, 96, 92, 70, 98, 88].map((w, i) => (
            <div
              key={i}
              style={{ width: `${w}%` }}
              className="h-3 animate-pulse rounded-full bg-muted"
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function Dot() {
  return <span aria-hidden className="h-0.5 w-0.5 rounded-full bg-muted-foreground/50" />;
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function EmptyState({
  icon: Icon,
  title,
  body,
  action,
}: {
  icon: typeof TriangleAlert;
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="grid h-full place-items-center px-6 py-10 text-center">
      <div className="max-w-sm">
        <div className="mx-auto grid h-11 w-11 place-items-center rounded-xl bg-muted text-muted-foreground">
          <Icon className="h-5 w-5" />
        </div>
        <h3 className="mt-4 font-display text-base text-ink">{title}</h3>
        <p className="mt-1.5 text-[13px] leading-relaxed text-muted-foreground">{body}</p>
        {action && <div className="mt-5 flex justify-center">{action}</div>}
      </div>
    </div>
  );
}