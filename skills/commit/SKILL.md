---
name: commit
description: >-
  Review, validate, stage, and commit an intended set of local changes. Use
  when asked to commit, or immediately before committing on request. Never
  pushes, opens a pull request, or merges; use `pull-requests` for work headed
  to review.
---

Review the intended changes, validate them, and commit only a coherent staged
set.

1. Inspect repository status and the current diff. Identify the intended scope
   and preserve unrelated user changes.
2. Review the changed behaviour and files. Fix in-scope defects, then rerun the
   relevant validation.
3. Stop for ambiguous ownership, unrelated changes that cannot be isolated,
   missing authority, or a meaningful expansion of scope. Ordinary in-scope
   fixes are part of completing the request and do not require another prompt.
4. Satisfy the commit blockers your always-loaded instructions define; they
   bind every commit and belong there rather than here. Where they define
   none, these do: relevant tests or the project's documented checks pass,
   no secrets are committed, staging is deliberate, and nothing is pushed,
   branched, or merged unless asked.
5. Stage the intended paths explicitly. Inspect the exact staged diff and
   staged status for omissions, accidental files, secrets, and unrelated work.
6. Commit with a concise message that describes the resulting change.

Do not push, open a pull request, or merge under this skill.
