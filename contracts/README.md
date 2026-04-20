# Contracts

Each file here defines an agreed interface between two agents or system components.
Only one agent modifies a contract at a time. Both owners must acknowledge changes.

## File Format

```markdown
---
owners: [agent-a, agent-b]
status: agreed | proposed | disputed
---

[Interface definition — endpoint shapes, data formats, event schemas, etc.]
```

## Rules

- Create a contract when two components need to agree on a shared interface
- Never modify a contract unilaterally — surface the change to the EM first
- A `disputed` contract means work in that area is blocked until resolved
