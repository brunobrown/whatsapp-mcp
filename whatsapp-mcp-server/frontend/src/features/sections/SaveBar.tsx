import { forwardRef } from "react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n/t";

export interface SaveBarProps {
  /** The id of the form the Save button submits (the bar may sit outside it). */
  formId: string;
  saving: boolean;
  onDiscard: () => void;
}

/** The bar a form shows only while it has unsaved edits: what is pending, Discard and Save. */
export const SaveBar = forwardRef<HTMLDivElement, SaveBarProps>(function SaveBar({ formId, saving, onDiscard }, ref) {
  const t = useT();
  return (
    <div ref={ref} data-save-bar className="flex flex-wrap items-center gap-2 border-t border-line bg-surface px-4 py-3 sm:px-6">
      <p role="status" className="m-0 min-w-0 flex-1 basis-40 text-sm font-medium">
        {t("section.unsaved")}
      </p>
      <Button variant="secondary" disabled={saving} onClick={onDiscard}>
        {t("section.discard")}
      </Button>
      <Button type="submit" form={formId} busy={saving}>
        {t("common.save")}
      </Button>
    </div>
  );
});
