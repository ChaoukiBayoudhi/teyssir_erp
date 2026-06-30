import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// Dev proxies /api and /health to the local Django node (same-origin in prod via Caddy),
// so the browser never sees a cross-origin request and we need no CORS dependency.
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      manifest: {
        name: "Teyssir POS",
        short_name: "Teyssir",
        lang: "fr",
        dir: "auto",
        theme_color: "#1B5E20",
        background_color: "#ffffff",
        display: "standalone",
        start_url: "/",
      },
    }),
  ],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
});
