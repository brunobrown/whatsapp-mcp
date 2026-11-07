import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig, type ProxyOptions } from "vite";

const uiPort = process.env["WHATSAPP_UI_PORT"] ?? "8765";

/**
 * The dev server forwards /api to the UI process. The API only serves the Host of its own bind address and refuses a
 * write whose Origin differs from that Host, so the proxy sends Host (changeOrigin) and Origin of the target, never the
 * Vite ones. No CORS headers are set: the browser only ever talks to the Vite origin.
 */
export function apiProxy(port: string): ProxyOptions {
  const target = `http://127.0.0.1:${port}`;
  return {
    target,
    changeOrigin: true,
    configure: (proxy) => {
      proxy.on("proxyReq", (request) => {
        if (request.getHeader("origin") !== undefined) request.setHeader("origin", target);
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: "../ui/web",
    emptyOutDir: true,
  },
  server: {
    proxy: { "/api": apiProxy(uiPort) },
  },
});
