import { X } from "lucide-react";
import { AlertDialog, Dialog as DialogPrimitive } from "radix-ui";
import { useRef } from "react";
import type { ComponentProps, ReactNode } from "react";
import { useT } from "@/i18n/t";
import { cn } from "@/lib/utils";
import { Button } from "./button";

const overlayClass = "fixed inset-0 z-50 bg-[#0b141a]/60";
const panelClass =
  "fixed top-1/2 left-1/2 z-50 max-h-[calc(100dvh-2rem)] w-[min(30rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-card border border-line bg-panel p-5 text-ink shadow-xl";

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

export function DialogTitle({ className, ...props }: ComponentProps<typeof DialogPrimitive.Title>) {
  return <DialogPrimitive.Title className={cn("m-0 pr-10 text-lg font-semibold", className)} {...props} />;
}

export function DialogDescription({ className, ...props }: ComponentProps<typeof DialogPrimitive.Description>) {
  return <DialogPrimitive.Description className={cn("mt-2 mb-0 text-muted", className)} {...props} />;
}

export function DialogFooter({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end", className)} {...props} />;
}

export function DialogContent({ className, children, ...props }: ComponentProps<typeof DialogPrimitive.Content>) {
  const t = useT();
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className={overlayClass} />
      <DialogPrimitive.Content className={cn(panelClass, className)} {...props}>
        {children}
        <DialogPrimitive.Close asChild>
          <Button variant="ghost" size="icon" aria-label={t("common.close")} className="absolute top-2 right-2">
            <X aria-hidden="true" />
          </Button>
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

interface ConfirmBase {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  confirmLabel: string;
  cancelLabel: string;
  onConfirm: () => void;
  children?: ReactNode;
}

/** A destructive confirmation must say what the operator loses. */
export type ConfirmDialogProps =
  | (ConfirmBase & { destructive?: false; consequence?: string })
  | (ConfirmBase & { destructive: true; consequence: string });

/**
 * Asks before something that cannot be undone or that replaces data. Escape and the cancel action leave
 * everything as it was. Focus starts on the confirm action, or on the cancel action when destructive, and
 * returns to what opened the dialog.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  cancelLabel,
  onConfirm,
  destructive = false,
  consequence,
  children,
}: ConfirmDialogProps) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  return (
    <AlertDialog.Root open={open} onOpenChange={onOpenChange}>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className={overlayClass} />
        <AlertDialog.Content
          className={panelClass}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
            (destructive ? cancelRef : confirmRef).current?.focus();
          }}
          onCloseAutoFocus={(event) => {
            // The dialog is controlled, so there is no Radix trigger to return to: go back to what had focus.
            event.preventDefault();
            openerRef.current?.focus();
          }}
        >
          <AlertDialog.Title className="m-0 text-lg font-semibold">{title}</AlertDialog.Title>
          <AlertDialog.Description className="mt-2 mb-0 text-muted">
            {description}
            {consequence ? <> {consequence}</> : null}
          </AlertDialog.Description>
          {children}
          <DialogFooter>
            <AlertDialog.Cancel asChild>
              <Button ref={cancelRef} variant="secondary">
                {cancelLabel}
              </Button>
            </AlertDialog.Cancel>
            <AlertDialog.Action asChild>
              <Button
                ref={confirmRef}
                variant={destructive ? "danger" : "primary"}
                onClick={() => {
                  onConfirm();
                }}
              >
                {confirmLabel}
              </Button>
            </AlertDialog.Action>
          </DialogFooter>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}
