/** The SPEC §15 demo path, driven through the real UI in a real browser
 * against a real backend + git repo (fake claude CLI). Serial: each test
 * builds on server state from the previous one. */
import { expect, test, type Page } from '@playwright/test'
import path from 'path'
import { fileURLToPath } from 'url'
import { DEMO } from './env'

const here = path.dirname(fileURLToPath(import.meta.url))
const shots = path.join(here, '..', 'e2e-artifacts')
const shot = (name: string) => path.join(shots, `${name}.png`)

test.describe.configure({ mode: 'serial' })

async function gotoTab(page: Page, tab: string) {
  await page.getByRole('navigation').getByRole('button', { name: tab }).click()
}

test('onboard a project from the add screen', async ({ page }) => {
  await page.goto('/')
  await page.getByPlaceholder(/Projects.my-app/).fill(DEMO)
  await page.getByRole('button', { name: 'Add project' }).click()
  await expect(page.getByText(/Spec board · 5 specs/)).toBeVisible({ timeout: 15_000 })
  await page.screenshot({ path: shot('01-board') })
})

test('board shows parsed specs; drawer resolves a decision', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.card')).toHaveCount(5)
  // risk chip parsed from the new template's ## Risk section
  await expect(page.locator('.card', { hasText: 'rate limiting' }).locator('.riskchip'))
    .toHaveText(/Moderate · High review/)
  // 0012 has an unresolved decision
  await page.locator('.card', { hasText: 'Refactor settings loader' }).click()
  const drawer = page.locator('.drawer')
  // scope to the decision row — the same text also appears in the raw markdown pane
  await expect(drawer.locator('.decision').getByText('Keep env-var overrides?')).toBeVisible()
  await expect(drawer.getByRole('button', { name: '▶ Build' })).toBeDisabled()
  page.once('dialog', (d) => d.accept('keep them'))
  await drawer.getByRole('button', { name: 'Resolve' }).click()
  await expect(drawer.locator('.decision')).toContainText('→ keep them', { timeout: 10_000 })
  await expect(drawer.getByRole('button', { name: '▶ Build' })).toBeEnabled()
  await page.screenshot({ path: shot('02-drawer') })
})

test('graph renders clusters and conflict edges', async ({ page }) => {
  await page.goto('/')
  await gotoTab(page, 'Graph')
  await expect(page.locator('.sgnode')).toHaveCount(5)
  // two conflict edges, labeled with shared file counts
  await expect(page.locator('.edgelabel')).toHaveCount(2)
  // wave-lane layout: recommended order rendered as labeled columns, and
  // no two spec nodes may overlap
  await expect(page.locator('.lanelabel')).toHaveCount(2)
  await expect(page.locator('.lanelabel').first()).toHaveText(/Wave 1/)
  const boxes = []
  for (const n of await page.locator('.react-flow__node:not([data-id^="lane-"])').all()) {
    const b = await n.boundingBox()
    if (b) boxes.push(b)
  }
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j]
      const overlap = a.x < b.x + b.width && b.x < a.x + a.width
        && a.y < b.y + b.height && b.y < a.y + a.height
      expect(overlap, `nodes ${i} and ${j} overlap`).toBe(false)
    }
  }
  await page.screenshot({ path: shot('03-graph') })
})

test('nodes are draggable', async ({ page }) => {
  await page.goto('/')
  await gotoTab(page, 'Graph')
  const node = page.locator('.react-flow__node', { hasText: 'dark-mode' })
  await expect(node).toBeVisible()
  const before = await node.boundingBox()
  await node.hover()
  await page.mouse.down()
  await page.mouse.move(before!.x + 160, before!.y + 90, { steps: 8 })
  await page.mouse.up()
  const after = await node.boundingBox()
  expect(Math.hypot(after!.x - before!.x, after!.y - before!.y)).toBeGreaterThan(60)
})

test('selecting conflicting specs shows colored waves', async ({ page }) => {
  await page.goto('/')
  await gotoTab(page, 'Graph')
  for (const slug of ['add-rate-limiting', 'fix-login-redirect', 'dark-mode', 'audit-logging']) {
    await page.locator('.sgnode', { hasText: slug }).click()
  }
  const composer = page.locator('.composer')
  await expect(composer.getByText(/share/)).toBeVisible()           // conflict note
  await expect(composer.getByText('Wave 1')).toBeVisible()
  await expect(composer.getByText('Wave 2')).toBeVisible()
  // wave membership is painted on the nodes: 0007 conflicts with 0015,
  // so they land in different waves
  await expect(page.locator('.sgnode[data-wave]')).toHaveCount(4)
  const w7 = await page.locator('.sgnode', { hasText: 'add-rate-limiting' })
    .getAttribute('data-wave')
  const w15 = await page.locator('.sgnode', { hasText: 'audit-logging' })
    .getAttribute('data-wave')
  expect(w7).not.toBeNull()
  expect(w15).not.toBeNull()
  expect(w7).not.toBe(w15)
  await expect(page.locator('.sgnode .wavetag').first()).toBeVisible()
  await page.screenshot({ path: shot('04-waves') })
})

test('launch batch: waves build, logs stream, specs flip to built', async ({ page }) => {
  await page.goto('/')
  await gotoTab(page, 'Graph')
  for (const slug of ['add-rate-limiting', 'fix-login-redirect', 'dark-mode', 'audit-logging']) {
    await page.locator('.sgnode', { hasText: slug }).click()
  }
  await page.getByRole('button', { name: /Launch batch · 2 waves/ }).click()

  await gotoTab(page, 'Runner')
  const batchRow = page.locator('.jobrow', { hasText: 'build_batch' }).first()
  await expect(batchRow).toBeVisible({ timeout: 15_000 })
  await expect(batchRow.locator('.pill.succeeded')).toBeVisible({ timeout: 90_000 })

  // open a child build job and check its streamed log reached the result line
  await page.locator('.jobrow', { hasText: 'build' }).nth(1).click()
  await expect(page.locator('.logpane .ev.result')).toContainText(/done|pull/,
    { timeout: 15_000 })
  await expect(page.locator('.logpane')).toContainText('session started')
  await page.screenshot({ path: shot('05-runner') })

  await gotoTab(page, 'Board')
  await expect(page.locator('.card .pill.built')).toHaveCount(4, { timeout: 15_000 })
  await expect(page.locator('.card').filter({ hasText: 'PR ↗' })).toHaveCount(4)
  await page.screenshot({ path: shot('06-built') })
})

test('executor roster shows calibration from the batch', async ({ page }) => {
  await page.goto('/')
  await gotoTab(page, 'Config')
  const row = page.locator('table.executors tbody tr').first()
  await expect(row).toContainText(/4W \/ 0L/)
  await expect(row).toContainText('$1.23')
  await page.screenshot({ path: shot('07-config') })
})
