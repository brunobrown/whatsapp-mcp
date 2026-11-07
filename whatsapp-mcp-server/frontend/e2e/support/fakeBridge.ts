import { createServer } from "node:http";
import type { IncomingMessage, Server, ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";

/** How the fake bridge answers `GET /api/status`. `down` closes the socket so the caller gets a refused connection. */
export type BridgeMode = "connected" | "disconnected" | "unauthorized" | "unsupported" | "unexpected" | "down";

export interface WebhookVerdict {
  ok: boolean;
  error?: string;
}

const CONNECTED_STATUS = {
  connected: true,
  logged_in: true,
  phone: "5500000000000",
  version: "0.0.0-test",
  started_at: "2026-01-02T03:04:05Z",
  uptime_seconds: 7500,
  last_event_at: "2026-01-02T05:00:00Z",
  last_message_at: "2026-01-02T04:59:00Z",
  last_connected_at: "2026-01-02T03:04:10Z",
  last_disconnected_at: null,
  pending_media_retries: 2,
  webhooks: {
    enabled: true,
    endpoints: [{ name: "hook-a", delivered: 12, failed: 1, dropped: 0, queued: 3, last_success_at: "2026-01-02T04:00:00Z" }],
  },
};

/**
 * A minimal stand-in for the bridge's local API: the status the interface polls and the webhooks check it asks for.
 * It wants the same bearer token as the real one, which the harness writes to the configuration directory.
 */
export class FakeBridge {
  mode: BridgeMode = "connected";
  verdict: WebhookVerdict = { ok: true };
  readonly requests: string[] = [];
  private server: Server | undefined;
  private port = 0;

  constructor(
    private readonly token: string,
    port = 0,
  ) {
    this.port = port;
  }

  get url(): string {
    return `http://127.0.0.1:${String(this.port)}/api`;
  }

  async start(): Promise<void> {
    const server = createServer((request, response) => {
      this.handle(request, response);
    });
    this.server = server;
    await new Promise<void>((resolve) => server.listen(this.port, "127.0.0.1", resolve));
    this.port = (server.address() as AddressInfo).port;
  }

  async stop(): Promise<void> {
    const server = this.server;
    this.server = undefined;
    if (server === undefined) return;
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => {
      resolve();
    }));
  }

  private handle(request: IncomingMessage, response: ServerResponse): void {
    const path = request.url ?? "";
    this.requests.push(`${request.method ?? "GET"} ${path}`);
    if (this.mode === "down") {
      request.socket.destroy();
      return;
    }
    if (request.headers.authorization !== `Bearer ${this.token}` || this.mode === "unauthorized") {
      respond(response, 401, { error: "unauthorized" });
      return;
    }
    if (this.mode === "unsupported") {
      respond(response, 404, { error: "not found" });
      return;
    }
    if (request.method === "GET" && path === "/api/status") {
      if (this.mode === "unexpected") {
        response.writeHead(200, { "Content-Type": "text/plain" }).end("not json");
        return;
      }
      respond(response, 200, this.mode === "connected" ? CONNECTED_STATUS : { connected: false, logged_in: false });
      return;
    }
    if (request.method === "POST" && path === "/api/config/validate-webhooks") {
      request.resume();
      request.on("end", () => {
        respond(response, 200, this.verdict);
      });
      return;
    }
    respond(response, 404, { error: "not found" });
  }
}

function respond(response: ServerResponse, status: number, body: unknown): void {
  response.writeHead(status, { "Content-Type": "application/json" }).end(JSON.stringify(body));
}
