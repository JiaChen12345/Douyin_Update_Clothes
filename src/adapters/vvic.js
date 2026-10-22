const { BaseAdapter } = require('./base');
const { clickText } = require('../browser-helpers');

class VvicAdapter extends BaseAdapter {
  matches(url) { return new URL(url).hostname.endsWith('vvic.com'); }
  get name() { return '搜款网'; }

  async download(page, url, dir) {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: this.config.timeouts.navigationMs });
    const downloadPromise = page.waitForEvent('download', { timeout: this.config.timeouts.downloadMs });
    await clickText(page, ['图片/视频下载', '图片／视频下载', '图片下载'], 30000);
    const dialog = page.getByRole('dialog').last();
    const scope = await dialog.isVisible().catch(() => false) ? dialog : page;
    await clickText(scope, ['立即下载', '开始下载'], 30000);
    return this.saveDownload(await downloadPromise, dir, '搜款网素材.zip');
  }
}

module.exports = { VvicAdapter };
