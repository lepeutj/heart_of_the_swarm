# Agent Studio

## Goal

Agent Studio makes the existing standalone-agent capabilities visible through one focused product
path:

```text
describe or edit
→ validate AgentSpec
→ create immutable AgentVersion
→ run a task
→ inspect lifecycle, trajectory, result, usage, and trace
```

It is not a second agent specification and does not require a `WorkflowSpec`.

## User flow

1. Open `/agents` and select an existing agent or create a new one.
2. Configure name, goal, instructions, model, tools, and Skills manually, or ask the existing
   Builder endpoint to generate a draft.
3. Review and edit the same typed `AgentSpec` returned by the backend.
4. Validate the draft before persistence.
5. Save the first immutable version or create a new immutable version of an existing agent.
6. Select an exact version, provide one task, and queue a direct agent run.
7. Follow status and safe model/tool lifecycle events.
8. Inspect the final response, stored trajectory, usage, and MLflow trace correlation.

## Boundaries

- The backend remains the authority for validation, catalogue data, versions, and execution.
- React edits `AgentSpec`; it never constructs LangChain tools, models, callbacks, or graphs.
- Builder output is always an editable draft and is never executed automatically.
- Editing an agent creates a new `AgentVersion`; an existing version is never mutated.
- Runs always reference the exact immutable version selected by the user.
- Tools and Skills are selected from trusted backend catalogues.
- Agent Studio reuses existing run and observability APIs rather than creating a parallel lifecycle.
- Workflow Studio remains available for explicit multi-step and multi-agent composition.

## MVP layout

```text
Agents                       Agent definition
------                       ----------------
New agent                    Name / goal / instructions
ResearchAgent v3             Model
RepositoryAnalyst v1         Tools
                             Skills
                             Generate with AI
                             Validate / Save new version

Run                          Understanding
---                          -------------
Task                         Status and lifecycle events
Run exact version            Tool/model trajectory
                             Result
                             Usage and trace identifier
```

## Acceptance criteria

- a user can create an agent without entering Workflow Studio;
- generated and manual drafts use the same `AgentSpec` editor;
- available models, tools, and Skills come from backend catalogues;
- validation errors identify the field or incompatible capability;
- saving creates an immutable `AgentVersion`;
- loading an existing agent reproduces its saved configuration;
- editing creates a new version and preserves previous versions;
- a task can be run against an explicitly selected version;
- the UI shows terminal result or safe failure plus trajectory and trace correlation;
- the path works with at least one reproducible portfolio example.

## Out of scope

- workflow editing inside Agent Studio;
- automated Docker provisioning;
- deployment registry administration;
- arbitrary Python, imports, callbacks, or generated code;
- a generic form-definition framework;
- Deep Agents configuration before the comparison spike is complete.
