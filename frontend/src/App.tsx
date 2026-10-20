import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronDown,
  CircleAlert,
  Filter,
  Plus,
  Sparkles,
  Square,
  Wand2,
  X,
} from "lucide-react";
import { DocumentPanel } from "./components/DocumentPanel";
import { MessageBubble } from "./components/MessageBubble";
import { api, type Citation, type DocumentRecord, type Message } from "./lib/api";
import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { ScrollArea } from "./components/ui/scroll-area";
import { Separator } from "./components/ui/separator";
import { Textarea } from "./components/ui/textarea";
import { cn } from "./lib/utils";

const SUGGESTIONS = [
  "What is this document about?",
  "Summarise the key points.",
  "What are the main dates and figures?",
];

const SCOPE_PANEL_W = 320;
type Turn = { id: string; controller: AbortController };

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [providerOk, setProviderOk] = useState<boolean | null>(null);
  const [selectedDocs, setSelectedDocs] = useState<Set<string> | null>(null);

  const activeRef = useRef<Turn | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const [atBottom, setAtBottom] = useState(true);
  const [canScroll, setCanScroll] = useState(false);
  const atBottomRef = useRef(true);
  const interactionIdRef = useRef<string | null>(null);

  const refreshDocuments = useCallback(() => {
    api
      .documents()
      .then(setDocuments)
      .catch(() => setDocuments([]));
  }, []);

  useEffect(() => {
    refreshDocuments();
    api
      .health()
      .then((h: { provider_configured: boolean }) => setProviderOk(h.provider_configured))
      .catch(() => setProviderOk(false));
  }, [refreshDocuments]);

  useEffect(() => {
    const vp = viewportRef.current;
    if (!vp) return;

    const measure = () => {
      const distance = vp.scrollHeight - vp.scrollTop - vp.clientHeight;
      const bottom = distance <= 80;
      atBottomRef.current = bottom;
      setAtBottom(bottom);
      setCanScroll(vp.scrollHeight - vp.clientHeight > 24);
    };

    measure();
    vp.addEventListener("scroll", measure, { passive: true });
    const ro = new ResizeObserver(measure);
    ro.observe(vp);
    const content = vp.firstElementChild;
    if (content) ro.observe(content);
    return () => {
      vp.removeEventListener("scroll", measure);
      ro.disconnect();
    };
  }, []);

  const jumpToEdge = useCallback((toTop: boolean) => {
    const vp = viewportRef.current;
    if (!vp) return;
    vp.scrollTo({
      top: toTop ? 0 : vp.scrollHeight,
      behavior: "smooth",
    });
  }, []);

  useEffect(() => {
    const el = endRef.current;
    if (!el) return;
    if (!atBottomRef.current) return;
    el.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  useEffect(() => () => activeRef.current?.controller.abort(), []);

  const push = useCallback((message: Omit<Message, "id" | "at">) => {
    setMessages((prev) => [...prev, { ...message, id: crypto.randomUUID(), at: Date.now() }]);
  }, []);

  const send = useCallback(
    async (raw: string) => {
      const question = raw.trim();
      if (!question || activeRef.current) return;

      const turn: Turn = { id: crypto.randomUUID(), controller: new AbortController() };
      activeRef.current = turn;
      setStreaming(true);

      setInput("");
      setError(null);
      push({ role: "user", content: question });
      setMessages((prev) => [
        ...prev,
        {
          id: turn.id,
          role: "assistant",
          content: "",
          citations: [],
          at: Date.now(),
          status: "streaming",
        },
      ]);

      const patch = (update: Partial<Message>) =>
        setMessages((prev) => prev.map((m) => (m.id === turn.id ? { ...m, ...update } : m)));

      const append = (text: string) =>
        setMessages((prev) =>
          prev.map((m) => (m.id === turn.id ? { ...m, content: m.content + text } : m)),
        );

      const previousInteractionId = interactionIdRef.current;

      try {
        const docIdsPayload =
          selectedDocs && selectedDocs.size > 0 && selectedDocs.size < documents.length
            ? Array.from(selectedDocs)
            : null;

        await api.askStream(
          {
            question,
            previous_interaction_id: previousInteractionId,
            doc_ids: docIdsPayload,
          },
          {
            onCitations: (c: Citation[]) => {
              if (activeRef.current?.id === turn.id) patch({ citations: c });
            },
            onDelta: append,
            onDone: (id) => {
              if (id && activeRef.current?.id === turn.id) interactionIdRef.current = id;
            },
          },
          turn.controller.signal,
        );
      } catch (e) {
        if ((e as Error)?.name !== "AbortError" && !turn.controller.signal.aborted) {
          const message = e instanceof Error ? e.message : "Request failed";
          setError(message);
          patch({ role: "error", status: "error", content: message });
        }
      } finally {
        if (activeRef.current?.id === turn.id) {
          activeRef.current = null;
          setStreaming(false);
          setMessages((prev) =>
            prev.map((m) => (m.id === turn.id && m.status === "streaming" ? { ...m, status: "done" } : m)),
          );
          textRef.current?.focus();
        }
      }
    },
    [push, selectedDocs, documents.length],
  );

  const stop = useCallback(() => {
    const turn = activeRef.current;
    if (!turn) return;

    activeRef.current = null;
    setStreaming(false);
    setMessages((prev) =>
      prev.map((m) => (m.id === turn.id && m.status === "streaming" ? { ...m, status: "stopped" } : m)),
    );

    turn.controller.abort();
    textRef.current?.focus();
  }, []);

  const newConversation = useCallback(() => {
    if (activeRef.current) stop();
    setMessages([]);
    setError(null);
    interactionIdRef.current = null;
  }, [stop]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send(input);
    }
  };

  const hasDocuments = documents.length > 0;

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground">
      <DocumentPanel documents={documents} onChanged={refreshDocuments} />

      <main className="relative flex min-w-0 flex-1 flex-col">
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-border/60 bg-background/70 px-4 py-3.5 backdrop-blur-md sm:px-6 lg:pl-6">
          <div className="flex min-w-0 items-baseline gap-3 pl-12 lg:pl-0">
            <h1 className="truncate font-display text-lg tracking-tight text-ink sm:text-xl">
              Research Assistant
            </h1>
            <span className="hidden truncate text-xs text-muted-foreground sm:inline">
              grounded answers · cited to the exact passage
            </span>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <AnimatePresence mode="wait" initial={false}>
              {providerOk === false && (
                <motion.div
                  key="warn"
                  initial={{ opacity: 0, y: -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -4 }}
                >
                  <Badge variant="warn" className="gap-1">
                    <CircleAlert className="h-3 w-3" />
                    <span className="hidden sm:inline">API key not set</span>
                    <span className="sm:hidden">No key</span>
                  </Badge>
                </motion.div>
              )}
              {/* The "connected" state used to render a green "Gemini
                  connected" badge. It was the only colour in the app outside
                  the paper/ink/ember palette, and it reported nothing: a
                  working provider is the normal case, not news. Status now
                  appears only when there is something to act on — a missing
                  key, or a connection still in flight. */}
              {providerOk === null && (
                <motion.div
                  key="loading"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                >
                  <Badge variant="outline" className="gap-1.5">
                    <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/60 animate-pulse" />
                    <span className="hidden sm:inline">Connecting…</span>
                  </Badge>
                </motion.div>
              )}
            </AnimatePresence>
            <Button
              variant="ghost"
              size="sm"
              onClick={newConversation}
              disabled={streaming}
              className="gap-1.5 text-muted-foreground hover:text-ink"
              aria-label="Start a new conversation"
              title="New conversation"
            >
              <Plus className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">New chat</span>
            </Button>
            {!hasDocuments && (
              <Badge variant="outline" className="gap-1">
                <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/60" />
                <span className="hidden sm:inline">No documents</span>
              </Badge>
            )}
          </div>
        </header>

        <AnimatePresence>
          {documents.length >= 2 && (
            <motion.div
              key="filterbar"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden border-b border-border/60 bg-background/50"
            >
              <DocFilterBar
                documents={documents}
                selected={selectedDocs}
                onChange={setSelectedDocs}
                disabled={streaming}
              />
            </motion.div>
          )}
        </AnimatePresence>

        <AnimatePresence>
          {error && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden border-b border-destructive/30 bg-destructive/10"
            >
              <div className="px-6 py-2 text-xs text-destructive">{error}</div>
            </motion.div>
          )}
        </AnimatePresence>

        <div className="relative flex min-h-0 flex-1 flex-col">
          <ScrollArea viewportRef={viewportRef} className="flex-1 min-h-0">
            <div className="mx-auto max-w-3xl px-4 sm:px-6 py-6 sm:py-8">
              {messages.length === 0 ? (
                <Welcome
                  hasDocuments={hasDocuments}
                  onSuggestion={(s) => void send(s)}
                />
              ) : (
                <div className="space-y-6">
                  <AnimatePresence initial={false}>
                    {messages.map((m) => (
                      <motion.div
                        key={m.id}
                        layout
                        initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
                      >
                        <MessageBubble
                          message={m}
                          streaming={m.status === "streaming"}
                          documentCount={documents.length}
                        />
                      </motion.div>
                    ))}
                  </AnimatePresence>
                  <div ref={endRef} className="h-px" />
                </div>
              )}
            </div>
          </ScrollArea>
          <AnimatePresence>
            {canScroll && (
              <motion.div
                key="jump"
                initial={{ opacity: 0, y: 10, scale: 0.8 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 10, scale: 0.8 }}
                transition={{ type: "spring", stiffness: 420, damping: 30 }}
                style={{ right: "max(1rem, calc((100% - 48rem) / 4 - 1.25rem))" }}
                className="pointer-events-none absolute bottom-5 z-20"
              >
                <button
                  type="button"
                  onClick={() => jumpToEdge(atBottom)}
                  aria-label={atBottom ? "Scroll to top" : "Scroll to bottom"}
                  title={atBottom ? "Scroll to top" : "Scroll to bottom"}
                  className={cn(
                    "pointer-events-auto grid h-9 w-9 place-items-center rounded-full",
                    "border border-border/70 bg-card/85 text-ink/60 backdrop-blur-md",
                    "shadow-[0_6px_20px_-8px_hsl(var(--ink)/0.45)]",
                    "transition-all duration-200 hover:-translate-y-0.5",
                    "hover:border-ink/25 hover:bg-card hover:text-ink",
                    "active:translate-y-0",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  )}
                >
                  {atBottom ? (
                    <ArrowUp className="h-3.5 w-3.5" />
                  ) : (
                    <ArrowDown className="h-3.5 w-3.5" />
                  )}
                </button>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        <div className="shrink-0 border-t border-border/60 bg-background/80 backdrop-blur-md">
          <div className="mx-auto max-w-3xl px-4 sm:px-6 py-3 sm:py-4">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void send(input);
              }}
              className={cn(
                "group relative flex items-end gap-2 rounded-2xl border bg-card",
                "border-border/80 shadow-[0_1px_0_hsl(var(--ink)/0.04),0_10px_30px_-16px_hsl(var(--ink)/0.18)]",
                "focus-within:border-ink/40 focus-within:shadow-[0_1px_0_hsl(var(--ink)/0.05),0_18px_40px_-16px_hsl(var(--ink)/0.30)]",
                "transition-shadow",
                !hasDocuments && "opacity-60",
              )}
            >
              <Textarea
                ref={textRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                placeholder={
                  hasDocuments
                    ? "Ask a question about your documents…"
                    : "Upload a document in the sidebar to begin"
                }
                rows={1}
                aria-label="Your question"
                disabled={!hasDocuments}
                className="min-h-[52px] flex-1 resize-none border-0 bg-transparent px-4 py-3.5 text-[15px] shadow-none focus-visible:ring-0 focus-visible:ring-offset-0"
              />
              <Separator orientation="vertical" className="my-2 h-8" />
              <div className="p-2">
                {streaming ? (
                  <Button
                    type="button"
                    size="icon"
                    variant="ember"
                    onClick={stop}
                    aria-label="Stop generating"
                  >
                    <Square className="h-3.5 w-3.5 fill-current" />
                  </Button>
                ) : (
                  <Button
                    type="submit"
                    size="icon"
                    variant="default"
                    disabled={!input.trim() || !hasDocuments}
                    aria-label="Send"
                    className="disabled:opacity-40"
                  >
                    <ArrowUp className="h-4 w-4" />
                  </Button>
                )}
              </div>
            </form>

            <p className="mt-2.5 hidden sm:block text-center text-[11px] text-muted-foreground">
              <kbd className="rounded border border-border bg-card px-1.5 py-0.5 font-mono text-[10px]">
                Enter
              </kbd>{" "}
              to send ·{" "}
              <kbd className="rounded border border-border bg-card px-1.5 py-0.5 font-mono text-[10px]">
                Shift+Enter
              </kbd>{" "}
              for a new line · answers cite the page they came from
            </p>
          </div>
        </div>
      </main>
    </div>
  );
}

