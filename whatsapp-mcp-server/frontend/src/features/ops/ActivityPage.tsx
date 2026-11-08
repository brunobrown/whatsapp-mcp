import type { ReactNode } from "react";
import { EmptyState, ErrorState, Skeleton } from "@/components/feedback/feedback";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { useT } from "@/i18n/t";
import { formatCount, formatMoment } from "@/lib/format";
import { useOpsAudit, useOpsDrafts } from "@/services/services";
import type { AuditRow, DraftRow } from "@/types/api";
import { Cell, DataTable } from "./DataTable";

interface ListQuery<Row> {
  isPending: boolean;
  isError: boolean;
  error: { message: string } | null;
  data: { rows: Row[] } | undefined;
  refetch: () => Promise<unknown>;
}

/**
 * One list in its four states. Rows are drawn in the order the server sent them (newest first); nothing here
 * re-sorts, and there is no control that acts on a row.
 */
function Listing<Row>({
  title,
  empty,
  emptyHelp,
  query,
  columns,
  row,
}: {
  title: string;
  empty: string;
  emptyHelp?: string;
  query: ListQuery<Row>;
  columns: readonly string[];
  row: (item: Row) => ReactNode;
}) {
  const t = useT();
  let body: ReactNode;
  if (query.isPending) {
    body = (
      <div aria-busy="true" aria-label={t("activity.loading")} className="flex flex-col gap-2">
        <Skeleton className="h-10" />
        <Skeleton className="h-10" />
      </div>
    );
  } else if (query.isError) {
    body = (
      <ErrorState
        title={t("activity.loadError")}
        message={query.error?.message}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  } else if (query.data === undefined || query.data.rows.length === 0) {
    body = <EmptyState title={empty} {...(emptyHelp !== undefined && { description: emptyHelp })} />;
  } else {
    body = (
      <DataTable label={t("activity.table", { title })} columns={columns}>
        {query.data.rows.map((item, index) => (
          <tr key={index}>{row(item)}</tr>
        ))}
      </DataTable>
    );
  }
  return (
    <Card>
      <CardHeader title={title} />
      <CardBody>{body}</CardBody>
    </Card>
  );
}

function useYesNo(): (value: boolean | undefined) => string {
  const t = useT();
  return (value) => (value === undefined ? t("format.missing") : value ? t("common.yes") : t("common.no"));
}

function text(value: string | null | undefined, fallback: string): string {
  return value ? value : fallback;
}

function Drafts() {
  const t = useT();
  const query = useOpsDrafts();
  const missing = t("format.missing");
  return (
    <Listing<DraftRow>
      title={t("activity.drafts")}
      empty={t("activity.drafts.empty")}
      emptyHelp={t("activity.drafts.emptyHelp")}
      query={query}
      columns={[
        t("activity.col.id"),
        t("activity.col.profile"),
        t("activity.col.chat"),
        t("activity.col.sender"),
        t("activity.col.created"),
        t("activity.col.expires"),
      ]}
      row={(draft) => (
        <>
          <Cell>{text(draft.id, missing)}</Cell>
          <Cell>{text(draft.profile, missing)}</Cell>
          <Cell>{text(draft.chat_jid, missing)}</Cell>
          <Cell>{text(draft.sender, missing)}</Cell>
          <Cell>{formatMoment(draft.created_at)}</Cell>
          <Cell>{formatMoment(draft.expires_at)}</Cell>
        </>
      )}
    />
  );
}

function SendAudit() {
  const t = useT();
  const query = useOpsAudit("send");
  const yesNo = useYesNo();
  const missing = t("format.missing");
  return (
    <Listing<AuditRow>
      title={t("activity.send")}
      empty={t("activity.send.empty")}
      query={query}
      columns={[
        t("activity.col.when"),
        t("activity.col.kind"),
        t("activity.col.chat"),
        t("activity.col.size"),
        t("activity.col.result"),
        t("activity.col.rule"),
        t("activity.col.override"),
        t("activity.col.message"),
      ]}
      row={(entry) => (
        <>
          <Cell>{formatMoment(entry.ts)}</Cell>
          <Cell>{text(entry.kind, missing)}</Cell>
          <Cell>{text(entry.chat_name ?? entry.chat_jid, missing)}</Cell>
          <Cell>{formatCount(entry.size)}</Cell>
          <Cell>{text(entry.result, missing)}</Cell>
          <Cell>{text(entry.rule, missing)}</Cell>
          <Cell>{yesNo(entry.override)}</Cell>
          <Cell>{text(entry.message_id, missing)}</Cell>
        </>
      )}
    />
  );
}

function MonitorAudit() {
  const t = useT();
  const query = useOpsAudit("monitor");
  const yesNo = useYesNo();
  const missing = t("format.missing");
  return (
    <Listing<AuditRow>
      title={t("activity.monitor")}
      empty={t("activity.monitor.empty")}
      query={query}
      columns={[
        t("activity.col.when"),
        t("activity.col.profile"),
        t("activity.col.chat"),
        t("activity.col.sender"),
        t("activity.col.triggers"),
        t("activity.col.mode"),
        t("activity.col.approval"),
        t("activity.col.result"),
        t("activity.col.size"),
        t("activity.col.error"),
        t("activity.col.reply"),
      ]}
      row={(entry) => (
        <>
          <Cell>{formatMoment(entry.ts)}</Cell>
          <Cell>{text(entry.profile, missing)}</Cell>
          <Cell>{text(entry.chat_jid, missing)}</Cell>
          <Cell>{text(entry.sender, missing)}</Cell>
          <Cell>{entry.triggers && entry.triggers.length > 0 ? entry.triggers.join(", ") : missing}</Cell>
          <Cell>{text(entry.mode, missing)}</Cell>
          <Cell>{yesNo(entry.approval_used)}</Cell>
          <Cell>{text(entry.result, missing)}</Cell>
          <Cell>{formatCount(entry.size)}</Cell>
          <Cell>{text(entry.error_code, missing)}</Cell>
          <Cell>{text(entry.reply_id, missing)}</Cell>
        </>
      )}
    />
  );
}

/** Pending drafts and the two audit logs, read only. Message text never reaches this page. */
export function ActivityPage() {
  const t = useT();
  return (
    <div className="flex max-w-6xl flex-col gap-4">
      <p className="m-0 text-muted">{t("activity.readOnly")}</p>
      <Drafts />
      <SendAudit />
      <MonitorAudit />
    </div>
  );
}
