# Marketplace review follow-up

This follow-up addresses the functional changes requested in marketplace
[issue #13259](https://github.com/omacom/omarchy-plugin-marketplace/issues/13259)
and upstream [icon issue #2](https://github.com/lockinflow-sudo/hovencast/issues/2).
It does not replace marketplace capability review or change the manual-install
override. The published v2.5.0 tag and bundled native binaries are unchanged.

## Bar icon

The custom icon now has explicitly sized item bounds cropped to the symbol's
painted art. Its size no longer depends on filling the Loader, and blank canvas
padding no longer makes it smaller than neighboring icons. Existing state
colors and color transitions are preserved.

An offscreen Qt/Quickshell harness loaded the candidate's symbol and icon
component with unmodified copies of the installed Omarchy `BarIconContent` and
`IconContentBounds` components. All 15 size/state combinations passed: 14,
17.5 (1.25×), and 28 (2×) logical pixels, each in idle, active, pending dim,
pending bright, and error states. Bounds were nonzero, width matched the target,
centering errors were zero, and state colors matched after animation settled.
This verifies component geometry and color behavior, not a live-shell restart,
physical pixel measurements, or a receiver hardware session.

## Setup readiness and repair

Readiness now compares all eight installed helper, wrapper, policy and rule
files with the candidate's source, required root ownership, and file modes.
Missing, stale, unsafe, denied, malformed or timed-out checks keep setup
incomplete. Confirmed setup reinstalls the components before the final readiness
check; declining repair preserves the existing configuration. A complete
installation still returns without running installers.

The existing metadata helper has a read-only installation-check mode for these
fixed paths, including protected polkit rules. It accepts no file-path arguments
and does not modify files or services. Setup verifies the installed reader,
wrapper and deny-by-default policy before invoking it. The existing active-local
polkit rule authorizes the check; administrative repair still uses normal setup
confirmation. See [SECURITY.md](../SECURITY.md) for the authorization boundary.

Regression coverage exercises every missing/stale file, ownership and mode
errors, helper refusal/timeouts/malformed replies, confirmed repair, cancellation,
and the complete-install no-op. Reader tests cover bounded regular-file reads,
symlink/FIFO/directory rejection, fixed paths, and caller identity continuity.
Privileged operations remain simulated in the isolated first-run harness and
setup tests; no real helper installation or desktop configuration was changed
for this follow-up.

## Validation and remaining review

The JavaScript model suite, QML parsing, QML lint (with framework warnings
allowed), and whitespace checks passed. The Python suite and its exact run/skip
counts are reported in the marketplace handoff for the published commit.

Fresh official compatibility and baseline checks must bind the new full commit
SHA. The marketplace maintainer's review of bundled binaries, privilege,
services, installation and package management is still required. These code
changes are not a security audit, binary/source equivalence proof, or an
approval to remove the curated manual-install override. Earlier receiver and
clean-machine validation limitations remain in [first-run-validation.md](first-run-validation.md).
