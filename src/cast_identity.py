"""Bounded discovery claims; these are not authenticated receiver identities.

No matching or persistence occurs here. Conflicting values remain distinct so
a future reconciler can reject ambiguous claims instead of taking the first.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Callable, Iterable


@dataclass(frozen=True)
class IdentityEvidence:
    namespace: str
    value: str
    provider: str


@dataclass(frozen=True)
class DiscoveryService:
    """Service locator/provenance, not a cross-transport identity proof."""

    name: str
    service_type: str
    domain: str
    hostname: str
    provider: str = "mice-mdns"


def normalize_guid(value: str) -> str | None:
    value = value.strip()
    if value.startswith("{") and value.endswith("}"):
        value = value[1:-1]
    if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value):
        return None
    guid = uuid.UUID(value)
    return str(guid) if guid.int else None


def normalize_roku_id(value: str) -> str | None:
    # Preserve case; serial/device-ID namespaces are not assumed equivalent.
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value):
        return None
    if value.casefold() in {"unknown", "none", "null", "n/a"} or not value.strip("0"):
        return None
    return value


def identity_claims(
    namespace: str, values: Iterable[str], provider: str,
    normalize: Callable[[str], str | None],
) -> tuple[IdentityEvidence, ...]:
    unique: dict[str, None] = {}
    for raw in values:
        value = normalize(raw)
        if value is not None:
            unique[value] = None
            if len(unique) > 8:
                # Do not truncate into an apparently unambiguous identity.
                return ()
    return tuple(IdentityEvidence(namespace, value, provider) for value in unique)
