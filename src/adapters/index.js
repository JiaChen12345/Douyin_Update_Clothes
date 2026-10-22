const { Zwd17Adapter } = require('./zwd17');
const { VvicAdapter } = require('./vvic');

function createAdapters(config) { return [new Zwd17Adapter(config), new VvicAdapter(config)]; }
function adapterFor(adapters, url) {
  const found = adapters.find(adapter => adapter.matches(url));
  if (!found) throw new Error(`不支持的素材网站：${new URL(url).hostname}`);
  return found;
}

module.exports = { createAdapters, adapterFor };
