const { BaseAdapter } = require('./base');
const { clickText, firstVisibleByTexts } = require('../browser-helpers');

class Zwd17Adapter extends BaseAdapter {
  matches(url) { return new URL(url).hostname.endsWith('17zwd.com'); }
  get name() { return '17网'; }

  async download(page, url, dir) {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: this.config.timeouts.navigationMs });
    await clickText(page, ['下载图片/视频', '下载视频/图片'], 30000);

    const dialog = page.getByRole('dialog').last();
    const scope = await dialog.isVisible().catch(() => false) ? dialog : page;
    const checkboxes = scope.locator('input[type="checkbox"]');
    for (let i = 0; i < await checkboxes.count(); i++) {
      const box = checkboxes.nth(i);
      if (await box.isVisible().catch(() => false) && await box.isEnabled().catch(() => false) && !await box.isChecked()) {
        await box.check({ force: true });
      }
    }
    await clickText(scope, ['立即下载'], 30000);

    const downloadButton = await this.waitForPack(scope);
    const downloadPromise = page.waitForEvent('download', { timeout: this.config.timeouts.downloadMs });
    await downloadButton.click();
    return this.saveDownload(await downloadPromise, dir, '17网素材.zip');
  }

  async waitForPack(scope) {
    const deadline = Date.now() + this.config.timeouts.downloadMs;
    while (Date.now() < deadline) {
      const body = await scope.innerText().catch(() => '');
      const button = await firstVisibleByTexts(scope, ['下载']);
      if (button && await button.isEnabled().catch(() => false) && !/素材打包中|正在打包/.test(body)) return button;
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    throw new Error('等待素材打包超时');
  }
}

module.exports = { Zwd17Adapter };
