import { defineConfig, devices } from '@playwright/test';
export default defineConfig({
  testDir: './tests',
  testMatch: '**/*.spec.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 45000,
  expect: { timeout: 10000 },
  use: {
    baseURL: 'http://127.0.0.1:3100',
    trace: 'retain-on-failure',
    ...devices['Desktop Chrome'],
    viewport: { width: 1440, height: 960 },
    launchOptions: {
      args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'],
    },
  },
  webServer: [
    {
      command: 'node tests/fixture-server.mjs',
      url: 'http://127.0.0.1:8100/health',
      reuseExistingServer: false,
    },
    {
      command: 'npm run dev -- --port 3100',
      url: 'http://127.0.0.1:3100',
      env: { API_ORIGIN: 'http://127.0.0.1:8100', KLACK_E2E: '1' },
      reuseExistingServer: false,
      timeout: 120000,
    },
  ],
});
