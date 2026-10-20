const API_BASE = (import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

export type Citation = {
  doc_id: string;
  source: string;
  page: number;
  score: number;
  snippet: string;
  locator?: string;
};

export type MessageStatus = "streaming" | "done" | "stopped" | "error";

export type Message = {
  id: string;
  role: "user" | "assistant" | "error" | "system";
  content: string;
  citations?: Citation[];
  at: number;
  status?: MessageStatus;
};

export type AskResult = {
  answer: string;
  citations: Citation[];
  grounded: boolean;
  interaction_id: string | null;
  model: string;
};

export type DocumentRecord = {
  doc_id: string;
  source: string;
  doc_type?: string;
  confidence?: "" | "high" | "medium" | "low";
  notes?: string;
  pages: number;
  chunks: number;
  indexed_at: string;
};

export type FormatInfo = {
  doc_type: string;
  extensions: string[];
  description: string;
  available: boolean;
};

export type UploadLimits = {
  max_upload_mb: number;
  max_documents: number;
  max_units_per_document: number;
  extensions: string[];
};

export type PreviewChunk = {
  index: number;
  page: number;
  locator: string;
  text: string;
};

export type DocumentPreview = {
  doc_id: string;
  source: string;
  doc_type: string;
  pages: number;
  chunks: number;
  offset: number;
  items: PreviewChunk[];
  has_more: boolean;
};

export const api = {
  base: API_BASE,

  health: () =>
    fetch(`${API_BASE}/api/health`).then((r) => r.json()),

  formats: () =>
    fetch(`${API_BASE}/api/documents/formats`).then(
      (r) =>
        r.json() as Promise<{ formats: FormatInfo[]; unsupported: Record<string, string> }>,
    ),

  limits: () =>
    fetch(`${API_BASE}/api/documents/limits`).then((r) => r.json() as Promise<UploadLimits>),

  documents: () =>
    fetch(`${API_BASE}/api/documents`).then((r) => r.json() as Promise<DocumentRecord[]>),

  deleteDocument: (docId: string) =>
    fetch(`${API_BASE}/api/documents/${encodeURIComponent(docId)}`, { method: "DELETE" }),


  previewDocument: (docId: string, { offset = 0, limit = 60 } = {}) =>
    fetch(
      `${API_BASE}/api/documents/${encodeURIComponent(docId)}/preview` +
        `?offset=${offset}&limit=${limit}`,
    ).then(async (r) => {
      if (!r.ok) {
        throw new Error(
          (await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`,
        );
      }
      return r.json() as Promise<DocumentPreview>;
    }),

  documentFileUrl: (docId: string) =>
    `${API_BASE}/api/documents/${encodeURIComponent(docId)}/file`,

  documentFileExists: (docId: string) =>
    fetch(`${API_BASE}/api/documents/${encodeURIComponent(docId)}/file`, {
      method: "HEAD",
    }).then((r) => r.ok),

  documentFileText: (docId: string) =>
    fetch(`${API_BASE}/api/documents/${encodeURIComponent(docId)}/file`).then(
      async (r) => {
        if (!r.ok) {
          throw new Error(
            (await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`,
          );
        }
        return r.text();
      },
    ),

  documentRenderedUrl: (docId: string) =>
    `${API_BASE}/api/documents/${encodeURIComponent(docId)}/preview.pdf`,

  documentRenderable: (docId: string) =>
    fetch(`${API_BASE}/api/documents/${encodeURIComponent(docId)}/preview.pdf`, {
      method: "HEAD",
    }).then((r) => r.ok),

  ask: (body: { question: string; previous_interaction_id?: string | null; doc_ids?: string[] | null }) =>
    fetch(`${API_BASE}/api/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(async (r) => {
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
      return r.json() as Promise<AskResult>;
    }),

  askStream: (
    body: { question: string; previous_interaction_id?: string | null; doc_ids?: string[] | null },
    handlers: {
      onCitations: (c: Citation[]) => void;
      onDelta: (t: string) => void;
      onDone?: (interactionId: string | null) => void;
    },
    signal: AbortSignal,
  ): Promise<void> => {
    const { onCitations, onDelta, onDone } = handlers;

    return new Promise((resolve, reject) => {
      const halted = () => signal.aborted;

      let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
      let settled = false;

      const settle = (err?: Error) => {
        if (settled) return;
        settled = true;
        signal.removeEventListener("abort", onAbort);
        if (err) reject(err);
        else resolve();
      };

      const onAbort = () => {
        void reader?.cancel().catch(() => {});
        settle();
      };

      if (signal.aborted) return settle();
      signal.addEventListener("abort", onAbort, { once: true });

      const run = async () => {
        const response = await fetch(`${API_BASE}/api/ask/stream`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal,
        });

        if (halted()) return;
        if (!response.ok || !response.body) {
          throw new Error(`HTTP ${response.status}`);
        }

        const body_ = response.body.getReader();
        reader = body_;
        const decoder = new TextDecoder();
        let buffer = "";

        const dispatch = (frame: string) => {
          const event = /^event:[ \t]*(.*)$/m.exec(frame)?.[1]?.trim();
          const data = frame
            .split(/\r?\n/)
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).replace(/^ /, ""))
            .join("\n");
          if (!event || !data) return;

          const payload = JSON.parse(data) as Record<string, unknown>;
          if (event === "citations") onCitations(payload.citations as Citation[]);
          else if (event === "delta") onDelta(payload.text as string);
          else if (event === "error") throw new Error(payload.message as string);
          else if (event === "done") {
            onDone?.((payload.interaction_id as string | undefined) ?? null);
          }
        };

        for (;;) {
          const { done, value } = await body_.read();
          if (done || halted()) break;
          buffer += decoder.decode(value, { stream: true });

          const frames = buffer.split(/\r?\n\r?\n/);
          buffer = frames.pop() ?? "";

          for (const frame of frames) {
            if (halted()) return;
            dispatch(frame);
          }
        }

        if (!halted() && buffer.trim()) dispatch(buffer);
      };

      run().then(
        () => settle(),
        (err: unknown) => {
          if ((err as Error)?.name === "AbortError" || halted()) return settle();
          settle(err instanceof Error ? err : new Error(String(err)));
        },
      );
    });
  },

  upload: (file: File, onProgress?: (pct: number) => void) =>
    new Promise<{
      doc_id: string;
      source: string;
      doc_type: string;
      pages: number;
      chunks: number;
      message: string;
    }>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `${API_BASE}/api/documents`);

        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable && onProgress) {
            onProgress(Math.round((e.loaded / e.total) * 100));
          }
        };
        xhr.onload = () => {
          try {
            const data = JSON.parse(xhr.responseText) as Record<string, unknown>;
            if (xhr.status >= 200 && xhr.status < 300) {
              resolve(data as never);
            } else {
              reject(new Error((data.detail as string) ?? `HTTP ${xhr.status}`));
            }
          } catch {
            reject(new Error(`HTTP ${xhr.status}`));
          }
        };
        xhr.onerror = () => reject(new Error("Network error"));
        xhr.onabort = () => reject(new Error("Upload cancelled"));

        const form = new FormData();
        form.append("file", file);
        xhr.send(form);
    }),
};
