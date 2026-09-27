const assert = require("node:assert/strict")
const model = require("../Model.js")

const discovery = model.parseDiscovery(JSON.stringify({
  receivers: [
    { name: "Living Room", address: "192.168.1.20", protocol: "miracast-mice" },
    { name: "Duplicate", address: "192.168.1.20", protocol: "miracast-mice" },
    { name: "Bedroom", address: "192.168.1.21", protocol: "miracast-mice" }
  ]
}))

assert.equal(discovery.error, "")
assert.equal(discovery.receivers.length, 2)
assert.equal(discovery.receivers[0].name, "Living Room")
assert.equal(discovery.receivers[1].address, "192.168.1.21")

assert.equal(model.parseDiscovery("not json").receivers.length, 0)
assert.match(model.parseDiscovery("not json").error, /unreadable/)
assert.equal(model.parseEvent('{"event":"metrics","video_frames":30}').video_frames, 30)
assert.equal(model.parseEvent("bad"), null)
const windows = model.parsePickerWindowList(
  "42[HC>]microsoft-edge[HT>]Video[HE>]6538cbb1ada0[HA>]"
  + "84[HC>]chatgpt[HT>]ChatGPT[HE>]6538caece100[HA>]"
)
assert.equal(windows.length, 2)
assert.equal(windows[0].handle, "42")
assert.equal(windows[0].title, "Video")
assert.equal(windows[1].appClass, "chatgpt")
assert.equal(model.displayName("eDP-1", "BOE panel"), "Built-in display")
assert.equal(model.displayName("HDMI-A-1", "Living Room Display"), "Living Room Display")
assert.equal(model.protocolLabel("miracast-mice"), "MIRACAST · LOCAL NETWORK")
assert.equal(model.protocolName("miracast-mice"), "MIRACAST")
assert.equal(model.connectionLabel("miracast-mice"), "LOCAL NETWORK")
assert.equal(model.connectionLabel("miracast-p2p"), "WI-FI DIRECT")
assert.equal(model.resolutionLabel({ nativeHeight: 720 }), "720P")
assert.equal(model.humanBitrate(6100000), "6.1 Mbps")
assert.equal(model.stateLabel("streaming"), "SHARING YOUR SCREEN")
assert.equal(model.localPath("file:///tmp/OmaCast%20Test/omarchy-cast"), "/tmp/OmaCast Test/omarchy-cast")

console.log("model tests passed")
