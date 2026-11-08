import { CircleAlert } from "lucide-react";
import { createContext, useContext, useId } from "react";
import type { ComponentProps, ReactNode } from "react";
import { t } from "@/i18n/t";
import { cn } from "@/lib/utils";

const TitleId = createContext<string | undefined>(undefined);

/** A titled group on its own surface: a border, a rounded corner and a colour different from the page. */
export function Card({ className, children, ...props }: ComponentProps<"section">) {
  const titleId = useId();
  return (
    <TitleId.Provider value={titleId}>
      <section
        aria-labelledby={titleId}
        className={cn("rounded-card border border-line bg-panel text-ink", className)}
        {...props}
      >
        {children}
      </section>
    </TitleId.Provider>
  );
}

export interface CardHeaderProps {
  title: ReactNode;
  /** Heading level of the title; the page owns level 1. */
  level?: 2 | 3 | 4;
  /** One line under the title. */
  description?: ReactNode;
  /** Marks the card as holding a field that needs attention. */
  error?: boolean;
  /** Controls placed at the end of the header, such as a refresh button. */
  actions?: ReactNode;
  className?: string;
}

export function CardHeader({ title, level = 2, description, error, actions, className }: CardHeaderProps) {
  const id = useContext(TitleId);
  const Heading = `h${String(level)}` as "h2" | "h3" | "h4";
  return (
    <div className={cn("flex flex-wrap items-start justify-between gap-2 border-b border-line px-4 py-3", className)}>
      <div className="min-w-0">
        <Heading id={id} className="flex flex-wrap items-center gap-2 text-base font-semibold">
          {title}
          {error ? (
            <span data-needs-attention className="inline-flex items-center gap-1 text-sm font-medium text-danger-ink">
              <CircleAlert aria-hidden="true" className="size-4" />
              {t("card.needsAttention")}
            </span>
          ) : null}
        </Heading>
        {description ? <p className="mt-0.5 text-sm text-muted">{description}</p> : null}
      </div>
      {actions}
    </div>
  );
}

export function CardBody({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("px-4 py-4", className)} {...props} />;
}
