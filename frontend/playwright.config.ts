import { defineConfig } from '@playwright/test'
import path from 'path'
import { fileURLToPath } from 'url'
import { HOME, PY } from './e2e/env'

const here = path.dirname(fileURLToPath(import.meta.url))
const setup = path.join(here, 'e2e', 'setup.mjs')

export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  fullyParallel: false,
  workers: 1,
  outputDir: './e2e-artifacts/results',
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:8788',
    screenshot: 'only-on-failure',
    viewport: { width: 1440, height: 900 },
  },
  webServer: {
    // setup.mjs must run before uvicorn creates its DB — do NOT move it to
    // globalSetup (Playwright starts the web server before globalSetup runs)
    command: `node "${setup}" && "${PY}" -m uvicorn spindlegraph.main:app --port 8788`,
    cwd: path.join(here, '..', 'backend'),
    env: { SPINDLEGRAPH_HOME: HOME },
    url: 'http://127.0.0.1:8788/api/health',
    reuseExistingServer: false,
    timeout: 30_000,
  },
})
