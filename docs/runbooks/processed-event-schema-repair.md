# Processed-event forward-only repair

Revision `s2f2_processed_event_groups`, parent `78e93118283f`.
Only `public.processed_event` is repaired. Historical migrations, consumers,
models, outbox uniqueness and Kafka offsets are unchanged.

## Approval, pause and backup prerequisites

This document does not authorize production execution. Obtain environment-specific
approval, an immutable reviewed artifact, a maintenance window and a rehearsed
recovery plan. Record operator and approval references in a restricted change record.

Stop all analytics and notification workers and other marker writers. Disable
automatic restarts, inspect every replica/scheduled job and independently verify
runtime and database activity. Record the pause verification under a non-sensitive
reference. A database lock is NOT pause evidence. `pe_pause_verified` is an operator
attestation, not an automated pause detector or authentication credential. Never
supply a false attestation to make unattended startup proceed.

Take an encrypted consistent backup and rehearse restoration separately. Capture
broker offsets and retained event availability independently: marker metadata is
not proof of committed Kafka offsets. Do not reset offsets or replay events.

## Exact supported states

- Legacy: seven original columns, PK `(event_id)`, no extra constraints/indexes.
- Partial A: legacy plus nullable `VARCHAR(100) consumer_group`.
- Partial B: Partial A plus unique, unfiltered, non-expression index
  `ix_processed_event_event_group(event_id, consumer_group)` with default options.
- Correct: eight model columns, group NOT NULL, PK `(event_id, consumer_group)`,
  no additional constraints/indexes. Existing multi-group rows are preserved.

Original types/nullability and absence of server defaults must match. Blank groups
are invalid. Wrong/missing keys, extra columns/constraints, incoming/outgoing FKs,
unknown indexes/options, event-ID-only uniqueness on an otherwise correct table,
a NOT-NULL group with the old PK, RLS, custom triggers/rules, partitioning and
inheritance are unsupported. Stop for separate review.
Do not stamp the revision, recreate tables or modify historical migrations.

## Historical reconciliation and protected manifest

No inference from topic or event type alone: both groups can process release
events. Verify EACH unattributed marker from trustworthy processing/transaction
evidence and actual deployed group configuration. If even one attribution remains
ambiguous, STOP. Do not delete, duplicate, replay or default historical markers.

With writers paused, export a private UNVERIFIED template from the repository root
using the existing DATABASE_URL binding and repository Python environment:

```sh
umask 077
.venv/bin/python scripts/prepare_processed_event_manifest.py \
  --pause-reference CHANGE-REFERENCE \
  --output /approved-private-directory/attribution.json
```

The exporter uses read-only REPEATABLE READ and exclusively creates a mode-0600
file. Keep its directory access-controlled and outside the checkout, ordinary logs
and CI artifacts. It does not verify historical provenance or authorize execution.

An authorized operator must fill `verified_by`, `approval_reference` and each
entry's exact `consumer_group` and restricted `evidence_reference`. Group strings
must be nonblank and at most 100 characters. Do not change bindings, identifiers,
version or entry coverage. Entries cover ONLY previously null groups. Non-null
values cannot be overwritten. Approval fields are trusted human attestations,
not cryptographic proof; the migration cannot establish external evidence's truth.

The binding covers database name/OID, server-start identity, schema class, count
and SHA-256 of every row/column ordered by PK, with absent groups normalized to
null, timestamps encoded with isoformat, and sorted compact JSON. Any data/schema
change or server restart requires a new export and renewed verification. Do not
rebind an old approval to a changed snapshot automatically.

The migration requires a regular file owned by its effective user, without group
or world permissions (0600/0400), and at most 10,000,000 bytes. Symlinks in any
path component, parent-directory (`..`) traversal, duplicate JSON keys,
unknown/duplicate IDs, missing records, malformed documents and conflicts fail
closed. File checks run before the exclusive lock, using descriptor-relative
no-symlink directory traversal. The same opened file is pinned through the locked
validation; it is not reopened by pathname. File metadata is rechecked before and
after a bounded byte read; changes require a fresh export/verification. Use a
physical directory path without symlinks. Larger histories require a separately
reviewed scalable procedure.

## Maintenance execution

Use the approved production PostgreSQL configuration and existing DATABASE_URL
and SECRET_KEY bindings, with workers still paused. From `backend`:

```sh
FLASK_ENV=production ../.venv/bin/flask --app run:app db upgrade \
  -x pe_pause_verified=CHANGE-REFERENCE \
  -x pe_manifest=/approved-private-directory/attribution.json
```

Empty legacy or fully attributed supported databases may omit `pe_manifest` only.
Do not use metadata schema creation or table recreation. Disable SQL echo/debug,
parameter/result logging and exception-local capture during restricted operations.

