import { useState } from "react";
import { Trash2 } from "lucide-react";
import { api } from "../lib/api";
import { ConfirmDialog } from "./ConfirmDialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

type Props = {
  docId: string;
  source: string;
  onDeleted: () => void;
};

export function DeleteDocumentButton({ docId, source, onDeleted }: Props) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDelete = async () => {
    setError(null);
    try {
      await api.deleteDocument(docId);
      onDeleted();
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
      throw e;
    }
  };

  const trigger = (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        setOpen(true);
      }}
      aria-label={`Delete ${source}`}
      className="
        grid h-7 w-7 shrink-0 place-items-center rounded-md
        text-muted-foreground/60
        hover:bg-destructive/10 hover:text-destructive
        focus-visible:bg-destructive/10 focus-visible:text-destructive
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring
        transition-colors
      "
    >
      <Trash2 className="h-3.5 w-3.5" />
    </button>
  );

  return (
    <>
      <Tooltip>
        <TooltipTrigger asChild>{trigger}</TooltipTrigger>
        <TooltipContent>Remove from library</TooltipContent>
      </Tooltip>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="Remove document?"
        description={
          <>
            This will permanently remove{" "}
            <strong className="text-foreground font-medium">{source}</strong>{" "}
            and all of its indexed chunks from the library. The source file
            stays on disk and can be re-uploaded later.
            {error && (
              <p className="mt-2 text-destructive text-xs font-medium">
                {error}
              </p>
            )}
          </>
        }
        confirmLabel="Remove"
        onConfirm={handleDelete}
        destructive
      />
    </>
  );
}
