import { useEffect, useRef } from "react";

export interface SaveShortcut {
  /** Where the shortcut counts: true for a node inside the form or its save bar. */
  inside: (target: Node) => boolean;
  onSave: () => void;
  enabled: boolean;
}

/**
 * Ctrl+S (Cmd+S on a Mac) saves the open form, but only when the focus is inside it; the browser's own save dialog
 * never opens there. Elsewhere the key keeps its usual meaning.
 */
export function useSaveShortcut({ inside, onSave, enabled }: SaveShortcut): void {
  const latest = useRef({ inside, onSave });
  useEffect(() => {
    latest.current = { inside, onSave };
  });
  useEffect(() => {
    if (!enabled) return;
    const onKeyDown = (event: KeyboardEvent): void => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey || event.key.toLowerCase() !== "s") return;
      if (!(event.target instanceof Node) || !latest.current.inside(event.target)) return;
      event.preventDefault();
      latest.current.onSave();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [enabled]);
}
