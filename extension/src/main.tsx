import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { LiveProvider } from "./hooks/useLive";
import { SettingsProvider } from "./hooks/useSettings";
import "./styles/global.css";

const container = document.getElementById("root");
if (container) {
  createRoot(container).render(
    <React.StrictMode>
      <SettingsProvider>
        <LiveProvider>
          <App />
        </LiveProvider>
      </SettingsProvider>
    </React.StrictMode>,
  );
}
