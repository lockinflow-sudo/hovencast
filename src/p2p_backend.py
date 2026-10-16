"""Native P2P links via libnm; HovenCast owns WFD/RTSP and media."""
from __future__ import annotations
import ipaddress
import re
import time
import uuid
import pwd
import os
import shutil
import socket
from typing import Callable, Any
import gi
gi.require_version("NM", "1.0")
from gi.repository import Gio, GLib, GObject, NM
from omarchy_cast_protocol import Receiver
from p2p_privilege import P2PPrivilege

SOURCE_WFD_IES = bytes.fromhex("00000600901c4400c8")


def wait_for_clean_device(device: Any, *, previous: Any = None,
                          group_interface: str = "", timeout: float = 10,
                          cancelled: Callable[[], bool] = lambda: False) -> None:
    """Observe teardown; never disconnect an unknown or replacement session."""
    def clean() -> bool:
        active = device.get_active_connection()
        if (active is not None and active.get_state() != NM.ActiveConnectionState.DEACTIVATING
                and (previous is None or active.get_uuid() != previous.get_uuid())):
            raise RuntimeError("Wi-Fi Direct adapter is in use; stop its current session before reconnecting")
        group_exists = False
        if group_interface:
            try:
                socket.if_nametoindex(group_interface)
                group_exists = True
            except OSError:
                pass
        return (active is None and device.get_state() == NM.DeviceState.DISCONNECTED
                and not group_exists)

    try:
        pump_until(clean, timeout, cancelled)
    except TimeoutError as error:
        raise RuntimeError("Previous Wi-Fi Direct session has not finished disconnecting; try again after it clears") from error


def sink_device_info(data: bytes) -> tuple[bool, int]:
    """Require an available primary sink in bounded WFD subelements."""
    offset = 0
    while offset + 3 <= len(data):
        kind = data[offset]
        length = int.from_bytes(data[offset + 1:offset + 3], "big")
        offset += 3
        if offset + length > len(data):
            return False, 0
        if kind == 0 and length == 6:
            info = int.from_bytes(data[offset:offset + 2], "big")
            port = int.from_bytes(data[offset + 2:offset + 4], "big")
            return (info & 3) in {1, 3} and bool(info & 0x10) and port > 0, port
        offset += length
    return False, 0


def selected_scan_timeout(devices: list[Any], target: str) -> float:
    """Short scan for a recently observed available sink; pairing still verifies it."""
    now = time.clock_gettime(time.CLOCK_BOOTTIME)
    for device in devices:
        if device.get_state() != NM.DeviceState.DISCONNECTED:
            continue
        for peer in device.get_peers():
            if peer.get_hw_address().lower() != target:
                continue
            seen = peer.get_last_seen()
            ies = peer.get_wfd_ies()
            if (seen >= 0 and 0 <= now - seen <= 30
                    and sink_device_info(ies.get_data() if ies else b"")[0]):
                return 2.0
    return 6.0


def pump_until(predicate: Callable[[], bool], timeout: float,
               cancelled: Callable[[], bool] = lambda: False) -> None:
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not predicate():
        if cancelled():
            raise InterruptedError("Wi-Fi Direct connection cancelled")
        while context.pending():
            context.iteration(False)
        if time.monotonic() >= deadline:
            raise TimeoutError("Wi-Fi Direct operation timed out")
        time.sleep(0.02)


def async_call(start: Callable[..., Any], finish: Callable[..., Any],
               *arguments: Any, timeout: float = 10,
               cancelled: Callable[[], bool] = lambda: False) -> Any:
    results: list[Any] = []
    errors: list[Exception] = []
    cancellable = Gio.Cancellable()
    def completed(_source: Any, result: Any, _data: Any = None) -> None:
        try:
            results.append(finish(result))
        except Exception as error:
            errors.append(error)
    try:
        start(*arguments, cancellable, completed, None)
        pump_until(lambda: bool(results or errors), timeout, cancelled)
    except BaseException:
        cancellable.cancel()
        raise
    if errors:
        raise errors[0]
    return results[0]


