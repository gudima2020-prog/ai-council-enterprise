# Development Agent Policy

## Delivery contract

1. Restate the objective, public seams, observable states, failure modes, and
   completion criteria before implementation.
2. Work in small vertical slices: failing contract test, minimal behavior,
   targeted regression, then the next slice.
3. Reuse existing architecture and policy boundaries before adding a new
   abstraction.
4. Keep errors machine-readable and metadata-only. Never place source content,
   credentials, tokens, host paths, or personal data in evidence.
5. Prepare an exact staging manifest after verification and stop. Commit and
   push require a separate Human Control decision.

## Required review gates

Run the following as separate checks so one dimension cannot hide another:

- **Standards review:** architecture, security boundaries, repository
  conventions, maintainability, and tests.
- **Spec review:** every requested behavior, negative case, and completion
  criterion is demonstrably implemented.
- **Simplification review:** duplication, unused code, speculative
  abstractions, feature envy, primitive obsession, shotgun surgery, and reuse
  opportunities.

Critical changes involving external providers, permissions, retention,
execution, or repository mutation also require an independent reviewer. A
reviewer conflict escalates to Human Control; it is never silently resolved by
the builder.

## Context checkpoint

Long-running work should preserve a compact, content-free checkpoint:

```json
{
  "objective": "",
  "confirmed_facts": [],
  "changed_files": [],
  "test_results": [],
  "open_questions": [],
  "blocked_actions": [],
  "branch": "",
  "current_commit": "",
  "migration_head": "",
  "next_safe_action": ""
}
```

Reference commits, ADRs, and specifications instead of copying them into the
checkpoint. Remove secrets, document text, personal data, and one-time tokens.

## Third-party skills and agent configuration

- Never auto-install or auto-update executable skills, hooks, plugins, or MCP
  servers.
- Record the upstream repository, immutable commit SHA, license, local review,
  tool permissions, network policy, and content classifications before use.
- Vendor only the reviewed material required by the product and retain the
  applicable copyright and license notices.
- Adapt Bash-oriented procedures to the repository's Windows PowerShell and
  `.venv` workflow.
- Broad shell/network permissions and automatic approval are incompatible with
  Human Control and Workspace Policy.
