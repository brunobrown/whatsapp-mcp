import { CircleAlert, CircleCheck, Info, TriangleAlert } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n/t";
import { cn } from "@/lib/utils";

export type BannerKind = "info" | "success" | "warning" | "error";

const KINDS: Record<BannerKind, { icon: LucideIcon; role: "status" | "alert"; tone: string }> = {
  info: { icon: Info, role: "status", tone: "bg-accent-soft text-accent-ink" },
  success: { icon: CircleCheck, role: "status", tone: "bg-accent-soft text-accent-ink" },
  warning: { icon: TriangleAlert, role: "status", tone: "bg-warning-soft text-warning-ink" },
  error: { icon: CircleAlert, role: "alert", tone: "bg-danger-soft text-danger-ink" },
};

export interface BannerProps extends Omit<ComponentProps<"div">, "title"> {
  kind: BannerKind;
  title?: ReactNode;
}

/** A message that stays on the page. Errors interrupt screen readers; the other kinds wait their turn. */
export function Banner({ kind, title, className, children, ...props }: BannerProps) {
  const { icon: Icon, role, tone } = KINDS[kind];
  return (
    <div role={role} className={cn("flex items-start gap-3 rounded-control px-4 py-3", tone, className)} {...props}>
      <Icon aria-hidden="true" className="mt-0.5 size-5 shrink-0" />
      <div className="min-w-0 flex-1">
        {title ? <p className="m-0 font-semibold">{title}</p> : null}
        {children ? <div className={cn("text-ink", title && "mt-0.5")}>{children}</div> : null}
      </div>
    </div>
  );
}

export interface EmptyStateProps {
  title: ReactNode;
  description?: ReactNode;
  /** The next step, such as an Add button. */
  children?: ReactNode;
  className?: string;
}

export function EmptyState({ title, description, children, className }: EmptyStateProps) {
  return (
    <div className={cn("flex flex-col items-start gap-1 py-8", className)}>
      <h2 className="m-0 text-base font-semibold">{title}</h2>
      {description ? <p className="m-0 max-w-prose text-muted">{description}</p> : null}
      {children ? <div className="mt-3">{children}</div> : null}
    </div>
  );
}

export interface ErrorStateProps {
  title: ReactNode;
  message?: ReactNode;
  onRetry?: () => void;
  className?: string;
}

export function ErrorState({ title, message, onRetry, className }: ErrorStateProps) {
  const t = useT();
  return (
    <div role="alert" className={cn("flex flex-col items-start gap-1 py-8", className)}>
      <p className="m-0 flex items-center gap-2 font-semibold text-danger-ink">
        <CircleAlert aria-hidden="true" className="size-5 shrink-0" />
        {title}
      </p>
      {message ? <p className="m-0 max-w-prose text-muted">{message}</p> : null}
      {onRetry ? (
        <Button variant="secondary" className="mt-3" onClick={onRetry}>
          {t("common.retry")}
        </Button>
      ) : null}
    </div>
  );
}

/** A placeholder block for content on its way. Pair it with aria-busy on the region that will hold the content. */
export function Skeleton({ className, ...props }: ComponentProps<"div">) {
  return <div aria-hidden="true" className={cn("animate-pulse rounded-control bg-selected", className)} {...props} />;
}
