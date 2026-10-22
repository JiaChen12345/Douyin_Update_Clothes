const fs = require('node:fs');
const path = require('node:path');

function loadConfig(projectRoot) {
  const configPath = path.join(projectRoot, 'config.json');
  const examplePath = path.join(projectRoot, 'config.example.json');
  if (!fs.existsSync(configPath)) fs.copyFileSync(examplePath, configPath);
  const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  config.browserProfileDir ||= config.chromeProfileDir || './edge-profile';
  config.browserExecutable ||= config.chromeExecutable;
  for (const key of ['excelPath', 'workDir', 'browserProfileDir']) {
    config[key] = path.resolve(projectRoot, config[key]);
  }
  if (!config.browserExecutable || !fs.existsSync(config.browserExecutable)) {
    throw new Error(`找不到 Microsoft Edge：${config.browserExecutable || '(未配置)'}`);
  }
  return config;
}

module.exports = { loadConfig };
