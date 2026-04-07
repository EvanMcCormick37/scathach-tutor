import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // In production (Tauri) the SPA is served directly from the webview.
  // The API base URL is injected by Tauri via window.__SCATHACH_API_PORT__.
  // In development, proxy API calls to the local uvicorn server.
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        rewrite: (path) => path.replace(/^\/api/, ""),
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
