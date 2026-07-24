import { defineConfig } from "@playwright/test";

const testPython = process.env.FLOWLAB_TEST_PYTHON ?? (process.platform === "win32" ? "python" : "python3");

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 30_000,
  workers: 1,
  expect: { timeout: 5_000 },
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure"
  },
  webServer: [
    {
      command: `${testPython} -m uvicorn server.app:app --host 127.0.0.1 --port 8787`,
      url: "http://127.0.0.1:8787/api/health",
      reuseExistingServer: true,
      timeout: 20_000
    },
    {
      command: "npm run dev -- --port 5173",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: true,
      timeout: 20_000
    }
  ],
  projects: [{ name: "desktop", use: { viewport: { width: 1440, height: 900 } } }]
});
