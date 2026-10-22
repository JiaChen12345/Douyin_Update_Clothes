const { clickText, clickLocatorAndFollow, clickTextAndFollow, waitForPageWithTexts, firstVisibleByTexts, detectHumanChallenge, waitForHuman } = require('./browser-helpers');

class DouyinDraftCreator {
  constructor(config) { this.config = config; this.s = config.selectors.douyin; }

  async create(page, mainImages, detailImages, onCheckpoint, onHuman) {
    await onCheckpoint('打开抖店商品创建');
    await page.goto(this.config.douyinHome, { waitUntil: 'domcontentloaded', timeout: this.config.timeouts.navigationMs });
    page = await this.handleChallenge(page, onHuman);
    page = await this.openProductCreation(page);

    await onCheckpoint('上传主图');
    page = await waitForPageWithTexts(page.context(), this.s.localUploadTexts, this.config.timeouts.navigationMs);
    await this.uploadThroughButton(page, this.s.localUploadTexts, mainImages);
    await this.waitUploads(page, mainImages.length);
    page = await clickTextAndFollow(page, this.s.nextGenerateTexts, 60000);
    page = await waitForPageWithTexts(page.context(), this.s.confirmGenerateTexts, this.config.timeouts.navigationMs);

    await onCheckpoint('确认AI生成');
    page = await clickTextAndFollow(page, this.s.confirmGenerateTexts, 60000);
    page = await this.waitForAi(page, onHuman);

    await onCheckpoint('替换商品详情图');
    const section = await this.findDetailSection(page);
    await this.deleteExistingDetailImages(section);
    await this.uploadThroughButton(section, this.s.detailUploadTexts, detailImages);
    await this.waitUploads(page, detailImages.length);

    await onCheckpoint('保存草稿');
    await this.assertNoPublishAction(this.s.saveDraftTexts);
    await clickText(page, this.s.saveDraftTexts, 30000);
    await this.waitForSuccess(page);
    return page;
  }

  async handleChallenge(page, onHuman) {
    if (await detectHumanChallenge(page)) return waitForHuman(page, onHuman, '登录已失效或页面要求安全验证');
    return page;
  }

  async openProductCreation(page) {
    const deadline = Date.now() + this.config.timeouts.navigationMs;
    while (Date.now() < deadline) {
      const create = await firstVisibleByTexts(page, this.s.createProductTexts);
      if (create) {
        page = await clickLocatorAndFollow(page, create);
        return waitForPageWithTexts(page.context(), this.s.localUploadTexts, this.config.timeouts.navigationMs);
      }
      const productMenu = await firstVisibleByTexts(page, this.s.productMenuTexts);
      if (productMenu) {
        page = await clickLocatorAndFollow(page, productMenu, 1200);
        await page.waitForTimeout(500);
        const revealed = await firstVisibleByTexts(page, this.s.createProductTexts);
        if (revealed) {
          page = await clickLocatorAndFollow(page, revealed);
          return waitForPageWithTexts(page.context(), this.s.localUploadTexts, this.config.timeouts.navigationMs);
        }
      }
      await page.waitForTimeout(1000);
    }
    throw new Error('抖店首页已打开，但在左侧导航中找不到“商品创建”');
  }

  async uploadThroughButton(scope, texts, files) {
    const button = await firstVisibleByTexts(scope, texts);
    if (!button) throw new Error(`找不到上传入口：${texts.join(' / ')}`);
    const page = scope.page ? scope.page() : scope;
    const chooserPromise = page.waitForEvent('filechooser', { timeout: 15000 }).catch(() => null);
    await button.click();
    const chooser = await chooserPromise;
    if (chooser) await chooser.setFiles(files);
    else {
      const input = scope.locator('input[type="file"]').last();
      if (!await input.count()) throw new Error('上传按钮未打开文件选择框，也未找到文件输入框');
      await input.setInputFiles(files);
    }
  }

  async waitUploads(page, expected) {
    const deadline = Date.now() + this.config.timeouts.uploadMs;
    while (Date.now() < deadline) {
      const body = await page.locator('body').innerText().catch(() => '');
      if (/上传失败|重新上传/.test(body)) throw new Error('页面报告图片上传失败');
      if (!/上传中|正在上传|处理中/.test(body)) { await page.waitForTimeout(1500); return; }
      await page.waitForTimeout(1000);
    }
    throw new Error(`等待 ${expected} 张图片上传完成超时`);
  }

  async waitForAi(page, onHuman) {
    const deadline = Date.now() + this.config.timeouts.aiGenerationMs;
    while (Date.now() < deadline) {
      if (await detectHumanChallenge(page)) page = await waitForHuman(page, onHuman, 'AI 生成期间出现安全验证');
      if (await firstVisibleByTexts(page, this.s.detailSectionTexts)) return page;
      const body = await page.locator('body').innerText().catch(() => '');
      if (/生成失败|生成异常/.test(body)) throw new Error('官方 AI 生成失败');
      await page.waitForTimeout(1500);
    }
    throw new Error('等待官方 AI 生成超过 10 分钟');
  }

  async findDetailSection(page) {
    const title = await firstVisibleByTexts(page, this.s.detailSectionTexts);
    if (!title) throw new Error('找不到“商品详情”区域');
    return title.locator('xpath=ancestor::*[.//input[@type="file"] or .//button][1]');
  }

  async deleteExistingDetailImages(section) {
    const page = section.page();
    await page.locator('[class*="assistantContent"], [class*="assistant-content"]').evaluateAll(elements => {
      for (const element of elements) element.style.pointerEvents = 'none';
    }).catch(() => {});
    for (let safety = 0; safety < 200; safety++) {
      const images = section.locator('img:visible');
      const count = await images.count();
      if (!count) return;
      const image = images.first();
      await image.scrollIntoViewIfNeeded().catch(() => {});
      await image.hover({ force: true, timeout: 10000 });
      const deleteButton = section.locator('[aria-label*="删除"], [title*="删除"], button:has-text("删除"), .delete, [class*="delete"], [class*="trash"]').first();
      if (!await deleteButton.isVisible().catch(() => false)) throw new Error('详情图悬停后未找到删除按钮');
      await deleteButton.click({ force: true });
      const confirm = await firstVisibleByTexts(page, ['确认', '确定', '删除']);
      if (confirm) await confirm.click({ force: true }).catch(() => {});
      await page.waitForTimeout(300);
      if (await images.count() >= count) throw new Error('详情图删除后数量未减少');
    }
    throw new Error('详情图数量异常，已停止删除以避免死循环');
  }

  async waitForSuccess(page) {
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      if (await firstVisibleByTexts(page, this.s.successTexts, { exact: false })) return;
      await page.waitForTimeout(500);
    }
    throw new Error('点击保存草稿后未检测到成功提示');
  }

  async assertNoPublishAction(texts) {
    if (texts.some(text => /发布|提交审核|上架/.test(text))) throw new Error('安全阻断：保存操作配置包含发布类文字');
  }
}

module.exports = { DouyinDraftCreator };
