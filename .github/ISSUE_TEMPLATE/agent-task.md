---
name: Agent task
about: One bounded job for one agent (see docs/PARALLEL_AGENTS.md)
title: "[task] "
labels: agent-task
---

## Goal
<!-- One or two sentences. What should be true when this is merged? -->

## Claim (files this task may change)
<!-- Exact paths. No other open task may claim these. -->
-

## Depends on / merge order
<!-- Issue numbers that must merge first, or "none". -->

## Acceptance checks
- [ ] `scripts/check.sh` passes; output pasted in the PR
- [ ] <!-- task-specific check -->

## Limits
Follow `AGENTS.md` (Standing limits). No credentials, school URLs/IDs or
student data. Branch `codex/<issue#>-<slug>`; open a draft PR with `Closes #<issue#>`.
If a file outside the claim needs changing, stop and say so in the PR.
