import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { AppProviders } from "./app/providers";
import { createAppRouter } from "./app/router";
import { startThemeSync } from "./lib/theme";

startThemeSync();

const router = createAppRouter();

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <AppProviders router={router}>
        <RouterProvider router={router} />
      </AppProviders>
    </StrictMode>,
  );
}
