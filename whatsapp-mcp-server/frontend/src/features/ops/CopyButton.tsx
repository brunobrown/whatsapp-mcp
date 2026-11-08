import { Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n/t";
import { showNotice } from "@/lib/notices";

/** Copies `value` to the clipboard and answers with a 'Copied' toast, or says it could not. */
export function CopyButton({ value, what }: { value: string; what: string }) {
  const t = useT();
  const copy = () => {
    const clipboard = typeof navigator === "undefined" ? undefined : (navigator.clipboard as Clipboard | undefined);
    if (!clipboard) {
      showNotice("copyFailed");
      return;
    }
    clipboard.writeText(value).then(
      () => {
        showNotice("copied");
      },
      () => {
        showNotice("copyFailed");
      },
    );
  };
  return (
    <Button variant="ghost" size="icon" aria-label={t("status.copy", { what })} onClick={copy}>
      <Copy aria-hidden="true" />
    </Button>
  );
}
