# Parallel agents — how work is split and merged

Coordination happens in GitHub. Discussion happens in Slack #parallel-agents
(no student data in Slack). Everything in `AGENTS.md` still applies to every
agent; this document adds the working model only.

## Roles

| Role | Who | Does | Does not |
|---|---|---|---|
| Owner | Katherine | States goals, approves task batches, merges to `main` | — |
| Architect | Claude | Turns a goal into independent task issues, names files each may touch, sets acceptance checks and merge order | Routine implementation |
| Worker | Codex (cloud) | Implements one task issue on its own branch, opens a draft PR | Touch files outside its task's claim without saying so in the PR |
| Integration reviewer | Claude | Reviews the open PRs together for collisions, duplicated fixes and cross-component breakage; recommends merge order | Merge to `main` |
| Hard debugging / reliability | Claude | Failures spanning several components; tests, checks and "done" definitions | Button-and-field changes |

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

1. Owner states a goal (Slack or chat).
2. Architect posts a batch of task issues with claims, dependencies and merge
   order, and summarizes it in #parallel-agents.
3. Owner approves the batch; workers pick up issues.
4. When the batch's PRs are open, the integration reviewer reviews them
   together and posts a merge order.
5. Owner merges.

## What stays outside this model

Cloud deployment, Actions workflows, enable variables, billing and schedules
belong to the separate deployment owner named in `AGENTS.md`. School writes and
browser runs keep their existing authorizations; no task here widens them.