class LinkStateMonitor:
    """Keep transient NM failures even when FAILED immediately becomes idle."""
    def __init__(self, device: Any, progress: Callable[..., None]) -> None:
        self.device = device
        self.progress = progress
        self.last_state = None
        self.failure_reason = None

    def changed(self, _device: Any, new: int, _old: int, reason: int) -> None:
        self.record(NM.DeviceState(new), NM.DeviceStateReason(reason))

    def record(self, state: Any, reason: Any) -> None:
        if state == NM.DeviceState.FAILED and reason != NM.DeviceStateReason.NONE:
            self.failure_reason = reason
        current = (state, reason)
        if current != self.last_state:
            self.last_state = current
            self.progress(state=state.value_nick, reason=reason.value_nick)

    def observe(self) -> None:
        self.record(self.device.get_state(), self.device.get_state_reason())

    def require_active(self, active: Any) -> None:
        self.observe()
        current = self.device.get_active_connection()
        if (self.failure_reason is not None
                or self.device.get_state() != NM.DeviceState.ACTIVATED
                or active.get_state() != NM.ActiveConnectionState.ACTIVATED
                or current is None or current.get_uuid() != active.get_uuid()):
            reason = self.failure_reason or self.device.get_state_reason()
            label = reason.value_nick if reason != NM.DeviceStateReason.NONE else "link-disconnected"
            raise RuntimeError("Wi-Fi Direct link failed while waiting for the receiver address ("
                               + label + "); reopen the receiver's mirroring screen and retry")


