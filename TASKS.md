# tbo_technical_engine tasks

Tasks for this repository. Bala's Claude Code session on his laptop reads a task here, works it, and writes the status
and result back; the coordinator reads this file for status. Common tasks and the task format are in
`../tbo_platform/TASKS.md` ([on GitHub](https://github.com/shankarThiyagaraajan/tbo_platform/blob/main/TASKS.md)); the rules and the step-by-step procedure (branch, check, PR,
merge, delete the branch, release, docs) are in `../tbo_platform/RULES.md` ([on GitHub](https://github.com/shankarThiyagaraajan/tbo_platform/blob/main/RULES.md)).

Format (ids are `technical-001`, `-002`, and so on; never reuse one; newest first):

```
### <id> <short title>
- status: todo | doing | blocked | done
- repos: <repository names>
- goal: <one or two sentences>
- steps:
  1. <step>
- exit: <what must be true to call it done>
- result: <PRs, merge commits, release request or "no release" with the reason, question for Bala>
- updated: <YYYY-MM-DD HH:MM UTC>
```

## Tasks

(none yet)
