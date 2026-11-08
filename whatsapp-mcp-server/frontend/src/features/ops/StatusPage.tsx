import { CircleCheck, Unplug } from "lucide-react";
import { useEffect } from "react";
import type { ReactNode } from "react";
import { Banner, ErrorState, Skeleton } from "@/components/feedback/feedback";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { en } from "@/i18n/en";
import type { TextKey } from "@/i18n/t";
import { useT } from "@/i18n/t";
import { formatCount, formatDuration } from "@/lib/format";
import { raiseNotice } from "@/lib/notices";
import { useOpsCounters, useOpsStatus } from "@/services/services";
import type { BridgeStatus, OpsCounters, Unavailable } from "@/types/api";
import { Cell, DataTable } from "./DataTable";
import { Freshness } from "./Freshness";
import { Moment } from "./Moment";

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

/** `titled` is false in the Counters card, so the page names the bridge's trouble once. */
function UnavailableNotice({ reason, titled = true }: { reason: Unavailable; titled?: boolean }) {
  const t = useT();
  const explain = useExplain();
  return (
    <Banner kind="warning" {...(titled && { title: t("status.unavailable.title") })}>
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
        <Fact label={t("status.startedAt")}><Moment value={status.started_at} /></Fact>
        <Fact label={t("status.lastEvent")}><Moment value={status.last_event_at} /></Fact>
        <Fact label={t("status.lastMessage")}><Moment value={status.last_message_at} /></Fact>
        <Fact label={t("status.lastConnected")}><Moment value={status.last_connected_at} /></Fact>
        <Fact label={t("status.lastDisconnected")}><Moment value={status.last_disconnected_at} /></Fact>
        <Fact label={t("status.mediaRetries")}>{formatCount(status.pending_media_retries)}</Fact>
      </Facts>
    </div>
  );
}

function ConnectionCard() {
  const t = useT();
  const query = useOpsStatus();
  const gone = query.data !== undefined && isUnavailable(query.data);
  // The bridge going away is a global event: one notification, not one per refresh.
  useEffect(() => {
    if (gone) raiseNotice("bridgeUnavailable");
  }, [gone]);
  let body: ReactNode;
  if (query.isPending) body = <Loading label={t("status.loading")} />;
  else if (query.isError && query.data === undefined)
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
    <Card>
      <CardHeader title={t("status.connection")} />
      <CardBody>{body}</CardBody>
    </Card>
  );
}

function Webhooks({ webhooks }: { webhooks: NonNullable<BridgeStatus["webhooks"]> }) {
  const t = useT();
  return (
    <div className="flex flex-col gap-4">
      <p className="m-0">
        <Badge variant={webhooks.enabled ? "accent" : "neutral"} className="px-3 py-1 text-sm">
          {webhooks.enabled ? t("status.webhooks.on") : t("status.webhooks.off")}
        </Badge>
      </p>
      {webhooks.error ? <Banner kind="warning">{webhooks.error}</Banner> : null}
      {webhooks.endpoints.length > 0 ? (
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
          {webhooks.endpoints.map((endpoint, index) => (
            <tr key={`${endpoint.name ?? ""}-${String(index)}`}>
              <Cell>{endpoint.name || t("format.missing")}</Cell>
              <Cell>{formatCount(endpoint.delivered)}</Cell>
              <Cell>{formatCount(endpoint.failed)}</Cell>
              <Cell>{formatCount(endpoint.dropped)}</Cell>
              <Cell>{formatCount(endpoint.queued)}</Cell>
              <Cell><Moment value={endpoint.last_success_at} /></Cell>
              <Cell className="whitespace-normal">{endpoint.last_error || t("format.missing")}</Cell>
            </tr>
          ))}
        </DataTable>
      ) : null}
    </div>
  );
}

function WebhooksCard() {
  const t = useT();
  const query = useOpsStatus();
  let body: ReactNode;
  if (query.isPending) body = <Loading label={t("status.loading")} />;
  else if (query.data === undefined || isUnavailable(query.data)) body = <p className="m-0 text-muted">{t("status.webhooks.unknown")}</p>;
  else if (query.data.status.webhooks === undefined) body = <p className="m-0 text-muted">{t("format.missing")}</p>;
  else body = <Webhooks webhooks={query.data.status.webhooks} />;
  return (
    <Card>
      <CardHeader title={t("status.webhooks")} />
      <CardBody>{body}</CardBody>
    </Card>
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
      <p className="m-0 text-sm text-muted">{t("status.counters.window")} <Moment value={counters.since} /></p>
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

function CountersCard({ className }: { className?: string }) {
  const t = useT();
  const query = useOpsCounters();
  let body: ReactNode;
  if (query.isPending) body = <Loading label={t("status.loading")} />;
  else if (query.isError && query.data === undefined)
    body = (
      <ErrorState
        title={t("status.loadError")}
        message={query.error.message}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  else if (isUnavailable(query.data)) body = <UnavailableNotice reason={query.data} titled={false} />;
  else body = <Counters counters={query.data} />;
  return (
    <Card {...(className !== undefined && { className })}>
      <CardHeader title={t("status.counters")} />
      <CardBody>{body}</CardBody>
    </Card>
  );
}

/** The bridge's state and the counters, refreshed by the hooks while the tab is visible. */
export function StatusPage() {
  const status = useOpsStatus();
  const counters = useOpsCounters();
  return (
    <div className="flex flex-col gap-4">
      <Freshness queries={[status, counters]} />
      <div className="grid grid-cols-1 items-start gap-4 min-[1100px]:grid-cols-2">
        <ConnectionCard />
        <WebhooksCard />
        <CountersCard className="min-[1100px]:col-span-2" />
      </div>
    </div>
  );
}