function DocFilterBar({
  documents,
  selected,
  onChange,
  disabled,
}: {
  documents: DocumentRecord[];
  selected: Set<string> | null;
  onChange: (next: Set<string> | null) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLUListElement>(null);
  const [anchor, setAnchor] = useState<{
    top: number;
    left: number;
    width: number;
    placement: "bottom" | "top";
  } | null>(null);

  const place = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const gap = 8;
    const width = Math.max(r.width, Math.min(SCOPE_PANEL_W, window.innerWidth - 24));
    const left = Math.max(12, Math.min(r.left, window.innerWidth - width - 12));
    const below = window.innerHeight - (r.bottom + gap);
    const above = r.top - gap;
    const placement = below < 220 && above > below ? "top" : "bottom";
    const top = placement === "bottom" ? r.bottom + gap : r.top - gap;
    setAnchor({ top, left, width, placement });
  }, []);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      if (ref.current?.contains(target)) return;
      if (panelRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    window.addEventListener("mousedown", handler);
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("mousedown", handler);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, place]);

  useEffect(() => {
    if (open) place();
  }, [open, place]);

  const isAll = selected === null || selected.size === documents.length;

  const summary = isAll
    ? "All documents"
    : selected.size === 0
      ? "No documents"
      : `${selected.size} of ${documents.length}`;

  const toggle = (docId: string) => {
    if (selected === null) {
      onChange(new Set(documents.map((d) => d.doc_id).filter((id) => id !== docId)));
      return;
    }
    const next = new Set(selected);
    if (next.has(docId)) next.delete(docId);
    else next.add(docId);
    if (next.size === documents.length) onChange(null);
    else onChange(next);
  };

  return (
    <div ref={ref} className="relative flex items-center gap-2 px-6 py-2">
      <Filter className="h-3.5 w-3.5 text-muted-foreground" />
      <span className="text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
        Scope
      </span>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        disabled={disabled}
        className={cn(
          "inline-flex items-center gap-2 rounded-md border border-border bg-card px-3 py-1 text-sm",
          "hover:bg-accent transition-colors disabled:opacity-50",
        )}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="font-medium text-ink">{summary}</span>
        <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
      </button>

      {createPortal(
        <AnimatePresence>
          {open && anchor && (
            <motion.ul
              ref={panelRef}
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.15 }}
              style={
                anchor.placement === "top"
                  ? {
                      bottom: window.innerHeight - anchor.top,
                      left: anchor.left,
                      width: anchor.width,
                    }
                  : { top: anchor.top, left: anchor.left, width: anchor.width }
              }
              className="fixed z-[90] max-h-[min(60vh,420px)] overflow-y-auto overscroll-contain rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-xl"
              role="listbox"
            >
            <li>
              <button
                type="button"
                onClick={() => onChange(null)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm hover:bg-accent",
                  isAll && "text-ember font-medium",
                )}
              >
                <Check className={cn("h-3.5 w-3.5", isAll ? "opacity-100" : "opacity-0")} />
                All documents ({documents.length})
              </button>
            </li>
            <li>
              <div className="my-1 h-px bg-border/60" />
            </li>
            {documents.map((doc) => {
              const checked = isAll || (selected?.has(doc.doc_id) ?? false);
              return (
                <li key={doc.doc_id}>
                  <button
                    type="button"
                    onClick={() => toggle(doc.doc_id)}
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm hover:bg-accent"
                    role="option"
                    aria-selected={checked}
                  >
                    <span
                      className={cn(
                        "grid h-4 w-4 shrink-0 place-items-center rounded border transition-colors",
                        checked
                          ? "border-ember bg-ember text-white"
                          : "border-border bg-background",
                      )}
                    >
                      {checked && <Check className="h-3 w-3" />}
                    </span>
                    <span className="truncate text-ink" title={doc.source}>
                      {doc.source}
                    </span>
                  </button>
                </li>
              );
            })}
            {selected !== null && selected.size > 0 && selected.size < documents.length && (
              <>
                <li>
                  <div className="my-1 h-px bg-border/60" />
                </li>
                <li>
                  <button
                    type="button"
                    onClick={() => onChange(null)}
                    className="flex w-full items-center justify-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-accent hover:text-ink"
                  >
                    <X className="h-3 w-3" />
                    Clear filter
                  </button>
                </li>
              </>
            )}
            </motion.ul>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </div>
  );
}

