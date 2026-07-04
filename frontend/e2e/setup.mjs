/** Fresh e2e environment: a demo target repo (5 specs, 2 conflicts) and a
 * SPINDLEGRAPH_HOME whose config points claude_bin at the fake CLI — the
 * whole suite runs offline with zero credentials.
 *
 * Runs as the first half of the Playwright webServer command (NOT as
 * globalSetup: Playwright starts the web server before globalSetup, and this
 * must run before uvicorn creates its database). */
import { execSync } from 'child_process'
import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'

const here = path.dirname(fileURLToPath(import.meta.url))
const HOME = path.join(here, '..', '.e2e-home')
const DEMO = path.join(HOME, 'demo-repo')

const SPECS = {
  '0007-add-rate-limiting.md': `---
title: Add rate limiting to the public API
status: decided
---

# Add rate limiting to the public API

## Affected files
- \`src/api/middleware.py\` — add limiter
- \`src/config.py\` — settings

## Decisions needed
- [x] Algorithm? → token bucket
`,
  '0009-fix-login-redirect.md': `---
title: Fix login redirect loop
status: decided
---

# Fix login redirect loop

## Affected files
- \`src/auth_views.py\`
`,
  '0012-settings-loader.md': `---
title: Refactor settings loader
status: draft
---

# Refactor settings loader

## Affected files
- \`src/config.py\`
- \`src/settings_loader.py\`

## Decisions needed
- [ ] Keep env-var overrides?
`,
  '0014-dark-mode.md': `---
title: Dark mode toggle
status: decided
---

# Dark mode toggle

## Affected files
- \`web/theme.ts\`
`,
  '0015-audit-logging.md': `---
title: Audit logging for API mutations
status: decided
---

# Audit logging for API mutations

## Affected files
- \`src/api/middleware.py\` — emit events
- \`src/audit_logger.py\` — new
`,
}

fs.rmSync(HOME, { recursive: true, force: true })
fs.mkdirSync(path.join(DEMO, 'specs'), { recursive: true })
for (const dir of ['src/api', 'src/settings', 'web']) {
  fs.mkdirSync(path.join(DEMO, dir), { recursive: true })
}
for (const f of ['src/api/middleware.py', 'src/config.py', 'src/auth_views.py',
                 'src/settings_loader.py', 'src/audit_logger.py', 'web/theme.ts']) {
  fs.writeFileSync(path.join(DEMO, f), '# stub\n')
}
for (const [name, body] of Object.entries(SPECS)) {
  fs.writeFileSync(path.join(DEMO, 'specs', name), body)
}
const git = (cmd) => execSync(`git ${cmd}`, { cwd: DEMO, stdio: 'pipe' })
git('init -q -b main')
git('config user.email e2e@example.com')
git('config user.name E2E')
git('config commit.gpgsign false')
git('add -A')
git('commit -q -m init')

const py = path.join(here, '..', '..', 'backend', '.venv', 'Scripts', 'python.exe')
const fake = path.join(here, '..', '..', 'backend', 'tests', 'fake_claude.py')
fs.writeFileSync(path.join(HOME, 'config.json'), JSON.stringify({
  claude_bin: `${py.replaceAll('\\', '/')} ${fake.replaceAll('\\', '/')}`,
  max_parallel: 3,
  job_timeout_min: 3,
}, null, 2))
console.log('e2e env ready:', HOME)
