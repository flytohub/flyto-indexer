# Task amendment ledger and intent parity

Owner: Codex
Branch: main
Date: 2026-08-22

## Failure

An audited coding job reached rework, but its Indexer successor could not pass
the consumer's parent proof. Root intent ledgers were labelled
`task-context.v1`, and a mixed-intent successor could omit the root-level
`intent` even though both values are identity-bearing contract fields.

## Resolution

- New intent ledgers emit `intent-ledger.v1`; instruction context remains
  `task-context.v1`.
- Amendment reads accept the historical shared ledger label so persisted root
  contracts remain resumable.
- Amendment finalization explicitly mirrors the immutable root intent on
  simple and compound successors.
- Unknown versions, digest drift, path widening, and chain drift remain closed.

## Verification

Focused producer tests and the exact persisted Cloud parent contract reproduce
the transition from a legacy-labelled root to a canonical successor. Full
repository verification is recorded in the release handoff for the commit.
