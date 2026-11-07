import { CircleCheck, Unplug } from "lucide-react";
import type { ReactNode } from "react";
import { Banner, ErrorState, Skeleton } from "@/components/feedback/feedback";
import { Badge } from "@/components/ui/badge";
import { en } from "@/i18n/en";
import type { TextKey } from "@/i18n/t";
import { useT } from "@/i18n/t";
import { formatCount, formatDuration, formatMoment } from "@/lib/format";
import { useOpsCounters, useOpsStatus } from "@/services/services";
import type { BridgeStatus, OpsCounters, Unavailable } from "@/types/api";
import { Cell, DataTable } from "./DataTable";

function isUnavailable(value: object): value is Unavailable {
  return "unavailable" in value;
}

/** The sentence for a reason the server gave; a reason this version does not know shows the server's own words. */
function useExplain(): (reason: Unavailable) => string {
  const t = useT();
  return (reason) => {
    const key = `status.unavailable.${reason.unavailable}`;
    return key in en ? t(key as TextKey) : reason.message;
  };
}

function UnavailableNotice({ reason }: { reason: Unavailable }) {
  const t = useT();
  const explain = useExplain();
  return (
    <Banner kind="warning" title={t("status.unavailable.title")}>
      {explain(reason)}
    </Banner>
  );
}

function Loading({ label }: { label: string }) {
  return (
    <div aria-busy="true" aria-label={label} className="flex flex-col gap-3">
      <Skeleton className="h-10 w-1/3" />
      <Skeleton className="h-24" />
    </div>
  );
}

function Facts({ children }: { children: ReactNode }) {
  return <dl className="m-0 grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">{children}</dl>;
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-sm text-muted">{label}</dt>
      <dd className="m-0 font-medium break-words">{children}</dd>
    </div>
  );
}

function Connection({ status }: { status: BridgeStatus }) {
  const t = useT();
  const connected = status.connected === true;
  return (
    <div className="flex flex-col gap-4">
      <p className="m-0 flex flex-wrap items-center gap-2">
        {connected ? (
          <Badge variant="accent" className="px-3 py-1 text-sm">
            <CircleCheck aria-hidden="true" className="size-4" />
            {t("status.connected")}
          </Badge>
        ) : (
          <Badge variant="danger" className="px-3 py-1 text-sm">
            <Unplug aria-hidden="true" className="size-4" />
            {t("status.disconnected")}
          </Badge>
        )}
        <Badge variant={status.logged_in === true ? "accent" : "neutral"} className="px-3 py-1 text-sm">
          {status.logged_in === true ? t("status.paired") : t("status.notPaired")}
        </Badge>
      </p>
      <Facts>
        <Fact label={t("status.phone")}>{status.phone || t("format.missing")}</Fact>
        <Fact label={t("status.version")}>{status.version || t("format.missing")}</Fact>
        <Fact label={t("status.uptime")}>{formatDuration(status.uptime_seconds)}</Fact>
        <Fact label={t("status.startedAt")}>{formatMoment(status.started_at)}</Fact>
        <Fact label={t("status.lastEvent")}>{formatMoment(status.last_event_at)}</Fact>
        <Fact label={t("status.lastMessage")}>{formatMoment(status.last_message_at)}</Fact>
        <Fact label={t("status.lastConnected")}>{formatMoment(status.last_connected_at)}</Fact>
        <Fact label={t("status.lastDisconnected")}>{formatMoment(status.last_disconnected_at)}</Fact>
        <Fact label={t("status.mediaRetries")}>{formatCount(status.pending_media_retries)}</Fact>
        <Fact label={t("status.webhooks")}>
          {status.webhooks === undefined
            ? t("format.missing")
            : status.webhooks.enabled
              ? t("status.webhooks.on")
              : t("status.webhooks.off")}
        </Fact>
      </Facts>
      {status.webhooks?.error ? <Banner kind="warning">{status.webhooks.error}</Banner> : null}
      {status.webhooks && status.webhooks.endpoints.length > 0 ? (
        <DataTable
          label={t("status.webhooks.endpoints")}
          columns={[
            t("status.endpoint.name"),
            t("status.endpoint.delivered"),
            t("status.endpoint.failed"),
            t("status.endpoint.dropped"),
            t("status.endpoint.queued"),
            t("status.endpoint.lastSuccess"),
            t("status.endpoint.lastError"),
          ]}
        >
          {status.webhooks.endpoints.map((endpoint, index) => (
            <tr key={`${endpoint.name ?? ""}-${String(index)}`}>
              <Cell>{endpoint.name || t("format.missing")}</Cell>
              <Cell>{formatCount(endpoint.delivered)}</Cell>
              <Cell>{formatCount(endpoint.failed)}</Cell>
              <Cell>{formatCount(endpoint.dropped)}</Cell>
              <Cell>{formatCount(endpoint.queued)}</Cell>
              <Cell>{formatMoment(endpoint.last_success_at)}</Cell>
              <Cell className="whitespace-normal">{endpoint.last_error || t("format.missing")}</Cell>
            </tr>
          ))}
        </DataTable>
      ) : null}
    </div>
  );
}

