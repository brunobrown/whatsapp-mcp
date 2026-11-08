import { createContext, useContext } from "react";
import { createPortal } from "react-dom";
import type { ReactNode } from "react";

/** The places of the shell a section puts its own controls in: the header actions and the strip under the content. */
export interface SectionSlots {
  actions: HTMLElement | null;
  bar: HTMLElement | null;
}

/** Null outside the shell (a form shown on its own): the controls then stay where the form is. */
export const SectionSlotsContext = createContext<SectionSlots | null>(null);

/** Renders `children` in the shell slot, or in place when there is no shell; nothing until the slot exists. */
export function InSlot({ slot, children }: { slot: keyof SectionSlots; children: ReactNode }) {
  const slots = useContext(SectionSlotsContext);
  if (slots === null) return children;
  const target = slots[slot];
  return target === null ? null : createPortal(children, target);
}
