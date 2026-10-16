function setupLaunchCommand(setupPath) {
  var path = String(setupPath)
  var directory = path.slice(0, path.lastIndexOf("/"))
  return ["omarchy", "launch", "terminal", "-e", "bash",
    directory + "/scripts/setup_terminal.sh", path]
}

function parseSetup(raw) {
  try {
    var result = JSON.parse(String(raw || "{}"))
    if (typeof result.ready !== "boolean" || !Array.isArray(result.issues))
      return { ready: false, error: "Setup status is unavailable. Try checking again." }
    return { ready: result.ready, error: result.issues.map(String).join("\n") }
  } catch (error) {
    return { ready: false, error: "Setup status could not be read. Try checking again." }
  }
}

function version() {
  return "2.5.0"
}

function buildTitle() {
  return "HovenCast v" + version()
}

function parseEndpoint(receiver) {
  return {
    name: String(receiver.name || receiver.address || ""),
    address: String(receiver.address || "").trim(),
    port: Number(receiver.port || 7250),
    interface: String(receiver.interface || ""),
    protocol: String(receiver.protocol || "miracast-mice"),
    nativeWidth: Number(receiver.native_width || 0),
    nativeHeight: Number(receiver.native_height || 0),
    paired: Boolean(receiver.paired),
    model: String(receiver.model || "")
  }
}

function endpointByAddress(receiver, address) {
  if (String(receiver.address || "") === String(address)) return receiver
  var endpoints = receiver.endpoints || []
  for (var i = 0; i < endpoints.length; i++)
    if (String(endpoints[i].address) === String(address)) return endpoints[i]
  return null
}

function connectionSummary(receiver) {
  var endpoints = receiver.endpoints || [receiver]
  var labels = []
  for (var i = 0; i < endpoints.length; i++) {
    var label = connectionLabel(endpoints[i].protocol)
    if (labels.indexOf(label) < 0) labels.push(label)
  }
  return labels.join(" + ")
}

function parseDiscovery(raw) {
  var result = { receivers: [], error: "" }
  try {
    var parsed = JSON.parse(String(raw || "{}"))
    if (!parsed || !Array.isArray(parsed.receivers)) {
      result.error = "The casting backend returned an invalid receiver list."
      return result
    }
    var seen = {}
    for (var i = 0; i < parsed.receivers.length; i++) {
      var receiver = parsed.receivers[i] || {}
      var address = String(receiver.address || "").trim()
      if (address === "" || seen[address]) continue
      seen[address] = true
      var row = parseEndpoint(receiver)
      if (Array.isArray(receiver.endpoints) && receiver.endpoints.length === 2) {
        var endpoints = receiver.endpoints.map(parseEndpoint)
        if (endpoints.every(function(endpoint) { return endpoint.address !== "" })
            && endpoints[0].address === row.address
            && endpoints[0].protocol === row.protocol
            && endpoints[1].address !== endpoints[0].address) row.endpoints = endpoints
      }
      result.receivers.push(row)
    }
  } catch (error) {
    result.error = "The casting backend returned unreadable discovery data."
  }
  return result
}

function parseEvent(raw) {
  try {
    var event = JSON.parse(String(raw || "{}"))
    return event && event.event ? event : null
  } catch (error) {
    return null
  }
}

function parseWorkspaces(raw) {
  var result = { workspaces: [], error: "" }
  try {
    var parsed = JSON.parse(String(raw || "{}"))
    if (!parsed || !Array.isArray(parsed.workspaces)) {
      result.error = "The workspace list is unavailable."
      return result
    }
    for (var i = 0; i < parsed.workspaces.length; i++) {
      var workspace = parsed.workspaces[i] || {}
      var name = String(workspace.name || "").trim()
      if (name === "") continue
      result.workspaces.push({
        name: name,
        monitor: String(workspace.monitor || ""),
        windows: Number(workspace.windows || 0),
        active: Boolean(workspace.active),
        casting: Boolean(workspace.casting)
      })
    }
  } catch (error) {
    result.error = "The workspace list is unreadable."
  }
  return result
}

function parsePickerWindowList(raw) {
  var result = []
  var source = String(raw || "")
  var offset = 0
  while (offset < source.length) {
    var classAt = source.indexOf("[HC>]", offset)
    var titleAt = classAt >= 0 ? source.indexOf("[HT>]", classAt + 5) : -1
    var endAt = titleAt >= 0 ? source.indexOf("[HE>]", titleAt + 5) : -1
    var addressAt = endAt >= 0 ? source.indexOf("[HA>]", endAt + 5) : -1
    if (classAt < 0 || titleAt < 0 || endAt < 0 || addressAt < 0) break
    var handle = source.slice(offset, classAt).trim()
    var appClass = source.slice(classAt + 5, titleAt).trim()
    var title = source.slice(titleAt + 5, endAt).trim()
    var address = source.slice(endAt + 5, addressAt).trim()
    if (handle !== "") {
      result.push({
        handle: handle,
        appClass: appClass,
        title: title !== "" ? title : (appClass !== "" ? appClass : "Application window"),
        address: address
      })
    }
    offset = addressAt + 5
  }
  return result
}

