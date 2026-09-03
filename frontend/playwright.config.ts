import { defineConfig, devices } from "@playwright/test";
import { tmpdir } from "node:os";
import { join } from "node:path";

const e2eDatabase = join(tmpdir(), `riffloom-e2e-${process.pid}.db`);
const e2eAssets = join(tmpdir(), `riffloom-e2e-assets-${process.pid}`);
const apiPort = process.env.RIFFLOOM_E2E_API_PORT ?? "8110";
const webPort = process.env.RIFFLOOM_E2E_WEB_PORT ?? "3110";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    permissions: ["clipboard-read", "clipboard-write"],
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: `RIFFLOOM_DATABASE_URL=sqlite:///${e2eDatabase} RIFFLOOM_ASSET_STORAGE_DIR=${e2eAssets} RIFFLOOM_CORS_ORIGINS=http://127.0.0.1:${webPort},http://localhost:${webPort} RIFFLOOM_MODEL_PROVIDER=mock-v1 RIFFLOOM_WORKER_STEP_DELAY=0.05 ../backend/.venv/bin/python -m uvicorn app.main:app --app-dir ../backend --host 127.0.0.1 --port ${apiPort}`,
      url: `http://127.0.0.1:${apiPort}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `RIFFLOOM_NEXT_DIST_DIR=.next-e2e RIFFLOOM_API_BASE_URL=http://127.0.0.1:${apiPort}/api/v1 RIFFLOOM_DEMO_USER=user_admin npm run dev -- --port ${webPort}`,
      url: `http://127.0.0.1:${webPort}/chat`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
  projects: [
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "narrow-desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 768, height: 960 } } },
  ],
});
