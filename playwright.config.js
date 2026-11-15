const { defineConfig, devices } = require('@playwright/test');
const port = Number(process.env.E2E_PORT || 19421);
const baseURL = `http://127.0.0.1:${port}`;
module.exports = defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  workers: 2,
  timeout: 30000,
  expect: { timeout: 7000 },
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [
    ['list'],
    ['html', { open: 'never' }],
    ['junit', { outputFile: 'reports/playwright/junit.xml', includeProjectInTestName: true }],
  ],
  use: { baseURL, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: {
    command: `${process.env.PYTHON || '.venv/bin/python'} tests/e2e_server.py`,
    url: baseURL + '/api/me',
    reuseExistingServer: false,
    timeout: 30000,
  },
  projects: [
    { name: 'desktop-chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'desktop-firefox', use: { ...devices['Desktop Firefox'] } },
    { name: 'mobile-webkit', use: { ...devices['iPhone 13'] } },
    { name: 'mobile-chromium', use: { ...devices['Pixel 7'] } },
  ],
});