function parseShareSources(raw) {
  var result = {
    output: "",
    description: "",
    width: 0,
    height: 0,
    preview: "",
    windows: [],
    error: ""
  }
  try {
    var parsed = JSON.parse(String(raw || "{}"))
    result.output = String(parsed.output || "")
    result.description = String(parsed.description || "")
    result.width = Number(parsed.width || 0)
    result.height = Number(parsed.height || 0)
    result.preview = String(parsed.preview || "")
    var windows = Array.isArray(parsed.windows) ? parsed.windows : []
    for (var i = 0; i < windows.length; i++) {
      var window = windows[i] || {}
      var address = String(window.address || "").trim()
      if (address === "") continue
      result.windows.push({
        handle: String(window.handle || address),
        address: address,
        appClass: String(window.appClass || "Application"),
        title: String(window.title || window.appClass || "Application window"),
        output: String(window.output || result.output)
      })
    }
    if (result.output === "") result.error = "The active display is unavailable."
  } catch (error) {
    result.error = "Share sources could not be loaded."
  }
  return result
}

function displayName(output, description) {
  var name = String(output || "")
  if (/^(eDP|LVDS|DSI)-/i.test(name)) return "Built-in display"
  var detail = String(description || "").trim()
  return detail !== "" ? detail : (name !== "" ? name : "Display")
}

function protocolLabel(protocol) {
  if (String(protocol || "") === "miracast-mice") return "MIRACAST · LOCAL NETWORK"
  if (String(protocol || "") === "miracast-p2p") return "MIRACAST · WI-FI DIRECT"
  if (String(protocol || "") === "airplay") return "AIRPLAY"
  if (String(protocol || "") === "google-cast") return "GOOGLE CAST"
  return String(protocol || "WIRELESS DISPLAY").replace(/-/g, " ").toUpperCase()
}

function protocolName(protocol) {
  if (String(protocol || "") === "miracast-mice") return "MIRACAST"
  if (String(protocol || "") === "miracast-p2p") return "MIRACAST"
  if (String(protocol || "") === "airplay") return "AIRPLAY"
  if (String(protocol || "") === "google-cast") return "GOOGLE CAST"
  return String(protocol || "WIRELESS DISPLAY").replace(/-/g, " ").toUpperCase()
}

function connectionLabel(protocol) {
  if (String(protocol || "") === "miracast-mice") return "LOCAL NETWORK"
  if (String(protocol || "") === "miracast-p2p") return "WI-FI DIRECT"
  if (String(protocol || "") === "airplay") return "LOCAL NETWORK"
  if (String(protocol || "") === "google-cast") return "LOCAL NETWORK"
  return "WIRELESS"
}

function resolutionLabel(receiver) {
  if (!receiver || Number(receiver.nativeHeight || 0) <= 0) return ""
  return Number(receiver.nativeHeight) + "P"
}

function humanBitrate(bitsPerSecond) {
  var value = Number(bitsPerSecond)
  if (!isFinite(value) || value <= 0) return ""
  if (value >= 1000000) return (Math.round(value / 100000) / 10) + " Mbps"
  if (value >= 1000) return Math.round(value / 1000) + " Kbps"
  return Math.round(value) + " bps"
}

function stateLabel(state) {
  switch (String(state || "idle")) {
    case "discovering": return "LOOKING FOR DISPLAYS"
    case "awaiting-portal": return "CHOOSE A SCREEN TO SHARE"
    case "connecting": return "CONNECTING"
    case "streaming": return "SHARING YOUR SCREEN"
    case "stopping": return "STOPPING"
    case "error": return "CONNECTION FAILED"
    default: return "READY TO CONNECT"
  }
}

function localPath(url) {
  var value = String(url || "")
  if (value.indexOf("file://") === 0) value = value.slice(7)
  try { return decodeURIComponent(value) } catch (error) { return value }
}

if (typeof module !== "undefined") {
  module.exports = {
    version: version,
    buildTitle: buildTitle,
    endpointByAddress: endpointByAddress,
    connectionSummary: connectionSummary,
    parseSetup: parseSetup,
    setupLaunchCommand: setupLaunchCommand,
    parseDiscovery: parseDiscovery,
    parseEvent: parseEvent,
    parseWorkspaces: parseWorkspaces,
    parsePickerWindowList: parsePickerWindowList,
    parseShareSources: parseShareSources,
    displayName: displayName,
    protocolLabel: protocolLabel,
    protocolName: protocolName,
    connectionLabel: connectionLabel,
    resolutionLabel: resolutionLabel,
    humanBitrate: humanBitrate,
    stateLabel: stateLabel,
    localPath: localPath
  }
}
