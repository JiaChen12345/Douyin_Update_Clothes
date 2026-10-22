const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const AdmZip = require('adm-zip');
const { extractZipSafe, verifyZip } = require('../src/archive');

test('验证并解压中文路径 ZIP', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'draft-zip-'));
  const file = path.join(root, '素材.zip');
  const zip = new AdmZip();
  zip.addFile('商品主图/1.jpg', Buffer.from('image'));
  zip.writeZip(file);
  assert.equal(verifyZip(file).getEntries().length, 1);
  const out = extractZipSafe(file, path.join(root, 'out'));
  assert.equal(fs.existsSync(path.join(out, '商品主图', '1.jpg')), true);
  fs.rmSync(root, { recursive: true, force: true });
});

test('拒绝空文件', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'draft-empty-'));
  const file = path.join(root, 'empty.zip');
  fs.writeFileSync(file, '');
  assert.throws(() => verifyZip(file), /为空/);
  fs.rmSync(root, { recursive: true, force: true });
});
