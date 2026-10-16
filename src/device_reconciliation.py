"""Snapshot-only association proposals, never automatic merging or dispatch.

This first rule supports Roku ECP-validated LAN observations bridged by MICE
vendor metadata. It deliberately leaves other receivers separate. Advertised
metadata is not authentication; this module does no probing or DNS resolution.
"""
from dataclasses import dataclass
import math
from typing import Iterable

from cast_devices import CastEndpoint, CastTransport
from cast_identity import normalize_guid, normalize_roku_id
from mice_vendor import MiceAdvertisement, usable_address


@dataclass(frozen=True)
class EndpointObservation:
    endpoint: CastEndpoint
    network_scope: str
    observed_at: float  # Same monotonic clock as the caller's `now`.


@dataclass(frozen=True)
class AssociationProposal:
    lan_endpoint_id: str | None
    reason: str
    colliding_container_ids: tuple[str, ...] = ()


def _claims(endpoint: CastEndpoint, namespace: str, provider: str) -> set[str]:
    return {e.value for e in endpoint.identity_evidence
            if e.namespace == namespace and e.provider == provider}


def _anchors(endpoint: CastEndpoint) -> dict[str, str] | None:
    result = {}
    for namespace in ("roku-serial-number", "roku-device-id", "roku-udn"):
        values = _claims(endpoint, namespace, "roku-ecp")
        normalize = normalize_guid if namespace == "roku-udn" else normalize_roku_id
        if len(values) > 1 or any(normalize(v) != v for v in values):
            return None
        if values:
            result[namespace] = next(iter(values))
    # Serial ties the observed hostname to ECP; another anchor detects reuse.
    return result if "roku-serial-number" in result and len(result) >= 2 else None


def propose_mice_association(
    peer: EndpointObservation,
    advertisement: MiceAdvertisement | None,
    lan: Iterable[EndpointObservation],
    *, now: float, max_age: float = 30.0,
) -> AssociationProposal:
    """Require fresh same-scope host + IP + unique validated Roku anchors.

    Scope is an explicit caller-owned network epoch, not an interface name or
    an IP prefix. Reused container IDs are reported but never used as anchors.
    More than 256 observations fails closed. No persistent identity is created.
    """
    def fresh(observation):
        return (observation.network_scope == peer.network_scope
                and math.isfinite(observation.observed_at)
                and 0 <= now - observation.observed_at <= max_age)

    if not math.isfinite(now) or not math.isfinite(max_age) or max_age <= 0:
        return AssociationProposal(None, "invalid-clock")
    if (not peer.network_scope or not fresh(peer)
            or peer.endpoint.protocol != "miracast"
            or peer.endpoint.transport != CastTransport.WIFI_DIRECT):
        return AssociationProposal(None, "ineligible-peer")
    if advertisement is None or not advertisement.supported:
        return AssociationProposal(None, "missing-or-unsupported-mice")
    observations = {}
    for index, observation in enumerate(lan):
        if index >= 256:
            return AssociationProposal(None, "excessive-observations")
        endpoint = observation.endpoint
        if (not fresh(observation) or endpoint.protocol != "miracast"
                or endpoint.transport != CastTransport.INFRASTRUCTURE):
            continue
        previous = observations.get(endpoint.id)
        if previous is not None and previous != observation:
            return AssociationProposal(None, "conflicting-endpoint-observations")
        observations[endpoint.id] = observation

    containers = {}
    anchor_owners = {}
    anchors_by_id = {}
    for observation in observations.values():
        endpoint = observation.endpoint
        for value in _claims(endpoint, "mice-container-id", "mice-mdns"):
            containers.setdefault(value, set()).add(endpoint.id)
        anchors = _anchors(endpoint)
        anchors_by_id[endpoint.id] = anchors
        # Include conflicting claims in reuse detection, not just eligible rows.
        for namespace in ("roku-serial-number", "roku-device-id", "roku-udn"):
            for value in _claims(endpoint, namespace, "roku-ecp"):
                anchor_owners.setdefault((namespace, value), set()).add(endpoint.id)
    collisions = tuple(sorted(v for v, owners in containers.items() if len(owners) > 1))
    candidates = []
    for observation in observations.values():
        endpoint = observation.endpoint
        service = endpoint.discovery_service
        if (service is not None and service.provider == "mice-mdns"
                and service.service_type == "_display._tcp"
                and service.domain.lower().rstrip(".") == "local"
                and service.hostname.lower().rstrip(".") == advertisement.hostname + ".local"
                and usable_address(endpoint.address) in advertisement.addresses):
            candidates.append(endpoint)
    if len(candidates) != 1:
        return AssociationProposal(None, "ambiguous-or-missing-locator", collisions)
    candidate = candidates[0]
    anchors = anchors_by_id[candidate.id]
    if anchors is None or anchors["roku-serial-number"].lower() != advertisement.hostname:
        return AssociationProposal(None, "missing-or-conflicting-ecp-anchors", collisions)
    if any(len(anchor_owners[(namespace, value)]) > 1 for namespace, value in anchors.items()):
        return AssociationProposal(None, "reused-ecp-anchor", collisions)
    return AssociationProposal(candidate.id, "fresh-mice-host-ip-ecp", collisions)
