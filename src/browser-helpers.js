const readline = require('node:readline/promises');
const { stdin, stdout } = require('node:process');
const path = require('node:path');
const { ensureDir } = require('./utils');

async function firstVisibleByTexts(pageOrLocator, texts, options = {}) {
  for (const text of texts) {
    const exact = options.exact !== false;
    const candidates = [
      pageOrLocator.getByRole('button', { name: text, exact }),
      pageOrLocator.getByRole('link', { name: text, exact }),
      pageOrLocator.getByText(text, { exact })
    ];
    for (const locator of candidates) {
      const first = locator.first();
      if (await first.isVisible().catch(() => false)) return first;
    }
    if (exact) {
      const fuzzy = pageOrLocator.getByText(text, { exact: false }).first();
      if (await fuzzy.isVisible().catch(() => false)) return fuzzy;
    }
  }
  return null;
}

async function clickText(pageOrLocator, texts, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const found = await firstVisibleByTexts(pageOrLocator, texts);
    if (found) { await found.click(); return; }
    const page = typeof pageOrLocator.page === 'function' ? pageOrLocator.page() : pageOrLocator;
    await page.waitForTimeout(500);
  }
  throw new Error(`找不到按钮或链接：${texts.join(' / ')}`);
}

async function clickLocatorAndFollow(page, locator, popupWaitMs = 3000) {
  const context = page.context();
  const popupPromise = context.waitForEvent('page', { timeout: popupWaitMs }).catch(() => null);
  await locator.click();
  const popup = await popupPromise;
  const active = popup || page;
  if (popup) await popup.waitForLoadState('domcontentloaded', { timeout: 30000 }).catch(() => {});
  await active.bringToFront().catch(() => {});
  return active;
}

async function clickTextAndFollow(page, texts, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const found = await firstVisibleByTexts(page, texts);
    if (found) return clickLocatorAndFollow(page, found);
    await page.waitForTimeout(500);
  }
  throw new Error(`找不到按钮或链接：${texts.join(' / ')}`);
}

async function findPageWithTexts(context, texts) {
  for (const candidate of [...context.pages()].reverse()) {
    if (candidate.isClosed()) continue;
    if (await firstVisibleByTexts(candidate, texts)) return candidate;
    for (const frame of candidate.frames()) {
      if (frame === candidate.mainFrame()) continue;
      if (await firstVisibleByTexts(frame, texts)) return candidate;
    }
  }
  return null;
}

async function waitForPageWithTexts(context, texts, timeout = 60000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const found = await findPageWithTexts(context, texts);
    if (found) {
      await found.bringToFront().catch(() => {});
      return found;
    }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  const pages = await Promise.all(context.pages().filter(page => !page.isClosed()).map(async (page, index) => ({
    index,
    url: page.url(),
    title: await page.title().catch(() => '')
  })));
  throw new Error(`所有窗口中都找不到：${texts.join(' / ')}；当前窗口=${JSON.stringify(pages)}`);
}

async function detectHumanChallenge(page) {
  const text = await page.locator('body').innerText({ timeout: 3000 }).catch(() => '');
  const title = await page.title().catch(() => '');
  const publicLanding = /抖店官网.*登录入口/.test(title) && /登录抖店/.test(text);
  return publicLanding || /(请登录|登录后|登录抖店|验证码|滑块|安全验证|风险验证|访问过于频繁|异常访问)/.test(text);
}

async function waitForHuman(page, onWait, reason) {
  const context = page.context();
  await onWait(reason);
  console.log(`\n需要人工处理：${reason}\n请在浏览器中完成登录/验证，不要关闭 Edge。进入商家后台后，回到此窗口按回车继续。`);
  const rl = readline.createInterface({ input: stdin, output: stdout });
  try {
    while (true) {
      await rl.question('进入商家后台后按回车：');
      const pages = context.pages().filter(candidate => !candidate.isClosed());
      if (!pages.length) throw new Error('人工处理期间专用 Edge 已被关闭；请重新运行并在登录后保持 Edge 打开');
      const merchant = await findMerchantPage(pages);
      if (merchant) {
        await merchant.bringToFront();
        return merchant;
      }
      console.log('尚未检测到商家后台。请确认页面左侧已经出现“商品创建”，不要停留在右上角显示“登录抖店”的公开首页。');
    }
  } finally {
    rl.close();
  }
}

async function findMerchantPage(pages) {
  for (const candidate of [...pages].reverse()) {
    if (!/jinritemai\.com/.test(candidate.url())) continue;
    const text = await candidate.locator('body').innerText({ timeout: 3000 }).catch(() => '');
    const publicLogin = /登录抖店/.test(text) && /立即入驻/.test(text);
    const createVisible = await candidate.getByText('商品创建', { exact: true }).first().isVisible().catch(() => false);
    const managementVisible = await candidate.getByText('商品管理', { exact: true }).first().isVisible().catch(() => false);
    const ordersVisible = await candidate.getByText('订单发货', { exact: true }).first().isVisible().catch(() => false);
    if ((createVisible || (managementVisible && ordersVisible)) && !publicLogin) return candidate;
  }
  return null;
}

async function screenshot(page, dir, name) {
  const file = path.join(ensureDir(dir), `${name}.png`);
  await page.screenshot({ path: file, fullPage: true }).catch(() => {});
  return file;
}

module.exports = { firstVisibleByTexts, clickText, clickLocatorAndFollow, clickTextAndFollow, findPageWithTexts, waitForPageWithTexts, detectHumanChallenge, waitForHuman, findMerchantPage, screenshot };
