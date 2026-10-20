import { useCallback, useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Eye,
  Library,
  Loader2,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  TriangleAlert,
  UploadCloud,
} from "lucide-react";
import { api, type DocumentRecord } from "../lib/api";
import {
  acceptAttribute,
  confidenceIsWorthy,
  iconForDocType,
  unitLabel,
} from "../lib/docTypes";
import { cn } from "../lib/utils";
import { Progress } from "./ui/progress";
import { ScrollArea } from "./ui/scroll-area";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "./ui/sheet";
import { DeleteDocumentButton } from "./DeleteDocumentButton";
import { DocumentPreviewDialog } from "./DocumentPreviewDialog";
import { Button } from "./ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "./ui/tooltip";

const FALLBACK_EXTENSIONS = [
  ".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".tsv", ".txt", ".md", ".html", ".json",
  ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff",
];

const COLLAPSE_KEY = "folio:sidebar-collapsed";

const SIDEBAR_WIDTH = 320;
const RAIL_WIDTH = 68;

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

type Props = {
  documents: DocumentRecord[];
  onChanged: () => void;
};

export function DocumentPanel({ documents, onChanged }: Props) {
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<boolean>(readCollapsed);
  const [previewing, setPreviewing] = useState<DocumentRecord | null>(null);
  const [extensions, setExtensions] = useState<string[]>(FALLBACK_EXTENSIONS);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let alive = true;
    api
      .limits()
      .then((limits) => {
        if (alive && limits.extensions?.length) setExtensions(limits.extensions);
      })
      .catch(() => {
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
    } catch {
      
    }
  }, [collapsed]);

  const upload = useCallback(
    async (file: File) => {
      const ext = `.${file.name.split(".").pop()?.toLowerCase() ?? ""}`;
      if (!extensions.includes(ext)) {
        setError(
          `"${file.name}" is not supported. Accepted: ${extensions.join(" ")}`,
        );
        return;
      }
      setBusy(true);
      setError(null);
      setProgress(0);
      try {
        await api.upload(file, setProgress);
        onChanged();
        setMobileOpen(false);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Upload failed");
      } finally {
        setBusy(false);
        setProgress(0);
        if (inputRef.current) inputRef.current.value = "";
      }
    },
    [onChanged, extensions],
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      const file = e.dataTransfer.files?.[0];
      if (file) void upload(file);
    },
    [upload],
  );

  const openPreview = useCallback((doc: DocumentRecord) => {
    setPreviewing(doc);
    setMobileOpen(false);
  }, []);

  const brand = (
    <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-ink text-paper shadow-[0_6px_18px_-6px_hsl(var(--ink)/0.6)]">
      <svg
        width={18}
        height={18}
        viewBox="0 0 24 24"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
      >
        <g
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinejoin="round"
        >
          <rect x="4.5" y="5.25" width="9.75" height="12" rx="1.95" opacity="0.55" />
          <rect x="9" y="7.5" width="9.75" height="12" rx="1.95" />
          <path d="M11.6 12h5.25M11.6 15h3.4" strokeLinecap="round" />
        </g>
      </svg>
    </div>
  );

  const errorBanner = error && (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      exit={{ opacity: 0, height: 0 }}
      className="mx-4 mb-2 overflow-hidden rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive"
    >
      {error}
    </motion.div>
  );

  const rail = (
    <>
      <div className="flex shrink-0 flex-col items-center gap-2.5 border-b border-border/40 px-2 py-4">
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          aria-label="Expand library"
          className="rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
        >
          {brand}
        </button>
        <span
          className="font-mono text-[10px] tabular-nums text-muted-foreground"
          aria-label={`${documents.length} documents`}
        >
          {String(documents.length).padStart(2, "0")}
        </span>
      </div>

      <div className="shrink-0 px-2 py-3">
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              aria-label="Upload a document"
              className="grid h-10 w-full place-items-center rounded-lg border-2 border-dashed border-border/80 text-muted-foreground transition-colors hover:border-ink/40 hover:bg-background/60 hover:text-ember focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
            >
              {busy ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <UploadCloud className="h-4 w-4" />
              )}
            </button>
          </TooltipTrigger>
          <TooltipContent side="right" sideOffset={10}>
            Upload a document
          </TooltipContent>
        </Tooltip>
      </div>

      <AnimatePresence>{error && errorBanner}</AnimatePresence>

      <ScrollArea className="min-h-0 flex-1">
        <ul className="space-y-1 px-2 pb-4">
          <AnimatePresence initial={false}>
            {documents.map((doc) => {
              const Icon = iconForDocType(doc.doc_type);
              return (
                <motion.li
                  key={doc.doc_id}
                  layout
                  initial={{ opacity: 0, scale: 0.9 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.9 }}
                  transition={{ duration: 0.16 }}
                >
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        onClick={() => openPreview(doc)}
                        aria-label={`Preview ${doc.source}`}
                        className="grid h-9 w-full place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-card hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                      >
                        <Icon className="h-4 w-4" />
                      </button>
                    </TooltipTrigger>
                    <TooltipContent side="right" sideOffset={10}>
                      {doc.source}
                    </TooltipContent>
                  </Tooltip>
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ul>
      </ScrollArea>

      <div className="shrink-0 border-t border-border/40 px-2 py-3">
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={() => setCollapsed(false)}
              aria-label="Expand library"
              className="grid h-9 w-full place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-card hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
            >
              <PanelLeftOpen className="h-4 w-4" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="right" sideOffset={10}>
            Expand library
          </TooltipContent>
        </Tooltip>
      </div>
    </>
  );

  const expanded = (
    <>
      <div className="flex shrink-0 items-center gap-3 border-b border-border/40 px-4 py-5">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          {brand}
          <div className="min-w-0 flex-1">
            <h1 className="font-display text-[15px] leading-tight tracking-tight text-ink">
              Folio
            </h1>
            <p className="truncate text-[11px] leading-tight text-muted-foreground">
              Grounded document research
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          aria-label="Collapse library"
          aria-expanded={!collapsed}
          className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-card hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
        >
          <PanelLeftClose className="h-4 w-4" />
        </button>
      </div>

      <div className="flex shrink-0 items-center justify-between px-5 pb-3 pt-4">
        <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
          <Library className="h-3.5 w-3.5" />
          Library
        </div>
        <span className="font-mono text-xs tabular-nums text-muted-foreground">
          {String(documents.length).padStart(2, "0")}
        </span>
      </div>

      <div className="shrink-0 px-4 pb-4">
        <motion.div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
          }}
          whileHover={{ scale: 1.005 }}
          whileTap={{ scale: 0.995 }}
          transition={{ type: "spring", stiffness: 400, damping: 30 }}
          className={cn(
            "group relative cursor-pointer overflow-hidden rounded-xl border-2 border-dashed",
            "px-4 py-7 text-center transition-colors",
            dragging
              ? "border-ember/60 bg-ember/10"
              : "border-border/80 bg-background/40 hover:border-ink/40 hover:bg-background/80",
          )}
        >
          <div className="pointer-events-none absolute inset-0 opacity-0 transition-opacity bg-[radial-gradient(circle_at_center,hsl(var(--ember)/0.08),transparent_60%)] group-hover:opacity-100" />
          <AnimatePresence mode="wait">
            {busy ? (
              <motion.div
                key="busy"
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                className="space-y-3"
              >
                <div className="mx-auto grid h-9 w-9 place-items-center rounded-full bg-ember/10 text-ember">
                  <Loader2 className="h-4 w-4 animate-spin" />
                </div>
                <div className="space-y-1.5">
                  <p className="text-sm font-medium text-ink">
                    Indexing… {progress}%
                  </p>
                  <Progress value={progress} className="mx-auto w-3/4" />
                </div>
              </motion.div>
            ) : (
              <motion.div
                key="idle"
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                className="space-y-2"
              >
                <div className="mx-auto grid h-9 w-9 place-items-center rounded-full bg-ink/5 text-ink transition-colors group-hover:bg-ember/10 group-hover:text-ember">
                  <UploadCloud className="h-4 w-4" />
                </div>
                <p className="text-sm font-medium text-ink">Drop a file</p>
                <p className="text-[11px] leading-snug text-muted-foreground">
                  PDF · Word · slides · sheets · data · images
                </p>
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>
      </div>

      <AnimatePresence>{error && errorBanner}</AnimatePresence>

      <ScrollArea className="min-h-0 flex-1">
        <ul className="space-y-1 px-2 pb-8">
          <AnimatePresence initial={false}>
            {documents.map((doc) => (
              <DocumentRow
                key={doc.doc_id}
                doc={doc}
                onDeleted={onChanged}
                onPreview={() => openPreview(doc)}
              />
            ))}
          </AnimatePresence>

          {documents.length === 0 && (
            <motion.li
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="grid place-items-center px-2 py-10 text-center"
            >
              <p className="text-[13px] text-muted-foreground">
                No documents yet.
              </p>
              <p className="mt-1 text-[11px] text-muted-foreground/70">
                Upload a document to begin.
              </p>
            </motion.li>
          )}
        </ul>
      </ScrollArea>
    </>
  );

  const renderPanel = (isCollapsed: boolean) => (
    <TooltipProvider delayDuration={250} skipDelayDuration={0}>
      <aside
        className={cn(
          "flex h-full w-full flex-col overflow-hidden",
          "border-r border-border/70 bg-card/40",
        )}
      >
        <input
          ref={inputRef}
          type="file"
          accept={acceptAttribute(extensions)}
          className="sr-only"
          tabIndex={-1}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void upload(file);
          }}
        />
        {isCollapsed ? rail : expanded}
      </aside>
    </TooltipProvider>
  );

  return (
    <>
      <div className="fixed left-3 top-3 z-40 lg:hidden">
        <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
          <SheetTrigger asChild>
            <Button
              variant="outline"
              size="icon"
              className="h-9 w-9 bg-background/80 shadow-sm backdrop-blur"
              aria-label="Open library"
            >
              <Menu className="h-4 w-4" />
            </Button>
          </SheetTrigger>
          <SheetContent
            side="left"
            className="w-[85vw] max-w-[340px] gap-0 overflow-hidden p-0"
          >
            <SheetTitle className="sr-only">Document library</SheetTitle>
            {renderPanel(false)}
          </SheetContent>
        </Sheet>
      </div>

      <motion.div
        initial={false}
        animate={{ width: collapsed ? RAIL_WIDTH : SIDEBAR_WIDTH }}
        transition={{ duration: 0.26, ease: [0.22, 1, 0.36, 1] }}
        className="hidden shrink-0 overflow-hidden lg:block"
      >
        {renderPanel(collapsed)}
      </motion.div>

      <DocumentPreviewDialog
        doc={previewing}
        open={previewing !== null}
        onOpenChange={(next) => {
          if (!next) setPreviewing(null);
        }}
      />
    </>
  );
}