function ConnectionSection() {
  const t = useT();
  const query = useOpsStatus();
  let body: ReactNode;
  if (query.isPending) body = <Loading label={t("status.loading")} />;
  else if (query.isError)
    body = (
      <ErrorState
        title={t("status.loadError")}
        message={query.error.message}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  else if (isUnavailable(query.data)) {
    body = (
      <div className="flex flex-col gap-3">
        <Badge variant="neutral" className="w-fit px-3 py-1 text-sm">
          <Unplug aria-hidden="true" className="size-4" />
          {t("status.disconnected")}
        </Badge>
        <UnavailableNotice reason={query.data} />
      </div>
    );
  } else body = <Connection status={query.data.status} />;
  return (
    <section className="flex flex-col gap-3">
      <h2 id="status-connection" className="m-0 text-base font-semibold">
        {t("status.connection")}
      </h2>
      {body}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: number | undefined }) {
  return (
    <div className="rounded-control border border-line bg-panel px-4 py-3">
      <dt className="text-sm text-muted">{label}</dt>
      <dd className="m-0 text-xl font-semibold">{formatCount(value)}</dd>
    </div>
  );
}

function Counters({ counters }: { counters: OpsCounters }) {
  const t = useT();
  const hooks = counters.webhooks;
  const sends = Object.entries(counters.sends.by_result);
  const replies = Object.entries(counters.monitor_replies.by_profile).flatMap(([profile, results]) =>
    Object.entries(results).map(([result, count]) => ({ profile, result, count })),
  );
  const errors = Object.entries(counters.monitor_replies.error_codes);
  return (
    <div className="flex flex-col gap-4">
      <p className="m-0 text-sm text-muted">{t("status.counters.window", { since: formatMoment(counters.since) })}</p>
      <dl className="m-0 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label={t("status.counters.received")} value={counters.messages?.received} />
        <Stat label={t("status.counters.fromThisAccount")} value={counters.messages?.from_this_account} />
        <Stat label={t("status.counters.withOverride")} value={counters.sends.with_override} />
        <Stat label={t("status.counters.pendingDrafts")} value={counters.pending_drafts} />
        <Stat label={t("status.counters.delivered")} value={hooks?.delivered} />
        <Stat label={t("status.counters.failed")} value={hooks?.failed} />
        <Stat label={t("status.counters.dropped")} value={hooks?.dropped} />
        <Stat label={t("status.counters.queued")} value={hooks?.queued} />
      </dl>
      {sends.length > 0 ? (
        <DataTable label={t("status.counters.sends")} columns={[t("status.counters.result"), t("status.counters.count")]}>
          {sends.map(([result, count]) => (
            <tr key={result}>
              <Cell>{result}</Cell>
              <Cell>{formatCount(count)}</Cell>
            </tr>
          ))}
        </DataTable>
      ) : null}
      {replies.length > 0 ? (
        <DataTable
          label={t("status.counters.replies")}
          columns={[t("status.counters.profile"), t("status.counters.result"), t("status.counters.count")]}
        >
          {replies.map((row) => (
            <tr key={`${row.profile}/${row.result}`}>
              <Cell>{row.profile}</Cell>
              <Cell>{row.result}</Cell>
              <Cell>{formatCount(row.count)}</Cell>
            </tr>
          ))}
        </DataTable>
      ) : null}
      {errors.length > 0 ? (
        <DataTable label={t("status.counters.errors")} columns={[t("status.counters.code"), t("status.counters.count")]}>
          {errors.map(([code, count]) => (
            <tr key={code}>
              <Cell>{code}</Cell>
              <Cell>{formatCount(count)}</Cell>
            </tr>
          ))}
        </DataTable>
      ) : null}
    </div>
  );
}

function CountersSection() {
  const t = useT();
  const query = useOpsCounters();
  let body: ReactNode;
  if (query.isPending) body = <Loading label={t("status.loading")} />;
  else if (query.isError)
    body = (
      <ErrorState
        title={t("status.loadError")}
        message={query.error.message}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  else if (isUnavailable(query.data)) body = <UnavailableNotice reason={query.data} />;
  else body = <Counters counters={query.data} />;
  return (
    <section className="flex flex-col gap-3">
      <h2 id="status-counters" className="m-0 text-base font-semibold">
        {t("status.counters")}
      </h2>
      {body}
    </section>
  );
}

/** The bridge's state and the counters, refreshed by the hooks while the tab is visible. */
export function StatusPage() {
  return (
    <div className="flex flex-col gap-8">
      <ConnectionSection />
      <CountersSection />
    </div>
  );
}
