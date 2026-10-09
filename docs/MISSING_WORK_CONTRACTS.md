# Current missing-work contracts

`hub_v2.missing_work_contracts.derive_plan` prepares supportive, print-only
contracts from fresh source checks. It makes no provider call, writes no grade,
and changes neither Printed nor Handed to student. A blank IC score, an old
contract, or an unassessed science explanation does not establish missing work.

The trusted host supplies the unmodified `work.lesson_work` result and three
proofs. The complete current IC roster includes the active count, unique student
names/sections, optional native IDs, start dates, and explicit assignment/Exempt
states. The source proof binds the exact result hash and confirms Forms, lesson
folders, shared and earlier intake, the actual unsorted location, and lesson
bindings. The due proof binds the original title, assignment key, recorded
due date, an independently verified opening date when available, Agenda row and
every exact Form ID. A Form's first-response/grace window is not an invented
assignment opening date. Week numbers are not IC cycle
identities; repeated weeks with the same assignment key are one assignment.

Every proof must be from the current Eastern day and no more than one hour old.
This freshness bound is for supportive printed checks, not grade authority.
Unresolved source access, intake/student matches, held work elsewhere, incomplete
roster coverage or missing flags produce one contextual coverage hold for the
class/week/assignment. Hub-only historical students are omitted. Located late
or earlier submissions close the contract; they are not missing work. Exempt,
unassigned and newly joined students, future assignments and known active
extensions are skipped.

Native IC IDs are preferred. For printing alone, a unique exact current IC
name/section can be joined to one freshly verified existing private archive
student key. First-name aliases and loose filename matches cannot provide this
proof. This fallback grants no grade authority and does not update archive
identity or delivery states. Grade entry continues to require native mappings.
An older name-filed folder may retain `identity_unverified`: printing requires
a separate current exact IC-name match and fresh private-folder readback proof;
that print-only proof does not promote the folder's stored identity status.
Blank IC start dates remain blank. A trusted explicit readback that the student
is assigned and not marked **Not rostered on due date** can prove membership
for this print-only check; no enrollment date is invented.

When extension information is unavailable, the report says **Work not located
as of [check date]**, lists the factual original due date, and invites the student
to identify an alternate submission or needed help. It makes no claim of
lateness, cleared extensions, a missed commitment, or a changed deadline. Its
signature and planning lines do not change the existing grading grace period.
No understanding or explanation level is invented.

Each report carries `report_type: missing_work_contract`, a dated
`feedbacksource_` ID, exact source-proof references and a stable
`missingworkcontract_` closure key per assignment/student. A fresh complete scan
must confirm the contract remains open before including it in each new day's
all-class print document. A verified submission resolves that closure key.
Historical contract PDFs remain historical evidence; they must not automatically
be relabeled or reprinted as current. A teacher may combine a student's current
open assignments on one page only while preserving every assignment's title,
original due date, closure key and source proof.

The pure module returns `contracts`, `resolved`, `skipped` and `coverage_holds`.
Authenticated rendering, private filing, dated packet publication, scheduler
invocation and live readback are separate integration steps. Implementation and
synthetic tests alone are not a claim that a current daily packet contains these
contracts.

The host-only `missing_work_contract_filing` module re-derives the exact bounded
input plan before filing. It saves one immutable private PDF for each student's
complete current group and preserves the original archive identity status.
Only after every expected bundle has verified bytes and owner attachment may
`publish_current_plan` publish the class pointer containing the exact open keys,
report IDs, input hashes and real evidence expiry. A complete resolved empty
plan clears those current keys while retaining earlier PDFs.

The daily source selector admits only contract IDs and keys from that trusted
latest pointer with matching input/plan proof. A new packet also requires its
actual evidence window to remain fresh. Saved dated packets stay readable as
historical snapshots after that window closes. There is no browser endpoint
that accepts a caller's missing-work declaration as source authority.
