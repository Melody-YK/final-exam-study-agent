import { defineConfig } from '@playwright/test'

const baseURL = 'http://127.0.0.1:4173'

export default defineConfig({
  testDir: '.',
  testMatch: /.*\.spec\.ts/,
  fullyParallel: false,
  timeout: 30_000,
  expect: { timeout: 8_000 },
  outputDir: '../../.local/evidence/playwright/artifacts',
  reporter: [
    ['list'],
    ['json', { outputFile: '../../.local/evidence/playwright/results.json' }],
  ],
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  webServer: {
    command: 'npm run dev --workspace @study-agent/web -- --host 127.0.0.1 --port 4173',
    cwd: '../..',
    url: baseURL,
    reuseExistingServer: true,
    timeout: 60_000,
  },
  projects: [
    { name: 'chromium-desktop', use: { browserName: 'chromium', viewport: { width: 1440, height: 900 } } },
    { name: 'chromium-mobile', use: { browserName: 'chromium', viewport: { width: 390, height: 844 }, hasTouch: true } },
    { name: 'webkit-desktop', use: { browserName: 'webkit', viewport: { width: 1280, height: 800 } } },
    { name: 'webkit-mobile', use: { browserName: 'webkit', viewport: { width: 390, height: 844 }, hasTouch: true } },
  ],
})
