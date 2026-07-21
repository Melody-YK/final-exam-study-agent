import { expect, test, type Page, type TestInfo } from '@playwright/test'

import { installMockApi } from './mockApi'

async function verifyView(page: Page, testInfo: TestInfo, name: string) {
  const pixels = await page.screenshot({
    fullPage: true,
    path: testInfo.outputPath(`${name}.png`),
  })
  expect(pixels.byteLength, `${name} screenshot should be nonblank`).toBeGreaterThan(10_000)

  const layout = await page.evaluate(() => {
    const activeRoot = document.querySelector<HTMLElement>('[role="dialog"]') ?? document.body
    const visible = (element: HTMLElement) => {
      const style = getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return (
        style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        Number(style.opacity) !== 0 &&
        rect.width > 0 &&
        rect.height > 0
      )
    }
    const controls = Array.from(
      activeRoot.querySelectorAll<HTMLElement>('a, button, input, select, textarea'),
    ).filter(visible)
    const fixedLayer = (element: HTMLElement) => {
      let current: HTMLElement | null = element
      while (current && current !== activeRoot) {
        const position = getComputedStyle(current).position
        if (position === 'fixed' || position === 'sticky') return current
        current = current.parentElement
      }
      return null
    }
    const overlaps: string[] = []
    for (let index = 0; index < controls.length; index += 1) {
      const left = controls[index]
      if (!left) continue
      const leftRect = left.getBoundingClientRect()
      for (let otherIndex = index + 1; otherIndex < controls.length; otherIndex += 1) {
        const right = controls[otherIndex]
        if (!right || left.contains(right) || right.contains(left)) continue
        const leftLayer = fixedLayer(left)
        const rightLayer = fixedLayer(right)
        if ((leftLayer === null) !== (rightLayer === null)) continue
        const rightRect = right.getBoundingClientRect()
        const overlapWidth = Math.min(leftRect.right, rightRect.right) - Math.max(leftRect.left, rightRect.left)
        const overlapHeight = Math.min(leftRect.bottom, rightRect.bottom) - Math.max(leftRect.top, rightRect.top)
        if (overlapWidth > 1 && overlapHeight > 1) {
          overlaps.push(
            `${left.getAttribute('aria-label') ?? left.textContent?.trim() ?? left.tagName} <> ${right.getAttribute('aria-label') ?? right.textContent?.trim() ?? right.tagName}`,
          )
        }
      }
    }
    const clippedControls = controls
      .filter((element) => !['TEXTAREA', 'INPUT', 'SELECT'].includes(element.tagName))
      .filter(
        (element) =>
          element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1,
      )
      .map(
        (element) =>
          element.getAttribute('aria-label') ?? element.textContent?.trim() ?? element.tagName,
      )

    return {
      viewport: window.innerWidth,
      bodyWidth: document.body.scrollWidth,
      rootWidth: document.documentElement.scrollWidth,
      visibleText: activeRoot.innerText.trim().length,
      overlaps,
      clippedControls,
    }
  })

  expect(layout.visibleText, `${name} should contain visible content`).toBeGreaterThan(20)
  expect(layout.bodyWidth, `${name} body should not overflow`).toBeLessThanOrEqual(layout.viewport + 1)
  expect(layout.rootWidth, `${name} root should not overflow`).toBeLessThanOrEqual(layout.viewport + 1)
  expect(layout.overlaps, `${name} interactive controls should not overlap`).toEqual([])
  expect(layout.clippedControls, `${name} control labels should not clip`).toEqual([])
}

async function verifyMobileLibraryClearance(page: Page, testInfo: TestInfo) {
  const clearance = await page.evaluate(async () => {
    const nav = document.querySelector<HTMLElement>('.mobile-nav')
    if (!nav || getComputedStyle(nav).display === 'none') return null
    window.scrollTo(0, document.documentElement.scrollHeight)
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
    const lastAction = document.querySelector<HTMLElement>(
      '.document-table tbody tr:last-child .table-actions button:last-child',
    )
    return lastAction
      ? { actionBottom: lastAction.getBoundingClientRect().bottom, navTop: nav.getBoundingClientRect().top }
      : null
  })
  if (clearance) {
    expect(clearance.actionBottom, 'last Library action should clear the fixed mobile nav').toBeLessThanOrEqual(
      clearance.navTop + 1,
    )
    const pixels = await page.screenshot({ path: testInfo.outputPath('library-bottom.png') })
    expect(pixels.byteLength, 'Library bottom screenshot should be nonblank').toBeGreaterThan(10_000)
    await page.evaluate(() => window.scrollTo(0, 0))
  }
}

test('primary views stay nonblank and free of overflow or control overlap', async ({ page }, testInfo) => {
  await installMockApi(page)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '课程资料' })).toBeVisible()
  await verifyView(page, testInfo, 'library')
  await verifyMobileLibraryClearance(page, testInfo)

  await page.getByRole('link', { name: '问答' }).click()
  await page.getByLabel('课程问题').fill('什么是进程？')
  await page.getByRole('button', { name: '提交问题' }).click()
  await expect(page.getByText('已有来源')).toBeVisible()
  await verifyView(page, testInfo, 'qa')

  await page.getByRole('button', { name: /进程页面\.png/ }).click()
  await expect(page.getByRole('img', { name: '进程页面.png 页面 6' })).toBeVisible()
  await verifyView(page, testInfo, 'source')
  await page.getByRole('button', { name: '关闭' }).click()

  await page.getByRole('link', { name: '笔记' }).click()
  await expect(page.getByLabel('笔记正文')).toBeVisible()
  await verifyView(page, testInfo, 'notes')

  await page.getByRole('link', { name: 'Lab' }).click()
  await expect(page.getByRole('heading', { name: '工程链路' })).toBeVisible()
  await verifyView(page, testInfo, 'lab')

  await page.getByRole('link', { name: '资料' }).click()
  await page.getByRole('button', { name: '删除资料' }).first().click()
  await expect(page.getByRole('dialog', { name: /删除/ })).toBeVisible()
  await verifyView(page, testInfo, 'delete-dialog')
})
