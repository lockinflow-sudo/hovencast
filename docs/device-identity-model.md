# Incremental device identity model

Implemented in the experimental source checkout, October 6, 2026. Deployed to
this laptop's taskbar plugin as 0.3.0-beta.1 later that day; not merged to main.
Beta 0.3.0-beta.2 now wires conservative snapshot matching into discovery.

## Current boundary

`cast_devices.py` wraps each `Receiver` in one provisional `CastDevice` with one
`CastEndpoint`. It does not merge records, select an alternative endpoint, infer
capabilities, or persist anything. Locator-derived IDs are not hardware IDs.

`cast_identity.py` provides immutable, namespaced claims and service metadata
without importing the capture, desktop or network backends. `IdentityEvidence`
remains importable from `cast_devices` for compatibility with the first step.

Discovery now retains these optional internal observations:

| Observation | Namespace/provenance | Validation |
| --- | --- | --- |
| MICE TXT `container_id` | `mice-container-id` / `mice-mdns` | Canonical nonzero GUID, optional braces |
| Roku ECP `udn` | `roku-udn` / `roku-ecp` | Canonical nonzero GUID |
| Roku ECP `serial-number` | `roku-serial-number` / `roku-ecp` | Bounded ASCII identifier; case retained |
| Roku ECP `device-id` | `roku-device-id` / `roku-ecp` | Bounded ASCII identifier; case retained |
| LAN service name/type/domain/hostname | `DiscoveryService`, provider `mice-mdns` | Bounded nonempty text without control characters |

