const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright-core');
const { loadConfig } = require('./config');
const { ExcelStore } = require('./excel-store');
const { createAdapters, adapterFor } = require('./adapters');
const { extractZipSafe, verifyZip } = require('./archive');
const { DouyinDraftCreator } = require('./douyin');
const { ensureDir, nowText, taskId, collectImages } = require('./utils');
const { createLogger } = require('./logger');
const { screenshot, findPageWithTexts } = require('./browser-helpers');

const PROJECT_ROOT = path.resolve(__dirname, '..');

async function launch(config) {
  ensureDir(config.browserProfileDir);
  return chromium.launchPersistentContext(config.browserProfileDir, {
    executablePath: config.browserExecutable,
    headless: false,
    acceptDownloads: true,
    viewport: null,
    args: ['--start-maximized']
  });
}

async function loginMode(config) {
  const context = await launch(config);
  const page = context.pages()[0] || await context.newPage();
  await page.goto(config.douyinHome, { waitUntil: 'domcontentloaded' });
  console.log('专用 Edge 已打开。请依次登录 17网、搜款网和抖店；完成后直接关闭浏览器。');
  await context.waitForEvent('close');
}

async function retryDownload({ adapter, page, url, downloadsDir, retryCount, log, onRetry }) {
  let lastError;
  for (let attempt = 0; attempt <= retryCount; attempt++) {
    try {
      log.write(`下载尝试 ${attempt + 1}/${retryCount + 1}`);
      return await adapter.download(page, url, downloadsDir);
    } catch (error) {
      lastError = error;
      log.write('下载失败', { attempt: attempt + 1, message: error.message });
      if (attempt < retryCount) {
        await onRetry(attempt + 1, error);
        await page.waitForTimeout(1500);
      }
    }
  }
  throw lastError;
}

function existingArchive(downloadsDir) {
  if (!fs.existsSync(downloadsDir)) return null;
  for (const name of fs.readdirSync(downloadsDir)) {
    const file = path.join(downloadsDir, name);
    if (path.extname(name).toLowerCase() !== '.zip') continue;
    try { verifyZip(file); return file; } catch { /* ignore invalid leftovers */ }
  }
  return null;
}

async function processTask({ task, store, context, config, adapters }) {
  const adapter = adapterFor(adapters, task.url);
  const taskDir = ensureDir(path.join(config.workDir, taskId(task.url, task.rowNumber)));
  const downloadsDir = ensureDir(path.join(taskDir, 'downloads'));
  const extractDir = path.join(taskDir, 'extracted');
  const evidenceDir = ensureDir(path.join(taskDir, 'evidence'));
  const log = createLogger(taskDir);
  let page = await context.newPage();
  let retries = 0;

  const update = values => store.update(task.rowNumber, values);
  const checkpoint = step => update({ '状态': '上传中', '当前步骤': step });
  const human = reason => update({ '状态': '等待人工', '当前步骤': reason });

  await update({
    '站点': adapter.name, '状态': '下载中', '当前步骤': '准备下载素材',
    '开始时间': nowText(), '完成时间': '', '素材目录': taskDir, '失败原因': '', '重试次数': 0
  });

  try {
    let archive = existingArchive(downloadsDir);
    if (!archive) {
      archive = await retryDownload({
        adapter, page, url: task.url, downloadsDir, retryCount: config.retryCount, log,
        onRetry: async count => { retries = count; await update({ '重试次数': count }); }
      });
    } else log.write('复用已验证的下载压缩包', { archive });

    if (!fs.existsSync(extractDir)) extractZipSafe(archive, extractDir);
    const mainImages = collectImages(extractDir, '主图');
    const detailImages = collectImages(extractDir, '详情图');
    if (!mainImages.length) throw new Error('解压素材中没有找到名称包含“主图”的文件夹或图片');
    if (!detailImages.length) throw new Error('解压素材中没有找到名称包含“详情图”的文件夹或图片');
    log.write('素材检查完成', { mainImages: mainImages.length, detailImages: detailImages.length });
    await update({ '状态': '已解压', '当前步骤': '素材检查完成', '素材目录': extractDir });

    const creator = new DouyinDraftCreator(config);
    page = await creator.create(page, mainImages, detailImages, checkpoint, human);
    const successShot = await screenshot(page, evidenceDir, '草稿保存成功');
    log.write('草稿保存成功', { screenshot: successShot });
    await update({ '状态': '草稿成功', '当前步骤': '完成', '完成时间': nowText(), '失败原因': '', '重试次数': retries });
  } catch (error) {
    const workflowPage = await findPageWithTexts(context, ['商品详情', '本地上传']).catch(() => null);
    if (workflowPage) page = workflowPage;
    if (page.isClosed()) {
      page = [...context.pages()].reverse().find(candidate => !candidate.isClosed()) || page;
    }
    const shot = await screenshot(page, evidenceDir, `失败-${Date.now()}`);
    log.write('任务失败', { message: error.message, url: page.url(), title: await page.title().catch(() => ''), screenshot: shot });
    await update({ '状态': '失败', '当前步骤': '任务失败', '完成时间': nowText(), '失败原因': error.message, '重试次数': retries });
  } finally {
    await page.close().catch(() => {});
  }
}

async function main() {
  const config = loadConfig(PROJECT_ROOT);
  ensureDir(config.workDir);
  if (process.argv.includes('--login')) return loginMode(config);

  const store = new ExcelStore(config.excelPath);
  await store.load();
  const tasks = store.tasks().filter(task => task.status !== '草稿成功');
  if (!tasks.length) { console.log('没有待处理任务。'); return; }

  const context = await launch(config);
  const adapters = createAdapters(config);
  try {
    for (const task of tasks) await processTask({ task, store, context, config, adapters });
  } finally {
    await context.close();
  }
}

if (require.main === module) {
  main().catch(error => { console.error(`运行失败：${error.message}`); process.exitCode = 1; });
}

module.exports = { existingArchive, retryDownload };
