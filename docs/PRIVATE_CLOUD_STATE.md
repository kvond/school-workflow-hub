# Private cloud state and shared run ownership

This phase connects a storage implementation to the existing Python hub. It
does not activate Agenda saves, student assessment, IC writes, email or printing.
The hosted store is Firestore; SQLite remains the disposable offline test ledger.
There is no second cloud task database or dual-write synchronization.

## Persistence contract

`core.cloud_state.CloudState` uses `adapters.firestore_state.FirestoreState`.
The REST adapter uses the attached runtime service account, fixed Google HTTPS
endpoints, bounded JSON, and no browser-supplied credentials or local fallback.

- One task is identified by workflow, stable class and stable assignment IDs.
  These are hashed for document names; names or document URLs are not record keys.
- A logical request has an idempotency key and an input hash. Hash the complete
  source revisions, scope, prompt/rubric versions, checkpoint and policy version.
  A completed input also has an immutable receipt, so a different request ID
  cannot cause the same successful input to be processed again.
- Each active worker uses a **unique attempt ID** as owner, not a shared chat or
  account name. The task contains the only current owner and a monotonically
  increasing fencing counter. Concurrent workers cannot both commit ownership.
- Leases use Firestore read time. Renew during long work. Repeated requests for
  an active run cannot transfer it to another owner. Recovery after expiry uses
  a new request/attempt and a higher counter; an old owner cannot update state
  after a successor claims it. Lease expiry alone is not proof an external action
  failed. Reconcile any uncertain effect before retrying it.
- Results are immutable named records, published atomically with the run receipt
  and task state. JSON is bounded to 250 KB per record, 2 MB per result batch,
  and 32 named records. Partition larger reports before calling the store.
- A successful baseline update requires complete-scan evidence, compatible
  scope, fresh monotonic observation time and the expected checkpoint token.
  Failed/partial observations leave the previous baseline intact. Run records
  retain prior successful baseline metadata.
- Completion of storage moves the task to **Needs you** for result review. It
  never asserts that a grade was posted or a feedback document delivered.

The core uses an atomic compare-and-set commit, including the task record, for
every ownership/publication change. Firestore update-time/create preconditions
are enforced server-side. Network errors may have an uncertain commit outcome:
retry the same logical request and read back its receipt; never invent a new
request to bypass an uncertain result.

## Boundary with teaching workflows

The existing due-completion workflow, validated teaching-results envelope and
learning-observation validator remain authoritative for content. Trusted host
workers must validate those inputs before storing them. This release does not
accept generic student records through a browser API or supply a live teaching
results provider. It adds the durable state and ownership layer those providers
will use. Reference result records from the same private task record.

State fencing protects stored task/result updates. IC, email and printer adapters
still need their own idempotency, bound approvals and destination readback; a lock
alone cannot undo an already submitted external action. Those actions stay off.

## Cloud setup

Run the versioned script from an authenticated Cloud Shell in the existing
project; substitute the actual project, service and existing HTTPS service origin.
It prints a plan without `--apply`:

```sh
python3 scripts/configure_cloud_state.py \
  --project=YOUR_PROJECT --service=YOUR_SERVICE --region=us-central1 \
  --public-origin=https://YOUR_EXISTING_SERVICE.run.app \
  --configure-service --apply
```

The script enables Firestore, creates or reuses the `(default)` Standard Native
database, enables deletion protection, grants the existing service account
`roles/datastore.user` conditioned on this database, and excludes the opaque
payload field from indexing. An existing database with a different mode/region
is not replaced. It preserves existing Cloud Run variables and access controls.
No schedules, paid model calls, public access or teaching writes are enabled.

The default database is chosen for Firestore's free allowance. Usage beyond the
allowance can incur charges; backups/PITR are separate choices and are not enabled
by this script. See [pricing](https://firebase.google.com/docs/firestore/pricing),
[database access conditions](https://firebase.google.com/docs/firestore/manage-databases#configure_per-database_access_permissions),
and [atomic commits](https://docs.cloud.google.com/firestore/docs/reference/rest/v1/projects.databases.documents/commit).

## Authenticated acceptance

`POST /api/storage-check` is a narrow diagnostic exception to the app's read-only
HTTP routes. It requires signed IAP identity, an explicitly configured HTTPS
Origin, `application/json`, and only `{ "action": "verify", "probe_id": "UUID" }`.
It writes a fixed fictional sample, attempts a competing claim, publishes a
result, retries it, and reads the actual stored records with a separate client.
It cannot receive student work, grades, contacts, destinations or arbitrary code.

`GET /api/storage-check?probe_id=UUID` reads those records without writing. A
first successful round trip reports `write_readback_verified`. Update the existing
service with `HUB_STATE_ACCEPTANCE_PROBE=UUID`, causing a new revision, and reread.
Only successful readback from a different Cloud Run revision reports
`restart_verified` and `persistent_storage_verified=true`. A configured database,
local test, same-revision read or missing probe cannot set that flag.

After this acceptance, `/api/readiness` checks the saved probe with the current
runtime identity. Storage verification does not enable Save or Run. Test unsigned
access as well. Keep probe IDs and receipts in the deployment record; the probe
contains no student data. Local concurrency/expiry tests model the store and do
not substitute for live persistence and runtime IAM acceptance.

## Remaining integration

Next: private result provider and stable lesson/student mapping; complete source
readers and review; actual save/post/file/deliver adapters; destination receipts;
backup/restore policy for real student history; then one supervised end-to-end
lesson before unattended runs. Research remains with its separate continuation.

## Deployment acceptance: September 15, 2026

The existing private repository published implementation commit `3b31324`; Cloud
Build deployed it successfully. The default Native database is in the service's
region, with deletion protection and the payload index exemption. Runtime access
uses the attached service identity and a database-conditioned IAM binding. The
project inspection found no public principal grants.

At 22:53:33 UTC, an authenticated HTTPS POST returned 200 and
`write_readback_verified`. It saved only the fixed fictional sample, rejected a
competing claim, retried the result and reread the stored data. After deployment
of a different Cloud Run revision, the authenticated GET at 22:54:41 UTC returned
200, `restart_verified=true` and `persistent_storage_verified=true`. Readiness at
22:54:46 UTC independently returned verified IAP/Agenda metadata, storage
`restart_verified` and claims `storage_probe_verified`.

Anonymous requests to the hub storage/readiness endpoints redirected to Google
sign-in; an anonymous database read returned 403. Save and Run remained false;
the diagnostic reported zero student records and zero school writes. Its probe
ID is retained in the service's `HUB_STATE_ACCEPTANCE_PROBE` configuration.

All 460 Python tests passed. Local tests additionally force workers to read the
same task version and pause an old worker before a successor's claim: exactly
one claim succeeds, and the old commit cannot publish a result. Local expiry and
baseline tests are not claims that a real school workflow has recovered from an
interruption. School adapters and their destination receipts remain pending.
