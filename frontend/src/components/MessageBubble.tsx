import { useEffect, useMemo, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { AlertCircle, BookOpen, FileText, Square, User } from "lucide-react";
import { citationId, renderMarkdown } from "../lib/markdown";
import type { Citation, Message } from "../lib/api";
import { cn } from "../lib/utils";
import { Avatar, AvatarFallback } from "./ui/avatar";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "./ui/accordion";

type Props = {
  message: Message;
  streaming: boolean;
  documentCount?: number;
};

function scopeFor(messageId: string): string {
  return messageId.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 8) || "0";
}

export function MessageBubble({
  message,
  streaming,
  documentCount = 0,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const stopped = message.status === "stopped";
  const [openSource, setOpenSource] = useState<string | undefined>(undefined);

  const citations = message.citations ?? [];
  const scope = useMemo(() => scopeFor(message.id), [message.id]);

  useEffect(() => {
    if (streaming)
      ref.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [message.content, streaming]);

  const onProseClick = (event: React.MouseEvent<HTMLDivElement>) => {
    const anchor = (event.target as HTMLElement).closest("a.cite");
    const href = anchor?.getAttribute("href") ?? "";
    if (!href.startsWith("#")) return;

    event.preventDefault();
    const id = href.slice(1);
    setOpenSource(id);

    // The content panel mounts on the next commit; scroll after it exists so
    // the reader lands on the excerpt rather than the collapsed header.
    requestAnimationFrame(() => {
      const target = ref.current?.querySelector(`[id="${CSS.escape(id)}"]`);
      target?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  };

  if (message.role === "user") {
    return (
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
        className="flex justify-end gap-3"
      >
        <div className="max-w-[78%]">
          <div className="rounded-2xl rounded-br-md bg-ink px-4 py-3 text-paper shadow-[0_4px_20px_-10px_hsl(var(--ink)/0.55)]">
            <p className="text-[15px] leading-relaxed whitespace-pre-wrap">
              {message.content}
            </p>
          </div>
        </div>
        <Avatar className="h-8 w-8 mt-1">
          <AvatarFallback className="bg-secondary text-ink">
            <User className="h-4 w-4" />
          </AvatarFallback>
        </Avatar>
      </motion.div>
    );
  }

  if (message.role === "error" || message.role === "system") {
    return (
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
        className="flex justify-start gap-3"
      >
        <Avatar className="h-8 w-8 mt-1">
          <AvatarFallback className="bg-destructive text-destructive-foreground">
            <AlertCircle className="h-4 w-4" />
          </AvatarFallback>
        </Avatar>
        <div className="max-w-[78%] rounded-2xl rounded-bl-md border border-destructive/20 bg-destructive/5 px-4 py-3">
          <p className="text-sm leading-relaxed text-destructive">
            {message.content}
          </p>
        </div>
      </motion.div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.32, ease: [0.22, 1, 0.36, 1] }}
      className="flex justify-start gap-3"
      ref={ref}
    >
      <Avatar className="h-8 w-8 mt-1">
        <AvatarFallback className="bg-ink text-paper">
          <FileText className="h-4 w-4" />
        </AvatarFallback>
      </Avatar>
      <div className="w-full min-w-0 flex-1">
        <div className="rounded-2xl rounded-bl-md border border-border/70 bg-card px-4 py-4 sm:px-5 shadow-[0_1px_0_hsl(var(--ink)/0.03),0_8px_24px_-16px_hsl(var(--ink)/0.18)]">
          {streaming && !message.content ? (
            <div
              className="flex flex-col gap-3.5"
              role="status"
              aria-live="polite"
            >
              {citations.length === 0 && (
                <div className="answer-skeleton" aria-hidden="true">
                  <span className="w-full" />
                  <span />
                  <span />
                </div>
              )}
              <PipelineStatus
                step={citations.length > 0 ? 1 : 0}
                documentCount={documentCount}
                sourceCount={citations.length}
              />
            </div>
          ) : message.content ? (
            <>
              <div
                className="prose-doc"
                onClick={onProseClick}
                dangerouslySetInnerHTML={{
                  __html: renderMarkdown(message.content, scope),
                }}
              />
              <AnimatePresence>
                {streaming && (
                  <motion.span
                    key="caret"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="caret"
                    aria-label="Generating"
                  />
                )}
              </AnimatePresence>
              {stopped && (
                <p className="mt-2.5 flex items-center gap-1.5 text-[11px] uppercase tracking-[0.14em] text-muted-foreground/80">
                  <Square className="h-2.5 w-2.5 fill-current" />
                  Stopped
                </p>
              )}
            </>
          ) : (
            <p className="text-[13px] text-muted-foreground">
              {stopped
                ? "Stopped before any text was generated."
                : "No response was generated."}
            </p>
          )}
        </div>

        <Citations
          citations={citations}
          scope={scope}
          open={openSource}
          onOpenChange={setOpenSource}
        />
      </div>
    </motion.div>
  );
}

function PipelineStatus({
  step,
  documentCount,
  sourceCount,
}: {
  step: number;
  documentCount: number;
  sourceCount: number;
}) {
  const label =
    step === 0
      ? documentCount > 0
        ? `Searching ${documentCount} document${documentCount > 1 ? "s" : ""}`
        : "Searching your documents"
      : `Reading ${sourceCount} source${sourceCount > 1 ? "s" : ""}`;

  return (
    <div className="flex items-center gap-3 text-muted-foreground">
      <span className="flex shrink-0 items-center gap-1" aria-hidden="true">
        {[0, 1, 2].map((index) => (
          <span
            key={index}
            className={cn(
              "h-[3px] w-4 rounded-full transition-colors duration-500",
              index < step && "bg-ink/60",
              index === step && "pipeline-active bg-ember",
              index > step && "bg-ink/10",
            )}
          />
        ))}
      </span>
      <span className="truncate text-[12.5px] tracking-tight">{label}</span>
    </div>
  );
}

function Citations({
  citations,
  scope,
  open,
  onOpenChange,
}: {
  citations: Citation[];
  scope: string;
  open: string | undefined;
  onOpenChange: (value: string | undefined) => void;
}) {
  const [staggered, setStaggered] = useState(false);
  useEffect(() => {
    if (citations.length === 0) return;
    const timer = setTimeout(() => setStaggered(true), 1400);
    return () => clearTimeout(timer);
  }, [citations.length]);

  if (citations.length === 0) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.32, delay: 0.05, ease: [0.22, 1, 0.36, 1] }}
      className="mt-3 min-w-0"
    >
      <div className="mb-2 flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        <BookOpen className="h-3.5 w-3.5 shrink-0" />
        <span>Sources</span>
        <span className="rounded-full bg-ember/10 px-1.5 py-0.5 text-[10px] font-semibold text-ember tabular-nums">
          {citations.length}
        </span>
      </div>

      <Accordion
        type="single"
        collapsible
        value={open}
        onValueChange={onOpenChange}
        className="overflow-hidden rounded-lg border border-border/60 bg-card/50"
      >
        {citations.map((c, i) => {
          const id = citationId(scope, i + 1);
          const pct = Math.max(0, Math.min(1, c.score));
          const locator = c.locator || `p.${c.page}`;
          return (
            <AccordionItem
              key={`${c.doc_id}-${c.page}-${i}`}
              value={id}
              id={id}
              style={staggered ? undefined : { animationDelay: `${i * 0.07}s` }}
              className={cn(
                "transition-colors",
                !staggered && "cite-row-in",
                open === id && "bg-muted/40",
              )}
            >
              <AccordionTrigger className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)_auto_auto_auto] items-center gap-2.5 py-2.5 hover:no-underline">
                <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded bg-ember/10 text-[10px] font-bold text-ember tabular-nums">
                  {i + 1}
                </span>
                <span
                  className="min-w-0 truncate text-[13px] font-medium text-ink"
                  title={c.source}
                >
                  {c.source}
                </span>
                <span
                  className="max-w-[45%] shrink-0 truncate font-mono text-[10px] tabular-nums text-muted-foreground"
                  title={locator}
                >
                  {locator}
                </span>
                <span className="hidden shrink-0 items-center gap-1.5 sm:flex">
                  <span className="h-1 w-10 overflow-hidden rounded-full bg-muted">
                    <span
                      className="block h-full rounded-full bg-ember transition-[width] duration-500"
                      style={{ width: `${(pct * 100).toFixed(0)}%` }}
                    />
                  </span>
                  <span className="w-6 text-right font-mono text-[10px] tabular-nums text-muted-foreground">
                    {(pct * 100).toFixed(0)}
                  </span>
                </span>
              </AccordionTrigger>
              <AccordionContent>
                <p className="border-l-2 border-ember/30 pl-3 text-[12.5px] leading-relaxed text-foreground/80">
                  {c.snippet}
                </p>
              </AccordionContent>
            </AccordionItem>
          );
        })}
      </Accordion>
    </motion.div>
  );
}
