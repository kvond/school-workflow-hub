# Parallel agents — how work is split and merged

Coordination happens in GitHub. Discussion happens in Slack #parallel-agents
(no student data in Slack). Everything in `AGENTS.md` still applies to every
agent; this document adds the working model only.

## Roles

| Role | Who | Does |
|---|---|---|
| Owner | Katherine | States goals, approves task batches, merges to `main` |
| Direction | ChatGPT (Slack) | Decides what is worth building, sets priorities, writes the backlog as task issues, and stops work that would not make the school day easier |
| Workers | Codex (cloud) | Implements one task issue per branch, opens a draft PR, does routine review of ordinary PRs |
| Engineering specialist | Claude Code | Hard debugging, failures that cross components (IC helper, Cloud Run, auth, mismatched records), architecture second opinions, and review where several PRs or systems interact |
| Record | GitHub | Issues, branches, PRs, test results: the permanent code truth |
| Command center | Slack #parallel-agents | Direction and history in plain language |

Claude is called in for a specific problem, not kept running as another worker.
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
2. No new failing tests; the known baseline failure is listed in the check
   output until its own task fixes it.
3. Only claimed files changed, or the PR explains the exception.
4. No credentials, school URLs/IDs, student records or private inputs
   (`AGENTS.md` → Standing limits).
5. Superseded docs deleted in the same commit (`AGENTS.md` → Document retirement).

## Cycle

1. Owner states a goal in #parallel-agents.
2. ChatGPT decides whether it is worth doing and writes task issues (Agent task
   template) with claims, dependencies and merge order.
3. Owner approves; Codex workers pick up issues and open draft PRs.
4. The `Checks` workflow runs `scripts/check.sh` on every PR.
5. Codex reviews ordinary PRs. Claude reviews when PRs interact or touch IC,
   grades, rosters, browser runs or deployment.
6. Owner merges.

## What stays outside this model

Cloud deployment, enable variables, billing and schedules belong to the
separate deployment owner named in `AGENTS.md`. On 2026-10-15 the owner
authorized one Actions change: the `Checks` workflow, which runs synthetic
tests on pull requests and pushes to `main` with read-only permissions, no
secrets and no deployment. School writes and
browser runs keep their existing authorizations; no task here widens them.
