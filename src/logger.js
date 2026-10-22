const fs = require('node:fs');
const path = require('node:path');
const { ensureDir, nowText } = require('./utils');

function createLogger(dir) {
  ensureDir(dir);
  const file = path.join(dir, 'run.log');
  return {
    file,
    write(message, data) {
      const suffix = data === undefined ? '' : ` ${JSON.stringify(data)}`;
      const line = `[${nowText()}] ${message}${suffix}`;
      console.log(line);
      fs.appendFileSync(file, `${line}\n`, 'utf8');
    }
  };
}

module.exports = { createLogger };