function shortName(name: string, max = 26): string {
  if (name.length <= max) return name;
  const dot = name.lastIndexOf(".");
  const ext = dot > 0 && name.length - dot <= 6 ? name.slice(dot) : "";
  const stem = ext ? name.slice(0, dot) : name;
  const keep = Math.max(6, max - ext.length - 1);
  const head = stem.slice(0, Math.ceil(keep * 0.7));
  const tail = stem.slice(-Math.floor(keep * 0.3));
  return `${head}…${tail}${ext}`;
}

function DocumentRow({
  doc,
  onDeleted,
  onPreview,
}: {
  doc: DocumentRecord;
  onDeleted: () => void;
  onPreview: () => void;
}) {
  const Icon = iconForDocType(doc.doc_type);

  return (
    <motion.li
      layout
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 8 }}
      transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
      role="button"
      tabIndex={0}
      aria-label={`Preview ${doc.source}`}
      onClick={onPreview}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onPreview();
        }
      }}
      className={cn(
        "group grid cursor-pointer grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2.5",
        "rounded-lg border border-transparent px-2.5 py-2",
        "transition-colors hover:border-border/80 hover:bg-card",
        "focus-visible:border-ink/30 focus-visible:bg-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
      )}
    >
      <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />

      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-1.5">
          <p
            className="truncate text-[13px] font-medium leading-tight text-ink"
            title={doc.source}
          >
            {shortName(doc.source)}
          </p>
          {confidenceIsWorthy(doc.doc_type, doc.confidence ?? "") && (
            <Tooltip>
              <TooltipTrigger asChild>
                <span
                  className={cn(
                    "grid h-3.5 w-3.5 shrink-0 place-items-center rounded-full",
                    doc.confidence === "low"
                      ? "bg-destructive/15 text-destructive"
                      : "bg-amber-500/20 text-amber-700",
                  )}
                  aria-label={
                    doc.confidence === "low"
                      ? "Transcription uncertain"
                      : "Partially uncertain transcription"
                  }
                >
                  <TriangleAlert className="h-2.5 w-2.5" />
                </span>
              </TooltipTrigger>
              <TooltipContent side="top" sideOffset={8}>
                <p className="font-medium">
                  {doc.confidence === "low"
                    ? "This image was hard to read"
                    : "Some words were inferred"}
                </p>
                <p className="text-[10px] opacity-70">
                  {doc.notes?.trim() ||
                    "Answers citing this image may not be reliable."}
                </p>
              </TooltipContent>
            </Tooltip>
          )}
        </div>
        <p className="mt-0.5 truncate font-mono text-[10px] leading-tight tabular-nums text-muted-foreground">
          {unitLabel(doc.doc_type, doc.pages)} · {doc.chunks} chunks
        </p>
      </div>

      <div
        className="flex shrink-0 items-center gap-0.5"
        onKeyDown={(e) => e.stopPropagation()}
      >
        <span
          aria-hidden
          className="grid h-7 w-7 place-items-center rounded-md text-muted-foreground/0 transition-colors group-hover:text-muted-foreground/60 group-focus-visible:text-muted-foreground/60"
        >
          <Eye className="h-3.5 w-3.5" />
        </span>
        <DeleteDocumentButton
          docId={doc.doc_id}
          source={doc.source}
          onDeleted={onDeleted}
        />
      </div>
    </motion.li>
  );
}