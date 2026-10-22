const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const ExcelJS = require('exceljs');
const { ExcelStore, normalizeUrlCell } = require('../src/excel-store');

test('读取 Excel 带显示文字的超链接', () => {
  assert.equal(
    normalizeUrlCell({ text: '商品名称', hyperlink: 'https://cs.17zwd.com/item/165372718?' }, 2),
    'https://cs.17zwd.com/item/165372718?'
  );
});

test('无效网址报告 Excel 行号', () => {
  assert.throws(() => normalizeUrlCell('不是网址', 7), /第 7 行/);
});

test('Excel 自动补充状态列并持久化更新', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'draft-excel-'));
  const file = path.join(root, 'tasks.xlsx');
  const book = new ExcelJS.Workbook();
  const sheet = book.addWorksheet('任务');
  sheet.addRow(['网址']);
  sheet.addRow(['https://cs.17zwd.com/item/1']);
  await book.xlsx.writeFile(file);

  const store = new ExcelStore(file);
  await store.load();
  assert.equal(store.tasks()[0].status, '待处理');
  await store.update(2, { '站点': '17网', '状态': '草稿成功' });

  const reopened = new ExcelStore(file);
  await reopened.load();
  assert.equal(reopened.tasks()[0].status, '草稿成功');
  fs.rmSync(root, { recursive: true, force: true });
});
