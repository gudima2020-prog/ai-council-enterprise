# P1-019.9 — Retention, Legal Hold and Evidence Archives

This stage adds controlled data retention, legal holds, immutable evidence archives and export packages for an external auditor.

## Safety model

The default retention policy is disabled and runs in `observe` mode. A destructive run requires all of the following:

1. the policy is enabled;
2. `enforcement_mode` is `purge`;
3. human approval is not required, or the audited `force` path is used after approval;
4. the record is not covered by an active legal hold;
5. the record belongs to a supported purge class.

Operator audit events and immutable compliance reports are never physically deleted. They remain visible in retention previews and can be copied into a sealed archive.

## Legal hold

A hold can target:

- source classes such as `security_event` or `notification`;
- exact source IDs;
- event patterns;
- actors and custodians;
- a time interval;
- an optional expiration date.

An active hold overrides retention deletion. Releasing a hold requires an identified operator and a reason.

## Evidence archives

An archive contains immutable items. Each item receives a SHA-256 content hash. The archive stores:

- an ordered manifest;
- all item hashes;
- a manifest hash;
- the previous sealed archive hash;
- the final archive hash.

Sealed archives cannot be edited or deleted. They can only be marked `revoked`, preserving the original evidence and the reason for revocation.

## External auditor package

A package references sealed evidence archives and immutable compliance reports. It contains a portable manifest and a package hash. Export can include archive items and reports.

The package is a hash-sealed technical evidence bundle. It is not a qualified electronic signature, notarisation or legal opinion.

## Retention classes

Physical purge is limited to mutable operational records:

- `security_event`;
- terminal `notification` records.

The retention engine can archive these records before deletion. Legal holds are evaluated before the purge set is formed.

## Permissions

- `human_control.retention.view`
- `human_control.retention.manage`
- `human_control.legal_hold.manage`
- `human_control.evidence.export`
