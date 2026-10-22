# Parallel agents — how work is split and merged

Coordination happens in GitHub. Discussion happens in Slack #parallel-agents
(no student data in Slack). Everything in `AGENTS.md` still applies to every
agent; this document adds the working model only.

## Roles

| Role | Who | Does |
|---|---|---|
| Owner | Katherine | States goals, reviews delivered results afterward, may reject or request corrections, and resolves teaching decisions or new permissions |
| Merge coordinator | Coordinating Codex agent | Obtains independent review, verifies current checks and dependencies, merges approved routine work to `main`, and verifies the existing deployment |
| Direction | ChatGPT (Slack) | Decides what is worth building, sets priorities, writes the backlog as task issues, and stops work that would not make the school day easier |
| Workers | Codex in the verified execution environment | Implements one task issue per branch, opens a draft PR, does routine review of ordinary PRs |
| Independent reviewer | A separate Codex reviewer | Reviews exact commits, interacting changes and end-to-end evidence without approving its own implementation |
| Optional consultant | Claude Code, only at Katherine's request | Architecture or debugging advice; not a required executor or release dependency |
| Record | GitHub | Issues, branches, PRs, test results: the permanent code truth |
| Command center | Slack #parallel-agents | Direction and history in plain language |

Katherine's October 22 decision makes Codex the sole execution and integration
owner for the current SchoolHub repair. Claude is called in only at her request.
Preserve existing branches and reconcile actual external claims before a live
operation; an offline chat is not evidence that its claim was released.
Before a batch is dispatched, the question is whether each task actually makes
the school day easier; tasks that don't are dropped.

## One task = one issue = one branch = one PR

- Each task is a GitHub issue made from the **Agent task** template.
- Branch names: `codex/<issue#>-<slug>` or `claude/<issue#>-<slug>`.
- PRs open as **drafts** and link their issue (`Closes #N`).
- A task lists the files it may change (its *claim*). Two open tasks never
  claim the same file. If a worker needs a file outside its claim, it stops and
  says so in the PR instead of editing it.
- A task is done when `scripts/check.sh` passes on its branch and the PR
  description shows that output, plus the task's own acceptance checks.

## Definition of done (every PR)

1. `scripts/check.sh` passes (Python unittest + Node UI tests).
2. No failing tests. `main` is green, so any failure is the PR's to fix.
3. Only claimed files changed, or the PR explains the exception.
4. No credentials, school URLs/IDs, student records or private inputs
   (`AGENTS.md` → Standing limits).
5. Superseded docs deleted in the same commit (`AGENTS.md` → Document retirement).

## Cycle

1. Owner states a goal in #parallel-agents.
2. ChatGPT decides whether it is worth doing and writes task issues (Agent task
   template) with claims, dependencies and merge order.
3. Codex workers carry out routine reversible work within the owner's stated
   goals under standing authorization and open draft PRs. No per-batch or per-PR
   teacher approval is needed; the escalation boundaries below still apply.
4. The `Checks` workflow runs `scripts/check.sh` on every PR.
5. An independent Codex reviewer reviews the exact proposed commits. Interacting
   PRs, IC, grades, rosters, browser runs and deployment also need combined
   dependency and safety review; Claude's availability is not a prerequisite.
6. The coordinating Codex agent merges routine authorized work after independent
   technical review and passing checks, following the delegation below.
7. Report delivered behavior and verification in plain language. Katherine
   reviews results afterward and may reject or request a correction or revert.

## Reviewed merge delegation

On October 17, 2026, Katherine explicitly authorized the coordinating Codex agent
to take over routine reviewed merges for work she has approved, including the
then-current Hub PRs. She approves goals and teaching decisions; she does not
need to review code or click Merge for routine authorized changes. This replaces
the earlier owner-only merge step, without changing GitHub permissions or
security settings and without installing an auto-merge bot or schedule.

On October 18, she clarified that routine reversible work within her stated
goals should proceed without first approving each plan, task batch or PR. Her
review is after delivery. Independent technical review is still required before
merge; it is the engineering team's responsibility, not a request for teacher
approval. Do not treat an old owner-only rule or a stale checkout as revoking
this standing instruction: read the current rules before handing off work.

Before merging, the coordinator must:

- Verify the latest exact PR commit, approved scope, dependencies and target
  branch. Routine changes target `main`; any coordinated stack must reach
  `main` in its reviewed order.
- Require every applicable test and CI check to pass on that exact commit.
  Do not merge failed or pending checks, bypass protections, or use an admin
  override. Resolve merge conflicts and recheck changed code before merging.
- Obtain independent technical review of the latest exact PR commit from
  another agent or person, resolve actionable findings, and record the reviewed
  commit and validation in the PR. Any later changes require independent review
  and checks on the new commit before merging. Authors cannot supply their own
  independent approval. Preserve the combined dependency and safety review
  requirements above for interacting PRs, IC, grades, rosters, browser runs and
  deployment changes.
- Preserve original sources, student work, grade/history records, exact
  mappings, access boundaries and teacher-controlled delivery marks. Merge
  authorization does not authorize new student-data writes or policy changes.
- Verify the resulting `main` commit and the existing deployment path. Report
  source/test status, merge, deployment and live acceptance as separate facts.

If Katherine rejects a delivered result, handle it as engineering work: prepare
a targeted correction or revert on a branch, independently review and test its
latest commit, merge it under the same standing authority, and verify the
result. Preserve repository history, sources, student work, grades and delivery
records. Do not reset shared history or automatically undo student-data writes;
those retain their separate authorization and reconciliation requirements.

Ask Katherine only for a new teaching or grading policy, an unresolved source or
student-identity decision requiring her judgment, new access or sharing,
billing/schedule changes, destructive changes, or scope beyond approved work.
Technical failures remain engineering work to resolve or report with a concrete
blocker; do not return a routine merge click to her. Existing explicit school
write authorizations and the separate deployment/configuration ownership remain
unchanged.

## What stays outside this model

Cloud deployment, enable variables, billing and schedules belong to the
separate deployment owner named in `AGENTS.md`. On 2026-10-15 the owner
authorized one Actions change: the `Checks` workflow, which runs synthetic
tests on pull requests and pushes to `main` with read-only permissions, no
secrets and no deployment. School writes and
browser runs keep their existing authorizations; no task here widens them.
