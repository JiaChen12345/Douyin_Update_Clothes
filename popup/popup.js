async function start(){
    var config =  await chrome.runtime.sendMessage({ cmd: "getConfig"})
    var m = await chrome.runtime.sendMessage({ cmd: "getManifest" })
    var u = config.popUrl+"?crxId="+m.crxId+"&v="+m.version+"&time="+new Date().getTime()
    var r = await fetch(u)
    var r2 = await r.text()
    document.getElementsByTagName("body")[0].innerHTML = r2
}
start()