The MICE key is defined in [Microsoft's sink initialization specification](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-mice/f6b2c820-ff11-4593-8b87-614868401a60).
The Roku fields are shown in the official [ECP device-info example](https://developer.roku.com/dev/docs/external-control-api#querydevice-info-example).
TXT tokenization accounts for [Avahi's quoted-string escaping](https://github.com/avahi/avahi/blob/master/avahi-common/strlst.c).

Missing/malformed identity metadata does not remove otherwise usable receivers.
The existing ECP resolution filter, sorting, address/port deduplication, probe
requests/timeouts and connection behavior are unchanged. Identical claims are
deduplicated internally; conflicting valid values in one namespace remain
distinct. More than eight distinct values discard that namespace's claims
rather than truncating them into apparently trustworthy identity evidence.

ECP enrichment supplements mDNS evidence. It does not equate a Roku UDN with a
MICE container ID, or infer identity from a model, name, IP or MAC. The device
adapters preserve service details and evidence in both directions.

`Receiver.json()` deliberately retains the exact legacy field set. Internal
claims are omitted, including when present, so the UI payload and identifier
privacy remain unchanged. The existing `discover` CLI therefore does not expose
these observations yet: a future device-registry layer must consume the Python
Receiver records before legacy serialization. No new diagnostic export, logs,
profile file, or live P2P identity extraction was added in this step.

## Supplied vendor metadata and snapshot association

Paired hardware inspection found that distinct LAN Roku receivers reused a
container ID. Two observed Wi-Fi Direct peers instead advertised a Microsoft
vendor payload with an unqualified hostname and LAN IP agreeing with their LAN
service and ECP serial number. Another peer had no such payload. These findings
are observations of this setup, not a guarantee for every Roku or Fire TV.

`mice_vendor.py` parses **supplied** supplicant `VendorExtension` byte blocks:
Microsoft OUI `00:01:37`, then big-endian attribute ID, length and value. This
API does not accept the enclosing WSC IE. The conservative profile requires one
capabilities attribute (`0x2001`) and one ASCII DNS-label hostname (`0x2002`).
IP attributes (`0x2005`) are optional and may repeat; unsupported capabilities
remain inspectable but cannot match. Malformed framing, duplicate mandatory
attributes, multiple Microsoft blocks, unusable IP addresses and excessive
input fail closed. Limits are 16 blocks, 4096 bytes per block, 16384 total bytes,
64 attributes and eight IP attributes. Unknown vendor blocks and attributes are
ignored within those bounds.

The format comes from [Microsoft's MICE specification](https://winprotocoldoc.z19.web.core.windows.net/MS-MICE/%5BMS-MICE%5D-180912.pdf),
sections 2.2.8–2.2.8.5. The input shape corresponds to the read-only peer
`VendorExtension` property in the [supplicant D-Bus API](https://w1.fi/wpa_supplicant/devel/dbus.html#dbus_peer).

`device_reconciliation.py` offers a proposal, **not a merge**. Its current rule
only associates a Wi-Fi Direct Miracast peer with a Roku-enriched infrastructure
endpoint when all of these agree:

- Caller supplies a nonempty, identical network epoch and fresh observations
  using the same monotonic clock (30-second default maximum age).
- Supported MICE metadata supplies both hostname and IP matching one LAN
  `_display._tcp` service in `.local`.
- The ECP serial agrees with that hostname, and another valid ECP device ID or
  UDN exists. Conflicting claims or anchors reused by another fresh endpoint
  prevent association.
- There is exactly one candidate. Duplicate locator observations on different
  interfaces remain ambiguous; conflicting records with the same endpoint ID
  fail closed. At most 256 LAN observations are accepted.

Container-ID collisions are reported independently and never justify a match.
Names, models, similar MACs and IPs alone are not identity evidence. Missing
metadata, unsupported receivers, stale observations and different network
epochs remain separate. An association is local to this snapshot; DHCP or
network changes require new observations. Advertisements and ECP responses are
not cryptographically authenticated, so this is not an authorization mechanism.

Neither model module performs network requests, DNS resolution, logging, root access,
endpoint selection, UI changes, persistence or connection fallback. The caller
must supply genuinely fresh discovery/ECP observations and change the network
epoch when appropriate. The current libnm peer path does not expose the vendor
blocks. Accessing the supplicant property on this laptop requires privilege;
no system policy or installed helper permissions have been broadened.

## Explicit read-only live diagnostic

Run `python -B src/identity_diagnostic.py` from this checkout for an ordinary
scan and attempted unprivileged metadata read. The optional `--authorize-read`
flag explicitly invokes `pkexec` for the one-shot reader; it never silently
escalates, installs a policy, or grants future authorization. This developer
diagnostic is not called by the taskbar or connection path.

`scripts/read_p2p_metadata.py` is self-contained for isolated Python execution.
It accepts one validated P2P management interface and at most sixteen distinct
selected MAC addresses, maps that interface to its physical adapter, and pins
the current supplicant D-Bus owner. It resolves that adapter and reads its peer
list (at most 128 entries), rejecting paths outside the selected interface.
Only `DeviceAddress` is read for unselected peers; selected peers additionally
provide `VendorExtension`, with their address rechecked against a mid-read
change. There is no `GetAll`, arbitrary bus destination/path input, credential
query, pairing, configuration write or firewall operation.

The fixed `wpa_cli -i ADAPTER p2p_peer SELECTED_MAC` query supplies the peer's
age. Integer age is rounded conservatively; absent, conflicting, invalid or
old ages cannot become fresh observations. This is needed because repeated
live scans on this laptop retained old libnm LastSeen values despite current
supplicant peer observations. The query's output stays internal; only its
age is retained. The D-Bus read has a ten-second deadline, individual calls
have one-second timeouts, and each age query has a two-second timeout.

The parent checks a bounded response, refreshes LAN discovery/ECP data, and
discards proposed matches if the NetworkManager device/activation snapshot
changed during the diagnostic. Metadata and identifiers remain in memory.
Output contains friendly receiver names, decisions, counts and sanitized
failure stages, not serial numbers, MACs, IPs or raw vendor bytes.

This does **not** install a production privileged-reader interface. A future
automatic path needs a fixed root-owned entry point, active-local-session
authorization review and caller/lifetime validation before deployment. The
existing pairing-helper authorization must not be reused for arbitrary Python
or D-Bus access.

## Live diagnostic result — October 6, 2026

The authorized diagnostic read succeeded with the NetworkManager snapshot
unchanged. Four Wi-Fi Direct receiver records were selected; supplicant age
validated three as fresh. Three LAN receivers answered the existing ECP probe.
The 32-inch and 65-inch TCL Roku TVs each proposed association with their
correct same-named LAN receiver, despite the shared container-ID collision.
The 55-inch observation was stale and remained unmatched. The older receiver
named “Roku TV” lacked supported MICE metadata and remained unmatched.

No pairing, casting, device-list merge, permission-policy installation or
connection fallback was performed. This verifies the diagnostic matching path,
not production readiness for all receiver models.

## Verification and next gate

### Beta 0.3.0-beta.2 integration

The earlier diagnostic-only boundaries above describe the first implementation
gate. This beta now installs a **separate** fixed root-owned metadata reader and
active-local-session polkit action. It verifies authorizing UID, caller PID/birth
time and caller lifetime; it does not change the pairing helper's privileges.
`automatic_devices.py` checks root ownership/non-writable fixed files, invokes
only that installed entry point, validates its reply and uses fresh metadata
against the current LAN/ECP snapshot. NetworkManager state, activation UUID and
activation object path are checked before grouping. No persisted associations
or profile files are introduced.

Matched rows retain a LAN default and both exact legacy endpoint JSON records.
The widget exposes explicit transport buttons and identifies the active row
through either address. Plain row clicks continue to use LAN; there is no
silent fallback. Missing helper/denied read/stale metadata/network change keeps
legacy rows separate. Multiple P2P peers claiming one LAN target also remain
separate, instead of hiding an ambiguous endpoint.

The live discovery check returned four rows: the 32-inch and 65-inch TCL Roku
TVs each contained their correct LAN and Wi-Fi Direct endpoints; the 55-inch
TV was LAN-only in that scan; the unmatched older Roku retained its P2P row.
The selected Python suite passes 199 tests, plus JavaScript model tests for
endpoint choices, beta labels and grouped active-address handling. Panel status
now includes the actually loaded build title/version and grouping counts for
deployment verification. Root metadata rules were installed with admin approval;
subsequent live discovery completed without a repeated authentication prompt.

Earlier 0.3.0-beta.1 registry rescan did not establish which code the long-running
bar actually displayed. This deployment requires a full shell restart followed
by live panel-status verification, not merely checking manifest files.

Synthetic tests cover valid/missing/malformed/nil/oversized IDs, TXT framing and
escapes, conflicting and excessive claims, unchanged legacy JSON, evidence
preservation through discovery/enrichment/adapters, and no automatic merges.

Additional synthetic tests cover bounded vendor parsing, unsupported capability
versions, malformed/truncated attributes, duplicate claims, reused container
IDs with distinct ECP anchors, crossed host/IP claims, stale/network-mismatched
observations, ambiguous endpoints and collision rejection. Fixtures contain
synthetic identifiers and documentation-only IP addresses.

Tests also cover request validation before D-Bus access, property allowlisting,
peer-path/address validation, payload/response bounds, absence of automatic
escalation, age validation and suppression of matches after network changes.
The selected suite passes 187 Python tests and the JavaScript model tests.
The next gate is a production authorization design before any automatic
metadata read, device-list deduplication or connection fallback.
The taskbar, existing discovery JSON and casting path remain unchanged.
