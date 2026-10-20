import * as React from "react";
import * as ScrollAreaPrimitive from "@radix-ui/react-scroll-area";
import { cn } from "@/lib/utils";

const ScrollArea = React.forwardRef<
  React.ElementRef<typeof ScrollAreaPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof ScrollAreaPrimitive.Root> & {
    /** Ref to the scrollable `Viewport` — the element that actually carries
     *  `scrollTop` / `scrollHeight`. The forwarded ref only points at the
     *  `Root`, so scroll-aware UI (scroll-to-bottom, position tracking) needs
     *  this one. */
    viewportRef?: React.Ref<HTMLDivElement>;
  }
>(({ className, children, viewportRef, ...props }, ref) => (
  <ScrollAreaPrimitive.Root
    ref={ref}
    className={cn("relative overflow-hidden", className)}
    {...props}
  >
    {/* Radix renders an inner `display: table` wrapper whose intrinsic width
        is driven by the widest child. Without `w-full` + `min-w-0` on both
        the Viewport and the wrapper, a long filename widens the whole scroll
        area and pushes trailing controls (e.g. a delete button) out of the
        visible box. Forcing the width here is what makes `truncate` work
        reliably inside a scroll area. */}
    <ScrollAreaPrimitive.Viewport
      ref={viewportRef}
      className="h-full w-full min-w-0 rounded-[inherit] [&>div]:!block [&>div]:!w-full [&>div]:!min-w-0"
    >
      {children}
    </ScrollAreaPrimitive.Viewport>
    <ScrollBar />
    <ScrollAreaPrimitive.Corner />
  </ScrollAreaPrimitive.Root>
));
ScrollArea.displayName = ScrollAreaPrimitive.Root.displayName;

const ScrollBar = React.forwardRef<
  React.ElementRef<typeof ScrollAreaPrimitive.ScrollAreaScrollbar>,
  React.ComponentPropsWithoutRef<typeof ScrollAreaPrimitive.ScrollAreaScrollbar>
>(({ className, orientation = "vertical", ...props }, ref) => (
  <ScrollAreaPrimitive.ScrollAreaScrollbar
    ref={ref}
    orientation={orientation}
    className={cn(
      "flex touch-none select-none transition-colors",
      orientation === "vertical" &&
        "h-full w-1.5 border-l border-l-transparent p-px",
      orientation === "horizontal" &&
        "h-1.5 flex-col border-t border-t-transparent p-px",
      className,
    )}
    {...props}
  >
    <ScrollAreaPrimitive.ScrollAreaThumb className="relative flex-1 rounded-full bg-border hover:bg-muted-foreground/40 transition-colors" />
  </ScrollAreaPrimitive.ScrollAreaScrollbar>
));
ScrollBar.displayName = ScrollAreaPrimitive.ScrollAreaScrollbar.displayName;

export { ScrollArea, ScrollBar };