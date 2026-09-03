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
      // New npm builds emit a new SW; cleanup so upgrade clients drop old TVA UI chunks.
      workbox: {
        cacheId: "teyssir-pwa-20260903-vat-off",
        cleanupOutdatedCaches: true,
        clientsClaim: true,
        skipWaiting: true,
      },
      includeAssets: ["favicon.svg", "favicon.ico", "apple-touch-icon.png"],
      manifest: {
        name: "Teyssir — Librairie & POS",
        short_name: "Teyssir",
        description: "Point de vente, stock, achats, comptabilité et registre des livres pour la librairie Teyssir.",
        lang: "fr",
        dir: "auto",
        theme_color: "#1B5E20",
        background_color: "#ffffff",
        display: "standalone",
        start_url: "/",
        icons: [
          { src: "pwa-192x192.png", sizes: "192x192", type: "image/png" },
          { src: "pwa-512x512.png", sizes: "512x512", type: "image/png" },
          { src: "maskable-512x512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
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
