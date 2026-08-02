import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const frontendPort = Number(process.env.FLOWLAB_E2E_FRONTEND_PORT ?? 5173);
const backendPort = Number(process.env.FLOWLAB_E2E_BACKEND_PORT ?? 8787);

export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 550,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("/node_modules/three/")) return "vendor-three";
        }
      }
    }
  },
  server: {
    port: frontendPort,
    proxy: {
      "/api": `http://127.0.0.1:${backendPort}`
    }
  },
  test: {
    exclude: ["e2e/**", "tests/e2e/**", ".claude/**", "node_modules/**", "dist/**"],
    environment: "jsdom",
    globals: true
  }
});
