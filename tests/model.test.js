const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const model = require("../Model.js")
const manifest = require("../manifest.json")

function qmlObjectBlock(source, type, id) {
  const marker = `${type} {`
  let start = source.indexOf(marker)
  while (start !== -1) {
    let depth = 0
    for (let index = start; index < source.length; index++) {
      if (source[index] === "{") depth++
      if (source[index] === "}") {
        depth--
        if (depth === 0) {
          const block = source.slice(start, index + 1)
          if (new RegExp(`\\bid\\s*:\\s*${id}\\b`).test(block)) return block
          break
        }
      }
    }
    start = source.indexOf(marker, start + marker.length)
  }
  throw new Error(`Could not find ${type} object with id ${id}`)
}

assert.equal(model.version(), manifest.version)
assert.equal(manifest.name, "HovenCast")
assert.equal(manifest.barWidget.displayName, manifest.name)
assert.match(manifest.version, /^\d+\.\d+\.\d+$/)
assert.equal(model.buildTitle(), manifest.name + " v" + manifest.version)
const panelSource = fs.readFileSync(path.join(__dirname, "..", "Panel.qml"), "utf8")
assert.match(panelSource, /readonly property string buildTitle: Model\.buildTitle\(\)/)
assert.equal((panelSource.match(/title: root\.buildTitle/g) || []).length, 2)
assert.match(panelSource, /tooltipText:[\s\S]*?root\.buildTitle \+ " · sharing"/)
assert.doesNotMatch(panelSource, /"HovenCast v"/)
assert.match(panelSource, /buildTitle: root\.buildTitle/)
assert.match(panelSource, /groupedDeviceCount:/)

const grouped = model.parseDiscovery(JSON.stringify({receivers: [{
  name: "Living room", address: "192.0.2.1", port: 7250, protocol: "miracast-mice",
  endpoints: [
    {name: "Living room", address: "192.0.2.1", port: 7250, protocol: "miracast-mice"},
    {name: "Living room", address: "p2p:02:00:00:00:00:01", port: 7236, interface: "p2p-dev-wlan0", protocol: "miracast-p2p"}
  ]
}]})).receivers[0]
assert.equal(grouped.endpoints.length, 2)
assert.equal(grouped.address, "192.0.2.1")
assert.equal(model.endpointByAddress(grouped, "p2p:02:00:00:00:00:01").protocol, "miracast-p2p")
assert.equal(model.endpointByAddress(grouped, "missing"), null)
assert.equal(model.connectionSummary(grouped), "LOCAL NETWORK + WI-FI DIRECT")
assert.match(panelSource, /root\.connectReceiver\(modelData\)/)
assert.match(panelSource, /Model\.endpointByAddress\(modelData, root\.activeAddress\)/)

const discovery = model.parseDiscovery(JSON.stringify({
  receivers: [
    {
      name: '<img src="https://example.invalid/receiver-name-probe">',
      address: "192.168.1.20",
      protocol: "miracast-mice"
    },
    { name: "Duplicate", address: "192.168.1.20", protocol: "miracast-mice" },
    { name: "Bedroom", address: "192.168.1.21", protocol: "miracast-mice" }
  ]
}))

assert.equal(discovery.error, "")
assert.equal(discovery.receivers.length, 2)
assert.equal(
  discovery.receivers[0].name,
  '<img src="https://example.invalid/receiver-name-probe">'
)
assert.equal(discovery.receivers[1].address, "192.168.1.21")

