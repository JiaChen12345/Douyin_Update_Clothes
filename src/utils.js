const fs = require('node:fs');
const path = require('node:path');

const IMAGE_EXTENSIONS = new Set(['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp']);

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function nowText() {
  return new Date().toLocaleString('zh-CN', { hour12: false });
}

function safeName(value) {
  return String(value).replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').slice(0, 100);
}

function taskId(url, rowNumber) {
  const parsed = new URL(url);
  const leaf = parsed.pathname.split('/').filter(Boolean).pop() || parsed.hostname;
  return `${String(rowNumber).padStart(4, '0')}-${safeName(parsed.hostname)}-${safeName(leaf)}`;
}

function naturalCompare(a, b) {
  return a.localeCompare(b, 'zh-CN', { numeric: true, sensitivity: 'base' });
}

function collectImages(root, folderKeyword) {
  const matches = [];
  function walk(dir, insideMatch) {
    const base = path.basename(dir);
    const matched = insideMatch || base.includes(folderKeyword);
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.name.startsWith('.') || entry.name === 'Thumbs.db') continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full, matched);
      else if (matched && IMAGE_EXTENSIONS.has(path.extname(entry.name).toLowerCase())) matches.push(full);
    }
  }
  walk(root, false);
  return matches.sort((a, b) => naturalCompare(path.relative(root, a), path.relative(root, b)));
}

function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

module.exports = { ensureDir, nowText, safeName, taskId, naturalCompare, collectImages, sleep, IMAGE_EXTENSIONS };
