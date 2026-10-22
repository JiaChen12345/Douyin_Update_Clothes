const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { collectImages, naturalCompare } = require('../src/utils');

test('自然文件名排序为 1, 2, 10', () => {
  assert.deepEqual(['10.jpg', '2.jpg', '1.jpg'].sort(naturalCompare), ['1.jpg', '2.jpg', '10.jpg']);
});

test('递归收集所有包含关键词的文件夹中的图片', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'draft-images-'));
  const a = path.join(root, 'A主图');
  const nested = path.join(a, 'nested');
  const other = path.join(root, '普通目录');
  fs.mkdirSync(nested, { recursive: true });
  fs.mkdirSync(other);
  fs.writeFileSync(path.join(a, '10.jpg'), 'x');
  fs.writeFileSync(path.join(a, '2.png'), 'x');
  fs.writeFileSync(path.join(nested, '1.webp'), 'x');
  fs.writeFileSync(path.join(other, 'ignored.jpg'), 'x');
  const result = collectImages(root, '主图').map(file => path.basename(file));
  assert.deepEqual(result, ['2.png', '10.jpg', '1.webp']);
  fs.rmSync(root, { recursive: true, force: true });
});
