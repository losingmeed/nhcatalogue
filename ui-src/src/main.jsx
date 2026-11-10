import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./index.css";
import { applyTheme, cachedTheme } from "./theme.js";

// paint the last saved theme immediately; App replaces it once /api/settings
// responds (the server copy is authoritative).
applyTheme(cachedTheme(), { persist: false });

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