class P2PLink:
    def __init__(self) -> None:
        self.client = NM.Client.new(None)
        self.active = None
        self.device = None
        self.address = ""
        self.local_address = ""
        self.network = ""
        self.interface = ""
        self.privilege = None

    def devices(self) -> list[Any]:
        return [device for device in self.client.get_devices()
                if isinstance(device, NM.DeviceWifiP2P)]

    def discover(self, timeout: float = 5,
                 cancelled: Callable[[], bool] = lambda: False,
                 target: str = "") -> list[Receiver]:
        devices = [device for device in self.devices()
                   if device.get_state() == NM.DeviceState.DISCONNECTED]
        # LastSeen uses CLOCK_BOOTTIME, not wall clock or CLOCK_MONOTONIC.
        # Require a later whole second so cached/same-second observations
        # cannot short-circuit the selected-device scan.
        scan_started = int(time.clock_gettime(time.CLOCK_BOOTTIME))
        refreshed = set()
        started = []
        try:
            for device in devices:
                async_call(device.start_find, device.start_find_finish,
                           GLib.Variant("a{sv}", {"timeout": GLib.Variant("i", 10)}),
                           cancelled=cancelled)
                started.append(device)
            deadline = time.monotonic() + timeout
            def done() -> bool:
                if cancelled():
                    raise InterruptedError("Wi-Fi Direct connection cancelled")
                if target:
                    for device in devices:
                        for peer in device.get_peers():
                            if (peer.get_hw_address().lower() == target
                                    and peer.get_last_seen() > scan_started):
                                ies = peer.get_wfd_ies()
                                if sink_device_info(ies.get_data() if ies else b"")[0]:
                                    refreshed.add((device.get_iface(), target))
                                    return True
                return time.monotonic() >= deadline
            pump_until(done, timeout + 1, cancelled)
            receivers = []
            for device in devices:
                for peer in device.get_peers():
                    ies = peer.get_wfd_ies()
                    supported, port = sink_device_info(ies.get_data() if ies else b"")
                    if supported:
                        mac = peer.get_hw_address().lower()
                        if target and (mac != target or (device.get_iface(), mac) not in refreshed):
                            continue
                        receivers.append(Receiver(
                            peer.get_name() or mac, "p2p:" + mac, port,
                            device.get_iface(), protocol="miracast-p2p"))
            return sorted(receivers, key=lambda item: item.name.casefold())
        finally:
            for device in started:
                async_call(device.stop_find, device.stop_find_finish)

    def discover_selected(self, target: str, cancelled: Callable[[], bool],
                          progress: Callable[..., None]) -> list[Receiver]:
        timeout = selected_scan_timeout(self.devices(), target)
        progress(state="discovering-selected",
                 reason="recent-peer-2s-scan" if timeout == 2 else "full-6s-scan")
        receivers = self.discover(timeout=timeout, cancelled=cancelled, target=target)
        if timeout == 2 and not any(r.address == "p2p:" + target for r in receivers):
            if cancelled():
                raise InterruptedError("Wi-Fi Direct connection cancelled")
            progress(state="refreshing-selected", reason="short-scan-missed-full-6s-fallback")
            receivers = self.discover(timeout=6, cancelled=cancelled, target=target)
        if not receivers:
            if cancelled():
                raise InterruptedError("Wi-Fi Direct connection cancelled")
            # Some supplicant/driver combinations do not refresh LastSeen for
            # an unchanged known peer. A completed full scan is still required,
            # but absence of a timestamp update cannot prohibit pairing. Use
            # only the selected, available sink on a disconnected adapter;
            # NetworkManager activation remains the actual reachability check.
            receivers = []
            for device in self.devices():
                if device.get_state() != NM.DeviceState.DISCONNECTED:
                    continue
                for peer in device.get_peers():
                    if peer.get_hw_address().lower() != target:
                        continue
                    ies = peer.get_wfd_ies()
                    available, port = sink_device_info(ies.get_data() if ies else b"")
                    if available:
                        receivers.append(Receiver(peer.get_name() or target,
                            "p2p:" + target, port, device.get_iface(), protocol="miracast-p2p"))
            if receivers:
                progress(state="selected-peer-unconfirmed",
                         reason="full-scan-no-timestamp-update-bounded-pairing")
        return receivers

    def connect(self, address: str, timeout: float = 60,
                cancelled: Callable[[], bool] = lambda: False,
                progress: Callable[..., None] = lambda **fields: None) -> "P2PLink":
        if not re.fullmatch(r"p2p:(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", address):
            raise ValueError("Invalid Wi-Fi Direct receiver address")
        if not shutil.which("dnsmasq"):
            raise RuntimeError("Wi-Fi Direct requires dnsmasq for receiver IP addressing; run hovencast-setup")
        if not self.devices():
            raise RuntimeError("NetworkManager exposes no Wi-Fi Direct adapter; check Wi-Fi is enabled and the adapter/driver supports P2P")
        target = address[4:].lower()
        # Discovery skips busy devices. Wait for an already-known selected
        # adapter's teardown first, but never sweep other applications' links.
        for device in self.devices():
            if any(peer.get_hw_address().lower() == target for peer in device.get_peers()):
                progress(state="checking-reconnect", reason="waiting-for-clean-device")
                wait_for_clean_device(device, cancelled=cancelled)
        discovery_started = time.monotonic()
        discovered = self.discover_selected(target, cancelled, progress)
        progress(state="selected-discovery-complete",
                 reason=f"elapsed-{time.monotonic() - discovery_started:.3f}s")
        match = next(((device, peer) for device in self.devices()
                      for peer in device.get_peers()
                      if peer.get_hw_address().lower() == target
                      and any(r.address == address.lower() and r.interface == device.get_iface()
                              for r in discovered)), None)
        if not match:
            raise RuntimeError("Receiver is no longer visible; enable its mirroring screen")
        self.device, peer = match
        wait_for_clean_device(self.device, cancelled=cancelled)
        ies = peer.get_wfd_ies()
        supported, _port = sink_device_info(ies.get_data() if ies else b"")
        if not supported:
            raise RuntimeError("The receiver is not available for a Miracast session")
        connection = NM.SimpleConnection.new()
        general = NM.SettingConnection.new()
        general.props.id = "HovenCast Wi-Fi Direct"
        general.props.uuid = str(uuid.uuid4())
        general.props.type = "wifi-p2p"
        general.props.autoconnect = False
        general.add_permission("user", pwd.getpwuid(os.getuid()).pw_name, None)
        connection.add_setting(general)
        p2p = NM.SettingWifiP2P.new()
        p2p.props.peer = peer.get_hw_address()
        p2p.props.wps_method = NM.SettingWirelessSecurityWpsMethod.PBC
        p2p.props.wfd_ies = GLib.Bytes.new(SOURCE_WFD_IES)
        connection.add_setting(p2p)
        for cls in (NM.SettingIP4Config, NM.SettingIP6Config):
            setting = cls.new()
            setting.props.method = "auto"
            setting.props.never_default = True
            setting.props.may_fail = cls is NM.SettingIP6Config
            connection.add_setting(setting)
        options = GLib.Variant("a{sv}", {
            "persist": GLib.Variant("s", "volatile"),
            "bind-activation": GLib.Variant("s", "dbus-client"),
        })
        monitor = LinkStateMonitor(self.device, progress)
        state_handler = self.device.connect("state-changed", monitor.changed)
        try:
            self.privilege = P2PPrivilege(general.props.uuid, self.device.get_iface(), target)
            progress(state="awaiting-authorization", reason="p2p-helper")
            self.privilege.wait("helper-authorized", cancelled)
            # Authorization can take time; recheck before replacing any link.
            wait_for_clean_device(self.device, cancelled=cancelled)
            self.active, _result = async_call(
                self.client.add_and_activate_connection2,
                self.client.add_and_activate_connection2_finish,
                connection, self.device, peer.get_path(), options, timeout=timeout,
                cancelled=cancelled)
            def finished() -> bool:
                monitor.observe()
                self.privilege.check()
                return self.active.get_state() in {
                    NM.ActiveConnectionState.ACTIVATED, NM.ActiveConnectionState.DEACTIVATED}

            pump_until(finished, timeout, cancelled)
            if self.active.get_state() != NM.ActiveConnectionState.ACTIVATED:
                reason = monitor.failure_reason or self.device.get_state_reason()
                raise RuntimeError(
                    "Wi-Fi Direct pairing failed (" + reason.value_nick + "); "
                    "keep the receiver's mirroring screen open and approve any connection prompt")
            config = self.active.get_ip4_config()
            addresses = config.get_addresses() if config else []
            if not addresses:
                raise RuntimeError("The Wi-Fi Direct link has no IPv4 address")
            self.local_address = addresses[0].get_address()
            self.network = str(ipaddress.ip_network(
                f"{self.local_address}/{addresses[0].get_prefix()}", strict=False))
            self.address = config.get_gateway() or ""
            self.interface = self.device.get_ip_iface()
            # Group owners reach ACTIVATED before their selected sink finishes
            # association and DHCP. Give the helper time to identify that peer.
            def check_link() -> None:
                context = GLib.MainContext.default()
                while context.pending():
                    context.iteration(False)
                monitor.require_active(self.active)
            ready = self.privilege.wait("p2p-firewall-ready", cancelled, timeout=45,
                                        tick=check_link)
            check_link()  # Never accept a queued helper event for a lost link.
            self.address = ready["receiver"]
            return self
        except BaseException:
            try:
                self.close()
            except Exception as cleanup_error:
                progress(state="cleanup-error", reason=str(cleanup_error))
            raise
        finally:
            # NM.Device.disconnect is a network operation, not GObject's
            # signal disconnect. Use the explicit API to avoid that collision.
            detach_state_monitor(self.device, state_handler)

    def close(self) -> None:
        # Release the rule before removing the interface. Even if either step
        # fails, still attempt the other cleanup.
        try:
            if self.privilege is not None:
                privilege, self.privilege = self.privilege, None
                privilege.close()
        finally:
            self._close_link()

    def _close_link(self) -> None:
        if self.active is not None:
            active = self.active
            group = self.interface or (self.device.get_ip_iface() if self.device else "") or ""
            if not isinstance(group, str) or not group.startswith("p2p-") or group.startswith("p2p-dev-"):
                group = ""
            if active.get_state() != NM.ActiveConnectionState.DEACTIVATED:
                try:
                    async_call(self.client.deactivate_connection_async,
                               self.client.deactivate_connection_finish, active)
                except Exception:
                    # NM can remove a failed connection before updating our
                    # cached ActiveConnection state. Only tolerate that race
                    # when read-only state confirms it is already gone.
                    if not (active.get_state() == NM.ActiveConnectionState.DEACTIVATED
                            or (self.device is not None
                                and self.device.get_active_connection() is None
                                and self.device.get_state() == NM.DeviceState.DISCONNECTED)):
                        raise
            if self.device is not None:
                # DeactivateConnection finishing is not proof that the group
                # interface and device have finished asynchronous teardown.
                wait_for_clean_device(self.device, previous=active, group_interface=group)
            self.active = None


def discover_receivers(timeout: float = 5) -> list[Receiver]:
    return P2PLink().discover(timeout)


def detach_state_monitor(device: Any, handler: int) -> None:
    GObject.signal_handler_disconnect(device, handler)