function Welcome({
  hasDocuments,
  onSuggestion,
}: {
  hasDocuments: boolean;
  onSuggestion: (s: string) => void;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
      className="grid place-items-center text-center py-10"
    >
      <motion.div
        initial={{ scale: 0.6, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ delay: 0.05, duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
        className="grid h-14 w-14 place-items-center rounded-2xl bg-ink text-paper shadow-[0_12px_30px_-12px_hsl(var(--ink)/0.55)]"
      >
        <Wand2 className="h-6 w-6" />
      </motion.div>

      <motion.h2
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1, duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
        className="mt-6 font-display text-4xl tracking-tight text-ink text-balance"
      >
        Ask your documents anything.
      </motion.h2>

      <motion.p
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.16, duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
        className="mt-3 max-w-md text-balance text-sm leading-relaxed text-muted-foreground"
      >
        Answers are grounded in the pages you upload and cite their source.
        If the documents do not cover a question, you will be told so
        rather than given a guess.
      </motion.p>

      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.24, duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
        className="mt-8 flex flex-wrap justify-center gap-2"
      >
        {SUGGESTIONS.map((s, i) => (
          <motion.div
            key={s}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.32 + i * 0.06, duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
          >
            <Button
              variant="outline"
              size="sm"
              disabled={!hasDocuments}
              onClick={() => onSuggestion(s)}
              className="rounded-full bg-card/60 hover:bg-card"
            >
              <Sparkles className="h-3 w-3 text-ember" />
              {s}
            </Button>
          </motion.div>
        ))}
      </motion.div>

      {!hasDocuments && (
        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.6, duration: 0.4 }}
          className="mt-6 text-xs text-muted-foreground/80"
        >
          ↑ Upload a document in the sidebar to start asking questions.
        </motion.p>
      )}
    </motion.div>
  );
}