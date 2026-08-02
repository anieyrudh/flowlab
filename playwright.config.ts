import { defineConfig } from "@playwright/test";

const testPython = process.env.FLOWLAB_TEST_PYTHON ?? (process.platform === "win32" ? "python" : "python3");
const frontendPort = Number(process.env.FLOWLAB_E2E_FRONTEND_PORT ?? 5173);
const backendPort = Number(process.env.FLOWLAB_E2E_BACKEND_PORT ?? 8787);

export default defineConfig({
  testDir: "tests/e2e",
  timeout: process.env.CI ? 60_000 : 30_000,
  workers: 1,
  expect: { timeout: 5_000 },
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure"
  },
  webServer: [
    {
      command: `${testPython} -m uvicorn server.app:app --host 127.0.0.1 --port ${backendPort}`,
      url: `http://127.0.0.1:${backendPort}/api/health`,
      // Reusing a stray dev server silently tests a different checkout.
    reuseExistingServer: false,
      timeout: 20_000
    },
    {
      command: `npm run dev -- --port ${frontendPort}`,
      url: `http://127.0.0.1:${frontendPort}`,
      // Reusing a stray dev server silently tests a different checkout.
    reuseExistingServer: false,
      timeout: 20_000
    }
  ],
  projects: [{ name: "desktop", use: { viewport: { width: 1440, height: 900 } } }]
});
