import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Hyprland
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root
  moduleName: "hoven.cast"
  ipcTarget: "hoven.cast"
  manageIpc: false

  property var receivers: []
  property int selectedIndex: 0
  property bool cursorActive: false
  property string sessionState: "idle"
  property string activeAddress: ""
  property string activeName: ""
  property string lastError: ""
  property string discoverError: ""
  property string stderrTail: ""
  property bool expectedStop: false
  property bool keepLocalAudio: Boolean(setting("keepLocalAudio", false))
  property int videoFrames: 0
  property int audioBuffers: 0
  property int bitrateBps: 0
  property real avDriftMs: 0
  property bool sourcePickerVisible: false
  property string sourcePickerResult: "idle"
  property string sourcePickerPage: "ready"
  property string sourceOutput: ""
  property string sourceDescription: ""
  property int sourceWidth: 0
  property int sourceHeight: 0
  property string sourcePreviewPath: ""
  property int sourcePreviewNonce: 0
  property var sourceWindows: []
  property string sourceSelection: "screen"
  property string sourceSelectedWindowHandle: ""
  property int sourceCursor: 3
  property int sourceWindowIndex: 0
  property string sourceMode: "workspace"
  property string displayPlacement: String(setting("displayPlacement", "right"))
  property bool displayPlacementMenuOpen: false
  property var availableWorkspaces: []
  property int selectedWorkspaceIndex: 0
  property string activeWorkspaceName: ""
  property string workspaceError: ""

  readonly property string backendCommand: Model.localPath(Qt.resolvedUrl("omarchy-cast"))
  readonly property string version: Model.version()
  readonly property bool sessionActive: sessionState === "awaiting-portal"
    || sessionState === "connecting"
    || sessionState === "streaming"
    || sessionState === "stopping"
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.45)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function persistAudioPreference(enabled) {
    keepLocalAudio = enabled
    if (!bar || !bar.shell || typeof bar.shell.updateEntryInline !== "function") return
    var entry = { id: moduleName }
    for (var key in settings) if (key !== "id") entry[key] = settings[key]
    entry.keepLocalAudio = enabled
    bar.shell.updateEntryInline(moduleName, entry)
  }

  function normalizedDisplayPlacement(value) {
    var placement = String(value || "right").toLowerCase()
    return ["left", "above", "below", "right"].indexOf(placement) >= 0
      ? placement : "right"
  }

  function displayPlacementLabel(value) {
    switch (normalizedDisplayPlacement(value)) {
      case "left": return "←  Left"
      case "above": return "↑  Above"
      case "below": return "↓  Below"
      default: return "Right  →"
    }
  }

  function persistDisplayPlacement(value) {
    displayPlacement = normalizedDisplayPlacement(value)
    displayPlacementMenuOpen = false
    if (!bar || !bar.shell || typeof bar.shell.updateEntryInline !== "function") return
    var entry = { id: moduleName }
    for (var key in settings) if (key !== "id") entry[key] = settings[key]
    entry.displayPlacement = displayPlacement
    bar.shell.updateEntryInline(moduleName, entry)
  }

  function refresh() {
    if (discoverProc.running || sessionActive) return
    discoverError = ""
    sessionState = "discovering"
    discoverProc.command = [backendCommand, "discover"]
    discoverProc.running = true
    refreshWorkspaces()
  }

  function refreshWorkspaces() {
    if (workspaceListProc.running) return
    workspaceListProc.command = [backendCommand, "workspace-list"]
    workspaceListProc.running = true
  }

  function selectedWorkspaceName() {
    if (selectedWorkspaceIndex < 0 || selectedWorkspaceIndex >= availableWorkspaces.length)
      return ""
    return String(availableWorkspaces[selectedWorkspaceIndex].name || "")
  }

  function chooseWorkspace(index) {
    if (index < 0 || index >= availableWorkspaces.length) return
    selectedWorkspaceIndex = index
    if (sessionState === "streaming" && sourceMode === "workspace") {
      activeWorkspaceName = String(availableWorkspaces[index].name || "")
      workspaceActionProc.command = [backendCommand, "workspace", "set", activeWorkspaceName]
      workspaceActionProc.running = true
    }
  }

  function focusTvWorkspace() {
    workspaceActionProc.command = [backendCommand, "workspace", "focus-tv"]
    workspaceActionProc.running = true
  }

  function focusLaptop() {
    workspaceActionProc.command = [backendCommand, "workspace", "focus-local"]
    workspaceActionProc.running = true
  }

  function connectReceiver(receiver) {
    if (!receiver || sessionActive || castProc.running) return
    lastError = ""
    stderrTail = ""
    activeAddress = String(receiver.address || "")
    activeName = String(receiver.name || activeAddress)
    videoFrames = 0
    audioBuffers = 0
    bitrateBps = 0
    avDriftMs = 0
    expectedStop = false
    sessionState = "awaiting-portal"
    var command = [backendCommand, "session-start", "--address", activeAddress,
                   "--receiver-name", activeName, "--duration", "0",
                   "--ipc-target", ipcTarget]
    if (Number(receiver.nativeHeight || 0) === 720) command.push("--quality", "720p")
    else if (Number(receiver.nativeHeight || 0) === 1080) command.push("--quality", "1080p")
    if (sourceMode === "workspace") {
      var workspace = selectedWorkspaceName()
      if (workspace === "") {
        lastError = "Choose a workspace before connecting."
        sessionState = "error"
        return
      }
      activeWorkspaceName = workspace
      command.push("--workspace", workspace)
      command.push("--placement", normalizedDisplayPlacement(displayPlacement))
    }
    if (keepLocalAudio) command.push("--keep-local-audio")
    castProc.command = command
    castProc.running = true
  }

  function stopCasting() {
    if (sourcePickerVisible) sourcePickerResult = "cancel"
    if (stopProc.running) return
    if (!sessionActive) {
      sessionState = "idle"
      activeAddress = ""
      activeName = ""
      return
    }
    expectedStop = true
    sessionState = "stopping"
    stopProc.command = [backendCommand, "session-stop"]
    stopProc.running = true
  }

  function pollSession() {
    if (sessionStatusProc.running) return
    sessionStatusProc.command = [backendCommand, "session-status"]
    sessionStatusProc.running = true
  }

  function applySessionStatus(raw) {
    var status
    try {
      status = JSON.parse(String(raw || "{}"))
    } catch (error) {
      return
    }
    if (Boolean(status.running)) {
      activeAddress = String(status.address || activeAddress)
      activeName = String(status.receiver || activeName || activeAddress)
      activeWorkspaceName = String(status.workspace || activeWorkspaceName)
      if (activeWorkspaceName !== "") {
        sourceMode = "workspace"
        displayPlacement = normalizedDisplayPlacement(status.placement || displayPlacement)
      }
      videoFrames = Number(status.video_frames || 0)
      audioBuffers = Number(status.audio_buffers || 0)
      bitrateBps = Number(status.bitrate_bps || 0)
      avDriftMs = Number(status.av_drift_ms || 0)
      sessionState = String(status.state || "connecting")
      return
    }
    if (expectedStop || sessionActive || String(status.state || "") === "error") {
      if (!expectedStop && String(status.state || "") === "error") {
        lastError = String(status.error || "The wireless display session ended unexpectedly.")
        sessionState = "error"
      } else {
        lastError = ""
        sessionState = "idle"
      }
      expectedStop = false
      activeAddress = ""
      activeName = ""
      refreshWorkspaces()
    }
  }

  function openSourcePicker(output, description, width, height, previewPath, windowsB64) {
    sourceOutput = String(output || "")
    sourceDescription = String(description || "")
    sourceWidth = Number(width || 0)
    sourceHeight = Number(height || 0)
    sourcePreviewPath = String(previewPath || "")
    sourcePreviewNonce += 1
    sourceWindows = Model.parsePickerWindowList(Util.decodeBase64(String(windowsB64 || "")))
    sourcePickerPage = sourceMode === "window" ? "windows" : "ready"
    sourceSelection = sourceMode === "window" ? "window" : "screen"
    sourceSelectedWindowHandle = ""
    sourceCursor = 3
    sourceWindowIndex = 0
    sourcePickerResult = "pending"
    sourcePickerVisible = true
    sessionState = "awaiting-portal"
    root.open()
    return "ok"
  }

  function finishSourcePicker(selection) {
    sourcePickerResult = String(selection || "cancel")
    sourcePickerVisible = false
    sourcePickerPage = "ready"
  }

  function cancelSourcePicker() {
    if (!sourcePickerVisible) return
    sourcePickerResult = "cancel"
    sourcePickerVisible = false
    sourcePickerPage = "ready"
    expectedStop = true
    sessionState = "stopping"
  }

  function closeSourceView() {
    if (sourcePickerPage === "windows") {
      sourcePickerPage = "ready"
      sourceCursor = 1
    } else {
      cancelSourcePicker()
      root.close()
    }
  }

  function moveSourceCursor(delta) {
    if (sourcePickerPage === "windows") {
      if (sourceWindows.length > 0)
        sourceWindowIndex = Math.max(0, Math.min(sourceWindows.length - 1, sourceWindowIndex + delta))
      return
    }
    sourceCursor = Math.max(0, Math.min(3, sourceCursor + delta))
  }

  function chooseSourceWindow(index) {
    if (index < 0 || index >= sourceWindows.length) return
    sourceWindowIndex = index
    sourceSelectedWindowHandle = String(sourceWindows[index].handle || "")
    if (sourceSelectedWindowHandle === "") return
    sourceSelection = "window"
    finishSourcePicker("window:" + sourceSelectedWindowHandle)
  }

  function startSelectedSource() {
    if (sourceSelection === "window") {
      if (sourceSelectedWindowHandle === "") {
        sourcePickerPage = "windows"
        return
      }
      finishSourcePicker("window:" + sourceSelectedWindowHandle)
      return
    }
    if (sourceSelection === "area") {
      finishSourcePicker("area")
      root.close()
      return
    }
    finishSourcePicker("screen:" + sourceOutput)
  }

  function sourcePickerMetadata() {
    var metadata = {
      kind: sourceSelection === "area" ? "region" : sourceSelection,
      output: sourceOutput,
      outputWidth: sourceWidth,
      outputHeight: sourceHeight
    }
    if (sourceSelection === "window"
        && sourceWindowIndex >= 0
        && sourceWindowIndex < sourceWindows.length) {
      var selected = sourceWindows[sourceWindowIndex] || {}
      metadata.windowHandle = String(selected.handle || "")
      metadata.windowAddress = String(selected.address || "")
      metadata.windowClass = String(selected.appClass || "")
      metadata.windowTitle = String(selected.title || "")
    }
    return JSON.stringify(metadata)
  }

  function activateSourceCursor() {
    if (sourcePickerPage === "windows") {
      if (sourceWindows.length > 0) chooseSourceWindow(sourceWindowIndex)
      return
    }
    if (sourceCursor === 0) sourceSelection = "screen"
    else if (sourceCursor === 1) sourcePickerPage = "windows"
    else if (sourceCursor === 2) sourceSelection = "area"
    else startSelectedSource()
  }

  function handleCastLine(line) {
    var event = Model.parseEvent(line)
    if (!event) return
    switch (String(event.event || "")) {
      case "portal-requested":
        sessionState = "awaiting-portal"
        break
      case "virtual-workspace-started":
        sourceMode = "workspace"
        activeWorkspaceName = String(event.workspace || "")
        sessionState = "connecting"
        break
      case "portal-started":
      case "sender-started":
      case "pipeline-started":
        sessionState = "connecting"
        break
      case "first-video":
        sessionState = "streaming"
        break
      case "metrics":
        videoFrames = Number(event.video_frames || 0)
        audioBuffers = Number(event.audio_buffers || 0)
        bitrateBps = Number(event.bitrate_bps || 0)
        avDriftMs = Number(event.av_drift_ms || 0)
        if (videoFrames > 0) sessionState = "streaming"
        break
      case "portal-create-failed":
      case "portal-start-failed":
      case "audio-route-failed":
      case "sender-start-failed":
      case "pipeline-create-failed":
      case "pipeline-error":
      case "transport-process-exited":
      case "output-stalled":
        if (expectedStop) break
        lastError = String(event.error || "The wireless display session failed.")
        sessionState = "error"
        break
      case "stopped":
        if (!expectedStop && sessionState !== "error" && String(event.reason || "") !== "eos") {
          lastError = "The wireless display session ended: " + String(event.reason || "unknown reason")
          sessionState = "error"
        }
        break
    }
  }

  function selectReceiver(index) {
    if (index < 0 || index >= receivers.length) return
    cursorActive = true
    selectedIndex = index
  }

  function moveCursor(delta) {
    if (receivers.length === 0) return
    cursorActive = true
    selectedIndex = Math.max(0, Math.min(receivers.length - 1, selectedIndex + delta))
  }

  function activateCursor() {
    if (sessionActive) {
      stopCasting()
      return
    }
    if (receivers.length > 0) connectReceiver(receivers[selectedIndex])
  }

  function statusJson() {
    return JSON.stringify({
      state: sessionState,
      receiver: activeName,
      address: activeAddress,
      keepLocalAudio: keepLocalAudio,
      videoFrames: videoFrames,
      audioBuffers: audioBuffers,
      bitrateBps: bitrateBps,
      avDriftMs: avDriftMs,
      sourcePickerVisible: sourcePickerVisible,
      sourcePickerPage: sourcePickerPage,
      sourcePickerResult: sourcePickerResult,
      error: lastError
    })
  }

  Component.onCompleted: {
    displayPlacement = normalizedDisplayPlacement(displayPlacement)
    pollSession()
    refresh()
  }

  onOpenedChanged: {
    if (!opened) displayPlacementMenuOpen = false
    if (!opened && sourcePickerVisible && sourcePickerResult === "pending") cancelSourcePicker()
    if (opened && !sessionActive) refresh()
    if (opened) cursorActive = false
    if (opened) refreshWorkspaces()
  }

  Timer {
    interval: 15000
    repeat: true
    running: root.opened && !root.sessionActive
    onTriggered: root.refresh()
  }

  Timer {
    interval: 1000
    repeat: true
    running: true
    onTriggered: root.pollSession()
  }

  Process {
    id: discoverProc
    stdout: StdioCollector {
      id: discoverStdout
      waitForEnd: true
      onStreamFinished: {
        var result = Model.parseDiscovery(text)
        root.receivers = result.receivers
        root.discoverError = result.error
        if (root.selectedIndex >= root.receivers.length)
          root.selectedIndex = Math.max(0, root.receivers.length - 1)
      }
    }
    stderr: StdioCollector {
      id: discoverStderr
      waitForEnd: true
    }
    onExited: function(exitCode, exitStatus) {
      if (root.sessionActive) return
      root.sessionState = "idle"
      if (exitCode !== 0) {
        root.discoverError = String(discoverStderr.text || "").trim()
          || "Could not search for wireless displays."
      }
    }
  }

  Process {
    id: workspaceListProc
    stdout: StdioCollector {
      id: workspaceListStdout
      waitForEnd: true
      onStreamFinished: {
        var result = Model.parseWorkspaces(text)
        root.availableWorkspaces = result.workspaces
        root.workspaceError = result.error
        var activeIndex = 0
        for (var i = 0; i < result.workspaces.length; i++) {
          if (result.workspaces[i].casting
              || (!root.sessionActive && result.workspaces[i].active)) activeIndex = i
        }
        root.selectedWorkspaceIndex = Math.max(0, Math.min(activeIndex, result.workspaces.length - 1))
      }
    }
    stderr: StdioCollector {
      id: workspaceListStderr
      waitForEnd: true
    }
    onExited: function(exitCode, exitStatus) {
      if (exitCode !== 0)
        root.workspaceError = String(workspaceListStderr.text || "").trim()
          || "Could not list Hyprland workspaces."
    }
  }

  Process {
    id: workspaceActionProc
    stdout: StdioCollector { waitForEnd: true }
    stderr: StdioCollector {
      id: workspaceActionStderr
      waitForEnd: true
    }
    onExited: function(exitCode, exitStatus) {
      if (exitCode !== 0)
        root.workspaceError = String(workspaceActionStderr.text || "").trim()
          || "Could not control the TV workspace."
      root.refreshWorkspaces()
    }
  }

  Process {
    id: castProc
    stdout: StdioCollector { waitForEnd: true }
    stderr: StdioCollector {
      id: castStderr
      waitForEnd: true
    }
    onExited: function(exitCode, exitStatus) {
      if (exitCode !== 0) {
        root.lastError = String(castStderr.text || "").trim()
          || "The wireless display process exited with status " + exitCode + "."
        root.sessionState = "error"
      } else {
        root.sessionState = "connecting"
        root.pollSession()
      }
    }
  }

  Process {
    id: sessionStatusProc
    stdout: StdioCollector {
      id: sessionStatusStdout
      waitForEnd: true
      onStreamFinished: root.applySessionStatus(text)
    }
    stderr: StdioCollector { waitForEnd: true }
  }

  Process {
    id: stopProc
    stdout: StdioCollector { waitForEnd: true }
    stderr: StdioCollector {
      id: stopStderr
      waitForEnd: true
    }
    onExited: function(exitCode, exitStatus) {
      if (exitCode !== 0) {
        root.lastError = String(stopStderr.text || "").trim()
          || "Could not stop the wireless display session."
        root.sessionState = "error"
        root.expectedStop = false
      }
      root.pollSession()
    }
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { root.refresh(); return "ok" }
    function stop(): string { root.stopCasting(); return "ok" }
    function status(): string { return root.statusJson() }
    function pickerOpen(output: string, description: string, width: string,
                        height: string, previewPath: string, windowsB64: string): string {
      return root.openSourcePicker(output, description, width, height, previewPath, windowsB64)
    }
    function pickerResult(): string { return root.sourcePickerResult }
    function pickerMetadata(): string { return root.sourcePickerMetadata() }
    function pickerClose(): string {
      root.sourcePickerVisible = false
      root.sourcePickerPage = "ready"
      return "ok"
    }
    function connect(address: string): string {
      for (var i = 0; i < root.receivers.length; i++) {
        if (String(root.receivers[i].address) === address) {
          root.connectReceiver(root.receivers[i])
          return "ok"
        }
      }
      return "receiver not found"
    }
  }

  // Local chrome keeps the shell palette and existing interaction states.
  component CastSurface: CursorSurface {
    radius: 0
  }

  component CastSectionHeader: PanelSectionHeader {
    font.letterSpacing: 1.5
  }

  component WorkspacePreview: Item {
    id: workspacePreview

    property string workspaceName: ""
    property bool live: false
    readonly property var workspace: {
      var values = Hyprland.workspaces.values
      for (var i = 0; i < values.length; i++) {
        if (String(values[i].name) === workspaceName) return values[i]
      }
      return null
    }
    readonly property var monitor: workspace ? workspace.monitor : null

    Rectangle {
      anchors.fill: parent
      color: Qt.alpha(root.foreground, 0.06)
    }

    clip: true

    Repeater {
      model: workspacePreview.workspace ? workspacePreview.workspace.toplevels : null

      ScreencopyView {
        required property var modelData
        readonly property var ipc: modelData.lastIpcObject || ({})
        readonly property var position: ipc.at || [0, 0]
        readonly property var dimensions: ipc.size || [1, 1]
        readonly property real monitorScale: workspacePreview.monitor
          ? Math.max(0.1, workspacePreview.monitor.scale) : 1
        readonly property real monitorWidth: workspacePreview.monitor
          ? Math.max(1, workspacePreview.monitor.width / monitorScale) : 1280
        readonly property real monitorHeight: workspacePreview.monitor
          ? Math.max(1, workspacePreview.monitor.height / monitorScale) : 720
        readonly property real monitorX: workspacePreview.monitor
          ? workspacePreview.monitor.x : 0
        readonly property real monitorY: workspacePreview.monitor
          ? workspacePreview.monitor.y : 0

        x: Math.round((Number(position[0]) - monitorX)
          / monitorWidth * workspacePreview.width)
        y: Math.round((Number(position[1]) - monitorY)
          / monitorHeight * workspacePreview.height)
        width: Math.max(1, Math.round(Number(dimensions[0])
          / monitorWidth * workspacePreview.width))
        height: Math.max(1, Math.round(Number(dimensions[1])
          / monitorHeight * workspacePreview.height))
        z: 1000 - Number(ipc.focusHistoryID || 999)
        captureSource: modelData.wayland
        live: workspacePreview.live && captureSource !== null
        paintCursor: false
        constraintSize: Qt.size(width, height)
      }
    }
  }

  component WindowPreview: Item {
    id: windowPreview

    property string windowAddress: ""
    property bool live: false
    readonly property var toplevel: {
      var requested = String(windowAddress).toLowerCase().replace(/^0x/, "")
      var values = Hyprland.toplevels.values
      for (var i = 0; i < values.length; i++) {
        var address = String(values[i].address).toLowerCase().replace(/^0x/, "")
        if (address === requested) return values[i]
      }
      return null
    }

    Rectangle {
      anchors.fill: parent
      color: Qt.alpha(root.foreground, 0.06)
    }

    ScreencopyView {
      anchors.fill: parent
      captureSource: windowPreview.toplevel ? windowPreview.toplevel.wayland : null
      live: windowPreview.live && captureSource !== null
      paintCursor: false
      constraintSize: Qt.size(width, height)
    }

    WindowSourceIcon {
      visible: !windowPreview.toplevel || !windowPreview.toplevel.wayland
      anchors.centerIn: parent
      iconColor: root.dim
    }
  }

  component HovenCastSymbol: Canvas {
    property color iconColor: root.foreground

    implicitWidth: Style.font.display
    implicitHeight: Style.font.display

    onIconColorChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()

    onPaint: {
      var ctx = getContext("2d")
      ctx.reset()
      ctx.scale(width / 24, height / 24)
      ctx.strokeStyle = iconColor
      ctx.fillStyle = iconColor
      ctx.lineWidth = 2.15
      ctx.lineCap = "round"
      ctx.lineJoin = "round"

      ctx.beginPath()
      ctx.moveTo(3.25, 9)
      ctx.lineTo(3.25, 5.25)
      ctx.quadraticCurveTo(3.25, 3.25, 5.25, 3.25)
      ctx.lineTo(18.75, 3.25)
      ctx.quadraticCurveTo(20.75, 3.25, 20.75, 5.25)
      ctx.lineTo(20.75, 17.75)
      ctx.quadraticCurveTo(20.75, 19.75, 18.75, 19.75)
      ctx.lineTo(15, 19.75)
      ctx.stroke()

      ctx.beginPath()
      ctx.arc(3.25, 20.25, 5.75, -Math.PI / 2, 0, false)
      ctx.stroke()

      ctx.beginPath()
      ctx.arc(3.25, 20.25, 10, -Math.PI / 2, 0, false)
      ctx.stroke()

      ctx.beginPath()
      ctx.arc(3.35, 20.15, 1.45, 0, Math.PI * 2, false)
      ctx.fill()
    }
  }

  component WindowSourceIcon: Canvas {
    property color iconColor: root.foreground

    implicitWidth: Style.space(24)
    implicitHeight: Style.space(24)
    onIconColorChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
      var ctx = getContext("2d")
      ctx.reset()
      ctx.scale(width / 24, height / 24)
      ctx.strokeStyle = iconColor
      ctx.lineWidth = 1.8
      ctx.lineJoin = "round"
      ctx.strokeRect(3, 4, 18, 16)
      ctx.beginPath()
      ctx.moveTo(3, 8)
      ctx.lineTo(21, 8)
      ctx.stroke()
    }
  }

  component AreaSourceIcon: Canvas {
    property color iconColor: root.foreground

    implicitWidth: Style.space(24)
    implicitHeight: Style.space(24)
    onIconColorChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
      var ctx = getContext("2d")
      ctx.reset()
      ctx.scale(width / 24, height / 24)
      ctx.strokeStyle = iconColor
      ctx.lineWidth = 1.8
      ctx.lineCap = "square"
      ctx.beginPath()
      ctx.moveTo(9, 4); ctx.lineTo(4, 4); ctx.lineTo(4, 9)
      ctx.moveTo(15, 4); ctx.lineTo(20, 4); ctx.lineTo(20, 9)
      ctx.moveTo(20, 15); ctx.lineTo(20, 20); ctx.lineTo(15, 20)
      ctx.moveTo(9, 20); ctx.lineTo(4, 20); ctx.lineTo(4, 15)
      ctx.stroke()
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: ""
    iconComponent: Component {
      HovenCastSymbol {
        anchors.fill: parent
        iconColor: root.sessionState === "error"
          ? root.urgent
          : (root.sessionActive ? root.foreground : root.dim)
      }
    }
    foreground: root.sessionActive ? root.foreground : root.dim
    active: root.sessionState === "error"
    activeColor: root.urgent
    tooltipText: root.sessionActive
      ? "HovenCast v" + root.version + " · sharing"
      : "HovenCast v" + root.version
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton && root.sessionActive) root.stopCasting()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    padding: Style.space(24)
    contentWidth: panel.fittedContentWidth(Style.space(560))
    contentHeight: panel.fittedContentHeight(content.implicitHeight, Style.space(800))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (root.sourcePickerVisible) {
          root.moveSourceCursor(dy !== 0 ? dy : dx)
          return
        }
        if (!root.cursorActive) { root.cursorActive = true; return }
        if (dy !== 0) root.moveCursor(dy)
      }
      onActivateRequested: root.sourcePickerVisible ? root.activateSourceCursor() : root.activateCursor()
      onCloseRequested: root.sourcePickerVisible ? root.closeSourceView() : root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(text) {
        if (text === "r" || text === "R") root.refresh()
        else if ((text === "s" || text === "S") && root.sessionActive) root.stopCasting()
        else if ((text === "t" || text === "T") && root.sessionState === "streaming"
                 && root.sourceMode === "workspace") root.focusTvWorkspace()
        else if ((text === "l" || text === "L") && root.sessionState === "streaming"
                 && root.sourceMode === "workspace") root.focusLaptop()
      }

      Flickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: content.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: content
          width: parent.width
          spacing: Style.space(12)

          Column {
            id: sourcePickerContent
            visible: root.sourcePickerVisible
            width: parent.width
            spacing: Style.space(18)

            PanelHero {
              width: parent.width
              title: "HovenCast v" + root.version
              meta: root.sourcePickerPage === "windows" ? "CHOOSE A WINDOW" : "READY TO SHARE"
              foreground: root.foreground
              fontFamily: root.fontFamily
              iconComponent: Component {
                HovenCastSymbol {
                  iconColor: root.foreground
                }
              }
              trailingControl: Component {
                PanelActionButton {
                  iconText: root.sourcePickerPage === "windows" ? "←" : "×"
                  tooltipText: root.sourcePickerPage === "windows" ? "Back" : "Cancel"
                  foreground: root.foreground
                  hoverColor: root.foreground
                  fontFamily: root.fontFamily
                  bordered: true
                  onClicked: root.closeSourceView()
                }
              }
            }

            PanelSeparator { foreground: root.foreground }

            Text {
              visible: root.sourcePickerPage === "ready"
              width: parent.width
              text: "Choose what to share, then press Start sharing."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              wrapMode: Text.WordWrap
            }

            CastSurface {
              id: displayPreview
              visible: root.sourcePickerPage === "ready"
              width: parent.width
              implicitHeight: Style.space(220)
              hasCursor: root.sourceCursor === 0
              current: root.sourceSelection === "screen"
              bordered: true
              foreground: root.foreground
              accent: root.urgent
              borderSpec: current
                ? Border.flat(root.urgent, 2)
                : (hasCursor
                  ? Border.controlSpec("hover-cursor", foreground, accent)
                  : Border.controlSpec("normal", foreground, accent))

              Image {
                id: previewImage
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.bottom: previewLabels.top
                anchors.margins: Style.space(8)
                anchors.bottomMargin: Style.space(6)
                source: root.sourcePreviewPath !== ""
                  ? "file://" + root.sourcePreviewPath + "?v=" + root.sourcePreviewNonce : ""
                cache: false
                asynchronous: true
                fillMode: Image.PreserveAspectCrop
                sourceSize.width: 640
                sourceSize.height: 360
              }

              Text {
                visible: previewImage.status !== Image.Ready
                anchors.centerIn: previewImage
                textFormat: Text.PlainText
                text: "\uDB80\uDF79"
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
              }

              Column {
                id: previewLabels
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.leftMargin: Style.space(14)
                anchors.rightMargin: Style.space(14)
                anchors.bottomMargin: Style.space(9)
                spacing: Style.space(4)

                Text {
                  width: parent.width
                  horizontalAlignment: Text.AlignHCenter
                  text: Model.displayName(root.sourceOutput, root.sourceDescription)
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                  elide: Text.ElideRight
                }

                Text {
                  width: parent.width
                  horizontalAlignment: Text.AlignHCenter
                  text: root.sourceWidth + " × " + root.sourceHeight
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }

              HoverHandler { onHoveredChanged: if (hovered) root.sourceCursor = 0 }
              TapHandler {
                onTapped: {
                  root.sourceCursor = 0
                  root.sourceSelection = "screen"
                }
              }
            }

            RowLayout {
              visible: root.sourcePickerPage === "ready"
              width: parent.width
              spacing: Style.space(8)

              CastSurface {
                id: windowSourceOption
                Layout.fillWidth: true
                implicitHeight: Style.space(48)
                hasCursor: root.sourceCursor === 1
                current: root.sourceSelection === "window"
                bordered: true
                foreground: root.foreground
                accent: root.urgent
                borderSpec: current
                  ? Border.flat(root.urgent, 2)
                  : (hasCursor
                    ? Border.controlSpec("hover-cursor", foreground, accent)
                    : Border.controlSpec("normal", foreground, accent))

                Row {
                  anchors.centerIn: parent
                  spacing: Style.space(8)
                  WindowSourceIcon {
                    iconColor: root.foreground
                    anchors.verticalCenter: parent.verticalCenter
                  }
                  Text {
                    text: "Choose a window"
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    anchors.verticalCenter: parent.verticalCenter
                  }
                }
                HoverHandler { onHoveredChanged: if (hovered) root.sourceCursor = 1 }
                TapHandler {
                  onTapped: {
                    root.sourceCursor = 1
                    root.sourcePickerPage = "windows"
                  }
                }
              }

              CastSurface {
                id: areaSourceOption
                Layout.fillWidth: true
                implicitHeight: Style.space(48)
                hasCursor: root.sourceCursor === 2
                current: root.sourceSelection === "area"
                bordered: true
                foreground: root.foreground
                accent: root.urgent
                borderSpec: current
                  ? Border.flat(root.urgent, 2)
                  : (hasCursor
                    ? Border.controlSpec("hover-cursor", foreground, accent)
                    : Border.controlSpec("normal", foreground, accent))

                Row {
                  anchors.centerIn: parent
                  spacing: Style.space(8)
                  AreaSourceIcon {
                    iconColor: root.foreground
                    anchors.verticalCenter: parent.verticalCenter
                  }
                  Text {
                    text: "Select an area"
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    anchors.verticalCenter: parent.verticalCenter
                  }
                }
                HoverHandler { onHoveredChanged: if (hovered) root.sourceCursor = 2 }
                TapHandler {
                  onTapped: {
                    root.sourceCursor = 2
                    root.sourceSelection = "area"
                  }
                }
              }
            }

            CastSurface {
              visible: root.sourcePickerPage === "ready"
              width: parent.width
              implicitHeight: Style.space(52)
              hasCursor: root.sourceCursor === 3
              bordered: true
              foreground: root.foreground
              accent: root.urgent

              Row {
                anchors.centerIn: parent
                spacing: Style.space(9)
                Text {
                  text: "Start sharing"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                }
                Text {
                  textFormat: Text.PlainText
                  text: "→"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.icon
                }
              }
              HoverHandler { onHoveredChanged: if (hovered) root.sourceCursor = 3 }
              TapHandler {
                onTapped: {
                  root.sourceCursor = 3
                  root.startSelectedSource()
                }
              }
            }

            Text {
              visible: root.sourcePickerPage === "windows"
              width: parent.width
              text: root.sourceWindows.length > 0
                ? "Choose the application window you want to show. Sharing starts when you select it."
                : "No shareable application windows are open."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              wrapMode: Text.WordWrap
            }

            Grid {
              id: sourceWindowGrid
              visible: root.sourcePickerPage === "windows"
              width: parent.width
              columns: 2
              spacing: Style.space(10)
              property real uniformDescriptionHeight: Math.ceil(Style.font.bodySmall * 3.2)

              function updateDescriptionHeight() {
                var nextHeight = Math.ceil(Style.font.bodySmall * 3.2)
                for (var i = 0; i < sourceWindowRepeater.count; i++) {
                  var card = sourceWindowRepeater.itemAt(i)
                  if (card) nextHeight = Math.max(nextHeight, card.descriptionNaturalHeight)
                }
                uniformDescriptionHeight = nextHeight
              }

              Repeater {
                id: sourceWindowRepeater
                model: root.sourceWindows
                onCountChanged: Qt.callLater(sourceWindowGrid.updateDescriptionHeight)

                CastSurface {
                  id: sourceWindowCard
                  required property var modelData
                  required property int index
                  readonly property bool selected: root.sourceWindowIndex === index
                  readonly property real descriptionNaturalHeight: windowDescription.implicitHeight
                  readonly property string applicationName: {
                    var rawName = String(modelData.appClass || "Application").replace(/[-_.]+/g, " ")
                    if (rawName.toLowerCase() === "chatgpt") return "ChatGPT"
                    var words = rawName.split(/\s+/)
                    for (var i = 0; i < words.length; i++) {
                      if (words[i].length > 0) {
                        words[i] = words[i].charAt(0).toUpperCase() + words[i].slice(1)
                      }
                    }
                    return words.join(" ")
                  }
                  onDescriptionNaturalHeightChanged: Qt.callLater(sourceWindowGrid.updateDescriptionHeight)

                  width: Math.floor((sourceWindowGrid.width - sourceWindowGrid.spacing) / 2)
                  implicitHeight: applicationNameLabel.implicitHeight
                    + windowPreviewImage.implicitHeight
                    + windowDescription.height + Style.space(30)
                  hasCursor: selected
                  current: selected
                  bordered: true
                  foreground: root.foreground
                  accent: root.urgent
                  borderSpec: selected ? Border.flat(root.urgent, 2)
                    : Border.controlSpec("normal", foreground, accent)

                  Text {
                    id: applicationNameLabel
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.topMargin: Style.space(7)
                    anchors.leftMargin: Style.space(10)
                    anchors.rightMargin: Style.space(10)
                    text: sourceWindowCard.applicationName
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    font.bold: true
                    elide: Text.ElideRight
                  }

                  WindowPreview {
                    id: windowPreviewImage
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: applicationNameLabel.bottom
                    anchors.topMargin: Style.space(7)
                    anchors.leftMargin: Border.left(sourceWindowCard.borderSpec)
                    anchors.rightMargin: Border.right(sourceWindowCard.borderSpec)
                    implicitHeight: Math.round(sourceWindowCard.width * 0.56)
                    windowAddress: String(sourceWindowCard.modelData.address || "")
                    live: root.opened && root.sourcePickerVisible
                      && root.sourcePickerPage === "windows"
                  }

                  Text {
                    id: windowDescription
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: windowPreviewImage.bottom
                    anchors.leftMargin: Style.space(10)
                    anchors.rightMargin: Style.space(10)
                    anchors.topMargin: Style.space(7)
                    height: sourceWindowGrid.uniformDescriptionHeight
                    text: String(sourceWindowCard.modelData.title || "Application window")
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    wrapMode: Text.WordWrap
                  }

                  HoverHandler {
                    onHoveredChanged: if (hovered) root.sourceWindowIndex = sourceWindowCard.index
                  }
                  TapHandler { onTapped: root.chooseSourceWindow(sourceWindowCard.index) }
                }
              }
            }

            CastSurface {
              visible: root.sourcePickerPage === "windows"
              width: parent.width
              implicitHeight: Style.space(48)
              bordered: true
              foreground: root.foreground

              Text {
                anchors.centerIn: parent
                text: "Back to display"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }
              TapHandler { onTapped: root.closeSourceView() }
            }

            Item {
              width: parent.width
              height: Style.space(4)
            }
          }

          Column {
            id: receiverContent
            visible: !root.sourcePickerVisible
            width: parent.width
            spacing: Style.space(20)

          PanelHero {
            width: parent.width
            title: "HovenCast v" + root.version
            meta: root.activeName !== ""
              ? root.activeName + " · " + Model.stateLabel(root.sessionState)
              : Model.stateLabel(root.sessionState)
            detail: root.sessionState === "streaming" ? Model.humanBitrate(root.bitrateBps) : ""
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconOpacity: root.sessionActive ? 1.0 : 0.65
            iconComponent: Component {
              HovenCastSymbol {
                iconColor: root.sessionState === "error" ? root.urgent : root.foreground
              }
            }
            trailingControl: Component {
              PanelActionButton {
                iconText: root.sessionActive ? "\uDB81\uDC17" : "\uDB80\uDF49"
                tooltipText: root.sessionActive ? "Stop sharing" : "Search again"
                foreground: root.foreground
                hoverColor: root.sessionActive ? root.urgent : root.foreground
                fontFamily: root.fontFamily
                bordered: true
                onClicked: root.sessionActive ? root.stopCasting() : root.refresh()
              }
            }
          }

          Text {
            visible: root.lastError !== "" || root.discoverError !== ""
            width: parent.width
            text: root.lastError !== "" ? root.lastError : root.discoverError
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          PanelSeparator {
            foreground: root.foreground
          }

          RowLayout {
            width: parent.width
            spacing: Style.space(8)

            CastSurface {
              Layout.fillWidth: true
              implicitHeight: Style.space(58)
              bordered: true
              foreground: root.foreground

              Column {
                anchors.left: parent.left
                anchors.right: audioSwitch.left
                anchors.leftMargin: Style.space(12)
                anchors.rightMargin: Style.space(10)
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(2)

                Text {
                  width: parent.width
                  text: "Keep audio here"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  font.bold: true
                  elide: Text.ElideRight
                }

                Text {
                  width: parent.width
                  text: root.keepLocalAudio ? "Computer + TV" : "Follows its window"
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }
              }

              ToggleSwitch {
                id: audioSwitch
                anchors.right: parent.right
                anchors.rightMargin: Style.space(10)
                anchors.verticalCenter: parent.verticalCenter
                checked: root.keepLocalAudio
                enabled: !root.sessionActive
                foreground: root.foreground
                onToggled: root.persistAudioPreference(!root.keepLocalAudio)
              }
            }

            CastSurface {
              id: compactPlacementButton
              visible: root.sourceMode === "workspace"
              Layout.fillWidth: true
              implicitHeight: Style.space(58)
              bordered: true
              foreground: root.foreground
              accent: root.urgent

              Column {
                anchors.left: parent.left
                anchors.right: placementCaret.left
                anchors.leftMargin: Style.space(12)
                anchors.rightMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(2)

                Text {
                  width: parent.width
                  text: "TV placement"
                  color: root.sessionActive ? root.dim : root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  font.bold: true
                  elide: Text.ElideRight
                }

                Text {
                  width: parent.width
                  text: root.displayPlacementLabel(root.displayPlacement)
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }
              }

              Text {
                id: placementCaret
                anchors.right: parent.right
                anchors.rightMargin: Style.space(12)
                anchors.verticalCenter: parent.verticalCenter
                text: "▾"
                color: root.sessionActive ? root.dim : root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }

              MouseArea {
                id: compactPlacementMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: root.sessionActive ? Qt.ArrowCursor : Qt.PointingHandCursor
                enabled: !root.sessionActive
                onClicked: root.displayPlacementMenuOpen = !root.displayPlacementMenuOpen
              }

              PanelToolTip {
                visible: compactPlacementMouse.containsMouse
                text: "Display Placement"
                fontFamily: root.fontFamily
              }
            }
          }

          PanelSeparator {
            foreground: root.foreground
          }

          CastSectionHeader {
            width: parent.width
            text: "CAST MODE"
            foreground: root.foreground
            fontFamily: root.fontFamily
          }

          RowLayout {
            width: parent.width
            spacing: Style.space(8)

            CastSurface {
              Layout.fillWidth: true
              implicitHeight: Style.space(48)
              current: root.sourceMode === "workspace"
              bordered: true
              foreground: root.foreground
              accent: root.urgent
              borderSpec: current ? Border.flat(root.urgent, 2)
                : Border.controlSpec("normal", foreground, accent)
              Text {
                anchors.centerIn: parent
                text: "Extend Display"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                font.bold: root.sourceMode === "workspace"
              }
              TapHandler {
                enabled: !root.sessionActive
                onTapped: root.sourceMode = "workspace"
              }
            }

            CastSurface {
              Layout.fillWidth: true
              implicitHeight: Style.space(48)
              current: root.sourceMode === "window"
              bordered: true
              foreground: root.foreground
              accent: root.urgent
              borderSpec: current ? Border.flat(root.urgent, 2)
                : Border.controlSpec("normal", foreground, accent)
              Text {
                anchors.centerIn: parent
                text: "Share Screen"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                font.bold: root.sourceMode === "window"
              }
              TapHandler {
                enabled: !root.sessionActive
                onTapped: {
                  root.sourceMode = "window"
                  root.displayPlacementMenuOpen = false
                }
              }
            }
          }

          Text {
            width: parent.width
            text: root.sourceMode === "workspace"
              ? (root.sessionState === "streaming"
                ? "Choose another workspace without reconnecting the TV."
                : "Put a workspace on a dedicated wireless display.")
              : "Share a workspace, application window, or selected area."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          RowLayout {
            visible: root.sourceMode === "workspace" && root.displayPlacementMenuOpen
            width: parent.width
            spacing: Style.space(8)

            Repeater {
              model: [
                { value: "left", label: "←  Left" },
                { value: "above", label: "↑  Above" },
                { value: "below", label: "↓  Below" },
                { value: "right", label: "Right  →" }
              ]

              CastSurface {
                required property var modelData
                Layout.fillWidth: true
                implicitHeight: Style.space(36)
                current: root.displayPlacement === String(modelData.value)
                bordered: true
                foreground: root.foreground
                accent: root.urgent
                borderSpec: current ? Border.flat(root.urgent, 2)
                  : Border.controlSpec("normal", foreground, accent)

                Text {
                  anchors.centerIn: parent
                  text: String(parent.modelData.label)
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  font.bold: parent.current
                }

                TapHandler {
                  onTapped: root.persistDisplayPlacement(String(parent.modelData.value))
                }
              }
            }
          }

          Flow {
            visible: root.sourceMode === "workspace"
            width: parent.width
            spacing: Style.space(10)

            Repeater {
              model: root.availableWorkspaces

              CastSurface {
                id: workspaceChoice
                required property var modelData
                required property int index
                width: Math.floor((receiverContent.width - Style.space(30)) / 4)
                implicitHeight: Style.space(76)
                current: root.selectedWorkspaceIndex === index
                bordered: true
                foreground: root.foreground
                accent: root.urgent
                borderSpec: current ? Border.flat(root.urgent, 2)
                  : Border.controlSpec("normal", foreground, accent)

                WorkspacePreview {
                  anchors.fill: parent
                  anchors.topMargin: Border.top(workspaceChoice.borderSpec)
                  anchors.rightMargin: Border.right(workspaceChoice.borderSpec)
                  anchors.bottomMargin: Border.bottom(workspaceChoice.borderSpec)
                  anchors.leftMargin: Border.left(workspaceChoice.borderSpec)
                  workspaceName: String(workspaceChoice.modelData.name)
                  live: root.opened && root.sourceMode === "workspace"
                }

                Rectangle {
                  anchors.left: parent.left
                  anchors.bottom: parent.bottom
                  anchors.leftMargin: Style.space(10)
                  anchors.bottomMargin: Style.space(10)
                  implicitWidth: workspaceLabel.implicitWidth + Style.space(12)
                  implicitHeight: workspaceLabel.implicitHeight + Style.space(6)
                  color: Color.popups.background
                  border.width: 1
                  border.color: workspaceChoice.current
                    ? root.urgent : Qt.alpha(root.foreground, 0.55)

                  Text {
                    id: workspaceLabel
                    anchors.centerIn: parent
                    text: String(workspaceChoice.modelData.name)
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: workspaceChoice.current
                  }
                }

                TapHandler {
                  enabled: !workspaceActionProc.running
                  onTapped: root.chooseWorkspace(workspaceChoice.index)
                }
              }
            }
          }

          Text {
            visible: root.sourceMode === "workspace" && root.availableWorkspaces.length === 0
            width: parent.width
            text: root.workspaceError !== "" ? root.workspaceError : "No normal Hyprland workspaces are available."
            color: root.workspaceError !== "" ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          RowLayout {
            visible: root.sourceMode === "workspace" && root.sessionState === "streaming"
            width: parent.width
            spacing: Style.space(8)

            CastSurface {
              Layout.fillWidth: true
              implicitHeight: Style.space(40)
              bordered: true
              foreground: root.foreground
              Text {
                anchors.centerIn: parent
                text: "Control TV"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }
              TapHandler { onTapped: root.focusTvWorkspace() }
            }

            CastSurface {
              Layout.fillWidth: true
              implicitHeight: Style.space(40)
              bordered: true
              foreground: root.foreground
              Text {
                anchors.centerIn: parent
                text: "Return to laptop"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }
              TapHandler { onTapped: root.focusLaptop() }
            }
          }

          Text {
            visible: root.sourceMode === "workspace" && root.sessionState === "streaming"
            width: parent.width
            text: "Keyboard: Ctrl+Alt+Tab moves to the TV; Ctrl+Alt+Shift+Tab returns."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          Text {
            visible: root.workspaceError !== "" && root.availableWorkspaces.length > 0
            width: parent.width
            text: root.workspaceError
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          PanelSeparator {
            foreground: root.foreground
          }

          CastSectionHeader {
            width: parent.width
            text: "AVAILABLE DISPLAYS"
            foreground: root.foreground
            fontFamily: root.fontFamily
          }

          Text {
            visible: root.receivers.length === 0
            width: parent.width
            text: discoverProc.running ? "Searching your local network…" : "No compatible displays were found."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            wrapMode: Text.WordWrap
          }

          Repeater {
            model: root.receivers

            CastSurface {
              id: receiverRow
              required property var modelData
              required property int index
              readonly property bool selected: root.selectedIndex === index
              readonly property bool detailsVisible: receiverHover.hovered

              width: content.width
              implicitHeight: receiverRowContent.implicitHeight + Style.space(28)
              hasCursor: root.cursorActive && root.selectedIndex === index
              current: selected || root.activeAddress === String(modelData.address || "")
              foreground: root.foreground

              ColumnLayout {
                id: receiverRowContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: Style.space(14)
                anchors.rightMargin: Style.space(14)
                spacing: Style.space(10)

                RowLayout {
                  Layout.fillWidth: true
                  spacing: Style.space(10)

                  Text {
                    textFormat: Text.PlainText
                    text: "\uDB80\uDF79"
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.icon
                    Layout.alignment: Qt.AlignVCenter
                  }

                  ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Style.space(4)

                    Text {
                      Layout.fillWidth: true
                      text: String(receiverRow.modelData.name || receiverRow.modelData.address)
                      color: root.foreground
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.body
                      elide: Text.ElideRight
                    }

                    Text {
                      Layout.fillWidth: true
                      text: Model.protocolName(receiverRow.modelData.protocol)
                        + (Model.resolutionLabel(receiverRow.modelData) !== ""
                          ? " · " + Model.resolutionLabel(receiverRow.modelData) : "")
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      font.bold: true
                      font.letterSpacing: 0.7
                      elide: Text.ElideRight
                    }
                  }

                  Text {
                    textFormat: Text.PlainText
                    text: root.activeAddress === String(receiverRow.modelData.address || "")
                      ? (root.sessionState === "streaming" ? "SHARING" : "BUSY")
                      : "CONNECT"
                    color: root.activeAddress === String(receiverRow.modelData.address || "")
                      ? root.foreground : root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: true
                    Layout.alignment: Qt.AlignVCenter
                  }
                }

                Rectangle {
                  visible: receiverRow.detailsVisible
                  Layout.fillWidth: true
                  implicitHeight: 1
                  color: Qt.alpha(root.foreground, 0.18)
                }

                GridLayout {
                  visible: receiverRow.detailsVisible
                  Layout.fillWidth: true
                  columns: 2
                  columnSpacing: Style.space(12)
                  rowSpacing: Style.space(3)

                  Text {
                    text: "NETWORK ADDRESS"
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: true
                    font.letterSpacing: 0.7
                  }

                  Text {
                    Layout.fillWidth: true
                    text: String(receiverRow.modelData.address || "")
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: true
                    horizontalAlignment: Text.AlignRight
                  }

                  Text {
                    text: "CONNECTION"
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: true
                    font.letterSpacing: 0.7
                  }

                  Text {
                    Layout.fillWidth: true
                    text: Model.connectionLabel(receiverRow.modelData.protocol)
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: true
                    horizontalAlignment: Text.AlignRight
                  }
                }
              }

              HoverHandler {
                id: receiverHover
                onHoveredChanged: if (hovered) root.selectReceiver(receiverRow.index)
              }

              TapHandler {
                onTapped: {
                  root.selectReceiver(receiverRow.index)
                  if (root.activeAddress === String(receiverRow.modelData.address || "") && root.sessionActive)
                    root.stopCasting()
                  else if (!root.sessionActive)
                    root.connectReceiver(receiverRow.modelData)
                }
              }
            }
          }

          Text {
            visible: root.sessionState === "streaming"
            width: parent.width
            text: root.videoFrames + " video frames · " + root.audioBuffers
              + " audio buffers · A/V drift " + Math.round(root.avDriftMs) + " ms"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          Text {
            width: parent.width
            text: root.sessionActive
              ? "Right-click the bar icon or press S here to stop sharing."
              : "Select a display, then choose what you want to share in HovenCast."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          Item {
            width: parent.width
            height: Style.space(4)
          }
          }
        }
      }
    }
  }
}
