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
      result.receivers.push({
        name: String(receiver.name || address),
        address: address,
        port: Number(receiver.port || 7250),
        interface: String(receiver.interface || ""),
        protocol: String(receiver.protocol || "miracast-mice"),
        nativeWidth: Number(receiver.native_width || 0),
        nativeHeight: Number(receiver.native_height || 0),
        model: String(receiver.model || "")
      })
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

function displayName(output, description) {
  var name = String(output || "")
  if (/^(eDP|LVDS|DSI)-/i.test(name)) return "Built-in display"
  var detail = String(description || "").trim()
  return detail !== "" ? detail : (name !== "" ? name : "Display")
}

function protocolLabel(protocol) {
  if (String(protocol || "") === "miracast-mice") return "MIRACAST · LOCAL NETWORK"
  if (String(protocol || "") === "miracast-p2p") return "MIRACAST · WI-FI DIRECT"
  if (String(protocol || "") === "google-cast") return "GOOGLE CAST"
  return String(protocol || "WIRELESS DISPLAY").replace(/-/g, " ").toUpperCase()
}

function protocolName(protocol) {
  if (String(protocol || "") === "miracast-mice") return "MIRACAST"
  if (String(protocol || "") === "miracast-p2p") return "MIRACAST"
  if (String(protocol || "") === "google-cast") return "GOOGLE CAST"
  return String(protocol || "WIRELESS DISPLAY").replace(/-/g, " ").toUpperCase()
}

function connectionLabel(protocol) {
  if (String(protocol || "") === "miracast-mice") return "LOCAL NETWORK"
  if (String(protocol || "") === "miracast-p2p") return "WI-FI DIRECT"
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
    parseDiscovery: parseDiscovery,
    parseEvent: parseEvent,
    parsePickerWindowList: parsePickerWindowList,
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
