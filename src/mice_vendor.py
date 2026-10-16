"""Parse supplied supplicant VendorExtension payloads; no network/root access.

Input is OUI + big-endian attribute/length/value, not a full WSC IE. The
conservative supported profile requires one capabilities and hostname attribute.
Malformed or excessive MICE input is rejected, never partially trusted.
"""
from dataclasses import dataclass
import ipaddress
import re
from typing import Iterable


@dataclass(frozen=True)
class MiceAdvertisement:
    hostname: str
    addresses: tuple[str, ...]
    capabilities: int

    @property
    def supported(self) -> bool:
        return bool(self.capabilities & 1) and (self.capabilities >> 2) & 7 == 1


def usable_address(value: str) -> str | None:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if "%" in value or any((address.is_unspecified, address.is_loopback,
                            address.is_multicast, address.is_link_local,
                            address.is_reserved)):
        return None
    return str(address)


def parse_mice_vendor_extensions(payloads: Iterable[bytes]) -> MiceAdvertisement | None:
    """Bound input to 16 blocks, 16 KiB total, 64 attributes, eight IPs.

    Multiple Microsoft blocks are rejected rather than resolving competing
    advertisements. Other vendors' bounded payloads are ignored.
    """
    total = count = attributes = 0
    microsoft = None
    for block in payloads:
        count += 1
        if not isinstance(block, bytes) or len(block) > 4096:
            return None
        total += len(block)
        if count > 16 or total > 16384:
            return None
        if not block.startswith(b"\x00\x01\x37"):
            continue
        if microsoft is not None:
            return None
        microsoft = block[3:]
    if microsoft is None:
        return None
    hostname = capabilities = None
    addresses = []
    offset = 0
    while offset < len(microsoft):
        attributes += 1
        if attributes > 64 or len(microsoft) - offset < 4:
            return None
        kind = int.from_bytes(microsoft[offset:offset + 2], "big")
        size = int.from_bytes(microsoft[offset + 2:offset + 4], "big")
        offset += 4
        if size > len(microsoft) - offset:
            return None
        value = microsoft[offset:offset + size]
        offset += size
        if kind == 0x2001:
            if capabilities is not None or size != 1:
                return None
            capabilities = value[0]
        elif kind == 0x2002:
            if hostname is not None or not 1 <= size <= 63:
                return None
            try:
                hostname = value.decode("ascii").lower()
            except UnicodeDecodeError:
                return None
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", hostname):
                return None
        elif kind == 0x2005:
            if len(addresses) >= 8 or not 1 <= size <= 45:
                return None
            try:
                address = usable_address(value.decode("ascii"))
            except UnicodeDecodeError:
                return None
            if address is None:
                return None
            addresses.append(address)
    if hostname is None or capabilities is None:
        return None
    return MiceAdvertisement(hostname, tuple(dict.fromkeys(addresses)), capabilities)
