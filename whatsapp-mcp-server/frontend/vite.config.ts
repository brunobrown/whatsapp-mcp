import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

const uiPort = process.env["WHATSAPP_UI_PORT"] ?? "8765";

// The dev server forwards /api to the UI process. No CORS headers are set:
// the browser only ever talks to the Vite origin.
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
    proxy: {
      "/api": { target: `http://127.0.0.1:${uiPort}`, changeOrigin: false },
    },
  },
});
