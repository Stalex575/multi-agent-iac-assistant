---
name: crisp-dm-log
description: Append a CRISP-DM phase entry to docs/crisp_dm_log.md. Use after finishing any unit of work (data download, new node, eval run, prompt change) so the report reflects how CRISP-DM was applied.
---

# CRISP-DM log entry

Append (never rewrite history) to `docs/crisp_dm_log.md`:

```markdown
### <HH:MM> - <Phase> - <short title>
- **What:** one or two lines.
- **Why / decision:** the reason, alternatives rejected.
- **Evidence:** file paths, counts, metric values.
- **Next:** what this unlocks or what must be revisited.
```

Phases: Business Understanding, Data Understanding, Data Preparation, Modeling,
Evaluation, Deployment. If an evaluation result sends you back to an earlier phase
(e.g. low retrieval recall → re-chunking), say so explicitly - the loop-back is the
point of CRISP-DM.
