const fs = require('node:fs');
const path = require('node:path');
const AdmZip = require('adm-zip');
const { ensureDir } = require('./utils');

function verifyZip(file) {
  if (!fs.existsSync(file) || fs.statSync(file).size === 0) throw new Error('压缩包不存在或为空');
  const zip = new AdmZip(file);
  if (zip.getEntries().length === 0) throw new Error('压缩包内没有文件');
  return zip;
}

function extractZipSafe(file, destination) {
  const zip = verifyZip(file);
  const root = path.resolve(ensureDir(destination));
  for (const entry of zip.getEntries()) {
    const target = path.resolve(root, entry.entryName);
    if (target !== root && !target.startsWith(`${root}${path.sep}`)) {
      throw new Error(`压缩包包含不安全路径：${entry.entryName}`);
    }
  }
  zip.extractAllTo(root, true);
  return root;
}

module.exports = { verifyZip, extractZipSafe };
