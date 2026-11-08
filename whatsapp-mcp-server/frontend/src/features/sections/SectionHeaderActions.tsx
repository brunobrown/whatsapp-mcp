import { Ellipsis, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/menu";
import { useT } from "@/i18n/t";

/** The "More actions" menu button of the section header; Restore defaults asks its own confirmation. */
export function SectionHeaderActions({ onRestoreDefaults }: { onRestoreDefaults: () => void }) {
  const t = useT();
  return (
    <Menu>
      <MenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={t("section.moreActions")}>
          <Ellipsis aria-hidden="true" />
        </Button>
      </MenuTrigger>
      <MenuContent>
        <MenuItem onSelect={onRestoreDefaults}>
          <RotateCcw aria-hidden="true" />
          {t("section.restoreAll")}
        </MenuItem>
      </MenuContent>
    </Menu>
  );
}