One PostgreSQL transactional-DDL transaction sets a 5-second lock timeout and
60-second per-statement timeout; locks the table exclusively; revalidates the
snapshot and manifest; adds/backfills only the missing group; checks every original
value and row count; replaces the PK; enforces NOT NULL; verifies the final shape;
and advances Alembic. Partial B's transition index is removed only after the new
PK exists. There are no intermediate commits or concurrent index builds.
Diagnostics contain only fixed codes, state classes and counts, not identifiers,
topics, offsets, digests or attribution contents.

### Mandatory operator preflight gate

Reject unrehearsed workloads. Do not infer a production row-count limit from unit
or synthetic capacity tests. Approval requires a full-size restored clone of the
intended database, preserving row widths, schema/index shape, attribution coverage
and approved manifest/evidence lengths. Keep the clone and evidence private.

Rehearse the exact reviewed artifact with production-equivalent PostgreSQL role,
grants, server version, connection path/latency and execution resource limits.
Record and obtain approval for all of the following before execution:

- Verified memory headroom above observed process peak RSS. Snapshot validation
  materializes records in memory; the manifest's 10,000,000-byte limit does not
  bound table size, Python memory or already-correct schema validation cost.
- Verified database, index-build, WAL, temporary storage and backup disk headroom.
- Measured exclusive-lock acquisition-to-commit duration, including all per-record
  updates, snapshot/manifest validation, PK creation and commit; include observed
  variability and a documented safety margin.
- An approved maintenance window accommodating that measured duration and margin,
  consumer pause/resumption checks, cancellation, rollback and recovery time.
- A rehearsed absolute wall-clock deadline supervised outside the migration.
  Demonstrate cancellation/connection termination, verify backend/lock release
  and transaction rollback from a fresh connection, and keep consumers paused
  until recovery verification completes. Do not assume a client timeout alone
  immediately releases backend locks. Identify the responsible operator and
  escalation path. This repair does not implement its own total-runtime deadline.

The 5-second lock timeout bounds waiting to acquire locks, not lock-holding time.
The 60-second statement timeout applies separately to each SQL statement, not the
whole transaction or Python/file processing. Backfill issues one UPDATE per
unattributed marker; connection latency and record count affect total lock time.
Do not silently expand timeouts or extrapolate a safe row-count threshold.

If full-size rehearsal, production-equivalent role/path, approved headroom,
measured lock duration, maintenance window or proven deadline recovery is missing,
STOP and reject execution. Large workloads may require a separately approved
staged repair. Existing unattended container migration commands intentionally
stop without attestation. No release workflow is changed or re-enabled; its
future migration execution needs separate operational review.

## Post-upgrade verification

Keep consumers paused until independently confirming:

1. Revision is `s2f2_processed_event_groups` and the exact target schema is present.
2. The composite key exists and no event-ID-only uniqueness remains.
3. Every original record and metadata value matches the protected pre-upgrade
   snapshot; all preexisting non-null groups are unchanged.
4. New attribution exactly matches approved evidence, with zero null groups.
5. Outbox, analytics and notification rows were not modified by this migration.
6. The exact reviewed artifact passed migration-built PostgreSQL regression tests.

Resume workers only under the environment's operational authorization. Monitor
consumer errors, lag, projections, deduplication and dead-letter traffic. Do not
inject synthetic business events into production as a validation step.

## Failure and rollback

On error, PostgreSQL rolls back the revision's DDL/backfill/version advancement.
Keep workers paused and verify actual schema/data/revision using a fresh connection.
Alembic's outer transaction can include preceding revisions on a fresh full-chain
upgrade; inspect committed state rather than assuming a particular failure revision.
Do not automatically retry contention or stale approval. Resolve the cause and
obtain fresh verified attribution where necessary.

Automatic downgrade is refused: a single-column key cannot retain multi-group
history. After success, use an approved forward correction. Restoring a backup
requires a separate outage/reconciliation plan covering all writes since backup,
broker offsets and external effects; it is not automatic rollback.

Keep evidence and manifests under approved access/retention controls; delete only
when authorized. Never commit or upload them to ordinary CI artifacts.

## Independent validation

The migration-parity CI job builds unique disposable PostgreSQL databases through
Alembic only and prohibits model schema creation/deletion. It verifies all tables,
columns, defaults, PKs, uniqueness, FKs and indexes, plus actual analytics and
notification handlers, redelivery and rollback. The sole parity exception is the
existing redundant `event_outbox(event_id)` unique constraint, left unchanged.

The historical migration downgrade smoke test remains scoped to the original four
revisions. New tests verify this repair refuses downgrade. Existing consumer
retry/dead-letter/offset behavior and INTEGER offset capacity remain outside scope.
