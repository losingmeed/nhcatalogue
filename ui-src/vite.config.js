import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Build output goes into the Python package so the FastAPI server serves it.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../src/nhcatalogue/ui/dist",
    emptyOutDir: true,
  },
  server: {
    // dev proxy: run `nhcatalogue serve` separately, paste its token below
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8787",
        changeOrigin: false,
        headers: { "X-NHC-Token": process.env.NHC_TOKEN || "" },
      },
    },
  },
});
