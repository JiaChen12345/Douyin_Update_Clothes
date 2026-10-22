const ExcelJS = require('exceljs');
const fs = require('node:fs');
const path = require('node:path');

const COLUMNS = ['网址', '站点', '状态', '当前步骤', '开始时间', '完成时间', '素材目录', '失败原因', '重试次数'];

function normalizeUrlCell(value, rowNumber) {
  let raw = value;
  if (value && typeof value === 'object') {
    raw = value.hyperlink || value.text || value.result || '';
  }
  const url = String(raw || '').trim();
  if (!url) return '';
  let parsed;
  try { parsed = new URL(url); }
  catch { throw new Error(`Excel 第 ${rowNumber} 行不是有效网址：${url}`); }
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error(`Excel 第 ${rowNumber} 行仅支持 http/https 网址：${url}`);
  }
  return parsed.href;
}

class ExcelStore {
  constructor(file) {
    this.file = file;
    this.pendingFile = path.join(path.dirname(file), `${path.basename(file, path.extname(file))}.pending.xlsx`);
  }

  async load() {
    this.book = new ExcelJS.Workbook();
    const hasPending = fs.existsSync(this.pendingFile);
    const hasMain = fs.existsSync(this.file);
    const sourceFile = hasPending && (!hasMain || fs.statSync(this.pendingFile).mtimeMs > fs.statSync(this.file).mtimeMs)
      ? this.pendingFile : this.file;
    if (!fs.existsSync(sourceFile)) {
      this.sheet = this.book.addWorksheet('任务');
      this.sheet.addRow(COLUMNS);
      await this.save();
      throw new Error(`已创建任务表 ${this.file}。请在“网址”列填写网址后重新运行。`);
    }
    await this.book.xlsx.readFile(sourceFile);
    this.sheet = this.book.worksheets[0];
    const headers = new Map();
    this.sheet.getRow(1).eachCell((cell, col) => headers.set(String(cell.value || '').trim(), col));
    if (!headers.has('网址')) throw new Error('Excel 第一行缺少“网址”列');
    for (const name of COLUMNS) {
      if (!headers.has(name)) {
        const col = this.sheet.columnCount + 1;
        this.sheet.getCell(1, col).value = name;
        headers.set(name, col);
      }
    }
    this.headers = headers;
  }

  tasks() {
    const result = [];
    for (let rowNumber = 2; rowNumber <= this.sheet.rowCount; rowNumber++) {
      const row = this.sheet.getRow(rowNumber);
      const url = normalizeUrlCell(row.getCell(this.headers.get('网址')).value, rowNumber);
      if (!url) continue;
      const status = String(row.getCell(this.headers.get('状态')).value || '待处理').trim();
      result.push({ rowNumber, url, status });
    }
    return result;
  }

  async update(rowNumber, values) {
    const row = this.sheet.getRow(rowNumber);
    for (const [name, value] of Object.entries(values)) row.getCell(this.headers.get(name)).value = value;
    row.commit();
    await this.save();
  }

  async save() {
    const temp = `${this.file}.tmp.xlsx`;
    await this.book.xlsx.writeFile(temp);
    try {
      if (fs.existsSync(this.file)) fs.rmSync(this.file);
      fs.renameSync(temp, this.file);
      if (fs.existsSync(this.pendingFile)) fs.rmSync(this.pendingFile);
    } catch (error) {
      if (!['EPERM', 'EBUSY', 'EACCES'].includes(error.code)) throw error;
      if (fs.existsSync(this.pendingFile)) fs.rmSync(this.pendingFile);
      fs.renameSync(temp, this.pendingFile);
      console.warn(`警告：${this.file} 正被 Excel/WPS 占用，状态已安全写入 ${this.pendingFile}。关闭表格后再次运行会自动合并。`);
    }
  }
}

module.exports = { ExcelStore, COLUMNS, normalizeUrlCell };
