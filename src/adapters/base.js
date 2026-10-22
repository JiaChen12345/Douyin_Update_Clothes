const fs = require('node:fs');
const path = require('node:path');
const { verifyZip } = require('../archive');

class BaseAdapter {
  constructor(config) { this.config = config; }
  matches() { return false; }

  async saveDownload(download, dir, fallbackName = 'materials.zip') {
    const suggested = download.suggestedFilename() || fallbackName;
    const file = path.join(dir, suggested.toLowerCase().endsWith('.zip') ? suggested : `${suggested}.zip`);
    await download.saveAs(file);
    const failure = await download.failure();
    if (failure) throw new Error(`浏览器下载失败：${failure}`);
    if (!fs.existsSync(file) || fs.statSync(file).size === 0) throw new Error('下载文件为空');
    verifyZip(file);
    return file;
  }
}

module.exports = { BaseAdapter };