assert.equal(model.parseDiscovery("not json").receivers.length, 0)
assert.match(model.parseDiscovery("not json").error, /unreadable/)
assert.equal(model.parseEvent('{"event":"metrics","video_frames":30}').video_frames, 30)
assert.equal(model.parseEvent("bad"), null)
const workspaces = model.parseWorkspaces(JSON.stringify({ workspaces: [
  { name: "1", monitor: "eDP-1", windows: 2, active: true },
  { name: "web", monitor: "HovenCast-TV", windows: 1, casting: true }
] }))
assert.equal(workspaces.error, "")
assert.equal(workspaces.workspaces.length, 2)
assert.equal(workspaces.workspaces[0].active, true)
assert.equal(workspaces.workspaces[1].casting, true)
assert.match(model.parseWorkspaces("bad").error, /unreadable/)
const windows = model.parsePickerWindowList(
  "42[HC>]microsoft-edge[HT>]Video[HE>]6538cbb1ada0[HA>]"
  + "84[HC>]chatgpt[HT>]ChatGPT[HE>]6538caece100[HA>]"
)
assert.equal(windows.length, 2)
assert.equal(windows[0].handle, "42")
assert.equal(windows[0].title, "Video")
assert.equal(windows[1].appClass, "chatgpt")

const shareSources = model.parseShareSources(JSON.stringify({
  output: "eDP-1",
  description: "Built-in display",
  width: 1920,
  height: 1200,
  preview: "/tmp/preview.png",
  windows: [{
    handle: "0x1234",
    address: "0x1234",
    appClass: "chatgpt",
    title: '<img src="https://example.invalid/window-title-probe">',
    output: "eDP-1"
  }]
}))
assert.equal(shareSources.output, "eDP-1")
assert.equal(shareSources.windows.length, 1)
assert.equal(
  shareSources.windows[0].title,
  '<img src="https://example.invalid/window-title-probe">'
)

const panelSource = fs.readFileSync(path.join(__dirname, "..", "Panel.qml"), "utf8")
assert.match(qmlObjectBlock(panelSource, "Text", "applicationNameLabel"), /textFormat:\s*Text\.PlainText/)
assert.match(qmlObjectBlock(panelSource, "Text", "windowDescription"), /textFormat:\s*Text\.PlainText/)
assert.match(qmlObjectBlock(panelSource, "Text", "receiverNameLabel"), /textFormat:\s*Text\.PlainText/)
assert.match(qmlObjectBlock(panelSource, "Text", "receiverAddressLabel"), /textFormat:\s*Text\.PlainText/)

assert.equal(model.displayName("eDP-1", "BOE panel"), "Built-in display")
assert.equal(model.displayName("HDMI-A-1", "Living Room Display"), "Living Room Display")
assert.equal(model.protocolLabel("miracast-mice"), "MIRACAST · LOCAL NETWORK")
assert.equal(model.protocolName("miracast-mice"), "MIRACAST")
assert.equal(model.connectionLabel("miracast-mice"), "LOCAL NETWORK")
assert.equal(model.connectionLabel("miracast-p2p"), "WI-FI DIRECT")
assert.equal(model.resolutionLabel({ nativeHeight: 720 }), "720P")
assert.equal(model.humanBitrate(6100000), "6.1 Mbps")
assert.equal(model.stateLabel("streaming"), "SHARING YOUR SCREEN")
assert.equal(model.localPath("file:///tmp/HovenCast%20Test/omarchy-cast"), "/tmp/HovenCast Test/omarchy-cast")

assert.deepEqual(model.parseSetup('{"ready":true,"issues":[]}'), {ready: true, error: ""})
assert.deepEqual(model.parseSetup('{"ready":false,"issues":["Build helper","Set up portal"]}'),
  {ready: false, error: "Build helper\nSet up portal"})
assert.equal(model.parseSetup('{"ready":"true","issues":[]}').ready, false)
assert.equal(model.parseSetup('bad').ready, false)
console.log("model tests passed")

assert.equal(model.protocolName("airplay"), "AIRPLAY")
assert.equal(model.connectionLabel("airplay"), "LOCAL NETWORK")
assert.equal(model.parseDiscovery(JSON.stringify({receivers:[{name:"Apple TV",address:"airplay:00:11:22:33:44:55@192.0.2.1",protocol:"airplay",paired:true}]})).receivers[0].paired, true)
