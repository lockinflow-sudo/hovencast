"""Additive device/endpoint model; not yet used by discovery or casting.

Legacy Receiver records are connection observations, not physical identities.
Conversion deliberately produces one provisional device per record. IDs are
deterministic locator keys for this snapshot, NOT stable hardware identifiers:
DHCP, adapter or transport changes can change them. Do not persist associations
or merge physical receivers using these IDs.

This module does no discovery, capability inference, reconciliation or fallback.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from omarchy_cast_protocol import Receiver
from cast_identity import DiscoveryService, IdentityEvidence


class CastTransport(str, Enum):
    INFRASTRUCTURE = "infrastructure"
    WIFI_DIRECT = "wifi-direct"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CastEndpoint:
    id: str
    protocol: str
    transport: CastTransport
    address: str
    port: int
    interface: str
    legacy_protocol: str
    receiver_name: str
    native_width: int | None = None
    native_height: int | None = None
    model: str = ""
    vendor: str = ""
    identity_evidence: tuple[IdentityEvidence, ...] = ()
    discovery_service: DiscoveryService | None = None


@dataclass(frozen=True)
class CastDevice:
    id: str
    name: str
    manufacturer: str
    model: str
    endpoints: tuple[CastEndpoint, ...]
    provisional: bool = True


def device_from_receiver(receiver: Receiver) -> CastDevice:
    """Wrap a legacy observation without changing or identifying its receiver.

    Recognized legacy labels separate the Miracast protocol family from its
    transport. Keep the original label for lossless legacy dispatch. Unknown
    labels remain unknown rather than being guessed from an address prefix.
    """
    transport = {
        "miracast-mice": CastTransport.INFRASTRUCTURE,
        "miracast-p2p": CastTransport.WIFI_DIRECT,
    }.get(receiver.protocol, CastTransport.UNKNOWN)
    protocol = (
        "miracast" if transport != CastTransport.UNKNOWN else receiver.protocol
    )
    # Include protocol, port and interface: an address alone is not an endpoint.
    # JSON framing avoids separator collisions in externally supplied fields.
    locator = json.dumps(
        [receiver.protocol, receiver.address, receiver.port, receiver.interface],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    key = hashlib.sha256(locator.encode("utf-8")).hexdigest()
    endpoint = CastEndpoint(
        id="endpoint:" + key,
        protocol=protocol,
        transport=transport,
        address=receiver.address,
        port=receiver.port,
        interface=receiver.interface,
        legacy_protocol=receiver.protocol,
        receiver_name=receiver.name,
        native_width=receiver.native_width,
        native_height=receiver.native_height,
        model=receiver.model,
        vendor=receiver.vendor,
        identity_evidence=receiver.identity_evidence,
        discovery_service=receiver.discovery_service,
    )
    return CastDevice(
        id="provisional:" + key,
        name=receiver.name,
        manufacturer=receiver.vendor,
        model=receiver.model,
        endpoints=(endpoint,),
    )


def devices_from_receivers(receivers: Iterable[Receiver]) -> list[CastDevice]:
    """Preserve input order and cardinality; no deduplication or merging."""
    return [device_from_receiver(receiver) for receiver in receivers]


def receiver_from_endpoint(endpoint: CastEndpoint) -> Receiver:
    """Restore all legacy observation fields, including its protocol label."""
    return Receiver(
        name=endpoint.receiver_name,
        address=endpoint.address,
        port=endpoint.port,
        interface=endpoint.interface,
        protocol=endpoint.legacy_protocol,
        native_width=endpoint.native_width,
        native_height=endpoint.native_height,
        model=endpoint.model,
        vendor=endpoint.vendor,
        identity_evidence=endpoint.identity_evidence,
        discovery_service=endpoint.discovery_service,
    )


def receiver_from_device(device: CastDevice) -> Receiver:
    """Legacy adapter for exactly one endpoint; never silently choose one.

    Selection among multiple endpoints belongs to a future explicit strategy.
    Device presentation edits do not rewrite the original endpoint observation.
    """
    if len(device.endpoints) != 1:
        raise ValueError("Legacy conversion requires exactly one endpoint")
    return receiver_from_endpoint(device.endpoints[0])
