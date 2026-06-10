import { useCallback, useEffect, useRef } from "react";

interface UseSidebarResizeProps {
  resizable: boolean;
  isCollapsed: boolean;
  sidebarWidth: number;
  minWidth: number;
  maxWidth: number;
  position: "left" | "right";
  onWidthChange?: (width: number) => void;
  onLiveWidthChange?: (width: number) => void;
  onResizeStart?: () => void;
  onResizeEnd?: () => void;
}

export const useSidebarResize = ({
  resizable,
  isCollapsed,
  sidebarWidth,
  minWidth,
  maxWidth,
  position,
  onWidthChange,
  onLiveWidthChange,
  onResizeStart,
  onResizeEnd,
}: UseSidebarResizeProps) => {
  const resizingRef = useRef(false);
  const startXRef = useRef(0);
  const startWidthRef = useRef(sidebarWidth);
  const liveWidthRef = useRef(sidebarWidth);
  const frameRef = useRef<number | null>(null);
  const detachDragListenersRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    if (resizingRef.current) {
      return;
    }
    startWidthRef.current = sidebarWidth;
    liveWidthRef.current = sidebarWidth;
  }, [sidebarWidth]);

  const applyLiveWidth = useCallback(
    (width: number) => {
      liveWidthRef.current = width;
      if (frameRef.current !== null) {
        return;
      }

      frameRef.current = window.requestAnimationFrame(() => {
        frameRef.current = null;
        onLiveWidthChange?.(liveWidthRef.current);
      });
    },
    [onLiveWidthChange],
  );

  const clearDragState = useCallback(() => {
    document.body.style.removeProperty("cursor");
    document.body.style.removeProperty("user-select");
  }, []);

  const finishResize = useCallback(
    (commitWidth: boolean) => {
      if (!resizingRef.current) {
        return;
      }

      resizingRef.current = false;
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
        onLiveWidthChange?.(liveWidthRef.current);
      }
      detachDragListenersRef.current();
      clearDragState();
      if (commitWidth) {
        onWidthChange?.(liveWidthRef.current);
      }
      onResizeEnd?.();
    },
    [clearDragState, onLiveWidthChange, onResizeEnd, onWidthChange],
  );

  const handleMouseMove = useCallback(
    (e: MouseEvent) => {
      if (!resizingRef.current) return;
      if ((e.buttons & 1) !== 1) {
        finishResize(true);
        return;
      }

      const delta =
        position === "left"
          ? e.clientX - startXRef.current
          : startXRef.current - e.clientX;
      let newWidth = startWidthRef.current + delta;
      newWidth = Math.max(minWidth, Math.min(maxWidth, newWidth));

      if (newWidth === liveWidthRef.current) {
        return;
      }

      applyLiveWidth(newWidth);
    },
    [applyLiveWidth, finishResize, maxWidth, minWidth, position],
  );

  const handleMouseUp = useCallback(() => {
    finishResize(true);
  }, [finishResize]);

  const detachDragListeners = useCallback(() => {
    if (typeof document === "undefined") {
      return;
    }

    document.removeEventListener("mousemove", handleMouseMove);
    document.removeEventListener("mouseup", handleMouseUp);
    window.removeEventListener("blur", handleMouseUp);
  }, [handleMouseMove, handleMouseUp]);

  useEffect(() => {
    detachDragListenersRef.current = detachDragListeners;
  }, [detachDragListeners]);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (!resizable || isCollapsed || e.button !== 0) return;

      resizingRef.current = true;
      startXRef.current = e.clientX;
      startWidthRef.current = sidebarWidth;
      liveWidthRef.current = sidebarWidth;
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      document.addEventListener("mousemove", handleMouseMove);
      document.addEventListener("mouseup", handleMouseUp);
      window.addEventListener("blur", handleMouseUp);
      onResizeStart?.();
      e.preventDefault();
    },
    [
      handleMouseMove,
      handleMouseUp,
      isCollapsed,
      onResizeStart,
      resizable,
      sidebarWidth,
    ],
  );

  useEffect(() => {
    return () => {
      detachDragListenersRef.current();
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
      }
      clearDragState();
    };
  }, [clearDragState]);

  return { handleMouseDown };
};
