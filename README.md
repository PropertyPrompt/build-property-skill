# build-property skill

A skill for Claude Code and Codex that builds a complete [PropertyPrompt](https://propertyprom.pt) property from a floor-plan image. You supply the plan; the skill measures it, drafts a spec for you to confirm, then runs staged multi-agent workflows that build the walls and openings, the exterior, rooms furnished with real researched products, lighting and cameras, and finishes with a rendered review.

It is a toolkit, not a catalogue: every property is built from its own plan, measured scale and its own product research.

**Token use:** a build is a long multi-agent run and can use a large number of tokens. That usage is billed to your own Claude or Codex account, or comes out of your plan's allowances. The software is provided as is, under the [MIT License](LICENSE).

## Requirements

- Claude Code or Codex. In Claude Code, the Workflow tool runs the build and review stages in parallel. In Codex (and Claude Code sessions without the Workflow tool) the skill follows the same steps with subagents, more slowly. In Codex, enable multi-agent so it can hand tasks to subagents.
- The PropertyPrompt MCP connected and signed in (`https://propertyprom.pt/mcp`). In Codex: `codex mcp add PropertyPrompt --url https://propertyprom.pt/mcp`, then `codex mcp login PropertyPrompt`.
- A browser tab signed in to the same PropertyPrompt account, kept open on the property while it builds (renders are captured from it).
- Python 3.9+ with Pillow (the skill installs it if missing); Node for the workflow syntax check in the self-test.

## Install

### Claude Code

As a plugin:

```
/plugin marketplace add PropertyPrompt/build-property-skill
/plugin install build-property@propertyprompt
```

Or copy `skills/build-property/` into `~/.claude/skills/` (all projects) or `<project>/.claude/skills/` (one project).

### Codex

Copy `skills/build-property/` into `~/.agents/skills/` (all projects) or `<project>/.agents/skills/` (one project):

```
git clone https://github.com/PropertyPrompt/build-property-skill
mkdir -p ~/.agents/skills && cp -R build-property-skill/skills/build-property ~/.agents/skills/
```

## Use

Give it a plan. In Claude Code:

```
/build-property path/to/floor-plan.png
```

In Codex:

```
$build-property path/to/floor-plan.png
```

It asks a few questions (dimensions if the plan doesn't label them, property id and name, style, market, ceiling height, extras, model choices), sets up a durable working folder in `~/property-build-work/<id>/` (in a hosted session whose home folder is wiped, a persistent shared folder instead; set `PROPERTY_BUILD_WORK_ROOT` to choose it), shows you the measured spec to confirm, then builds and reviews. Progress, assumptions and anything left unchecked are reported as it goes.

## What's inside

```
skills/build-property/
├── SKILL.md            the staged procedure the agent follows
└── kit/
    ├── new_property.py   one-command setup: measure plan, draft spec, plan, areas, workflow args; --status, --check-spec
    ├── measure/          plan image -> draft spec.json, schema, example
    ├── shell/            spec -> walls, openings, windows, doors, bifolds
    ├── elements/         roof, siding, porch, landscape, plants, floor finishes, trim and ceilings
    ├── products/         how to research and model your own products: contract, template, test
    ├── lib/              payload helpers and checks, shared materials
    ├── workflow/         build.js and review.js (staged multi-agent workflows, model per role)
    ├── templates/        plan template, example areas and arguments
    └── tests/            self-test on a synthetic plan
```

`kit/README.md` explains how the build runs (parallel preparation, one write queue, gates, review rounds), model choice per role, the module contract and troubleshooting.

## Check the kit

```
python3 skills/build-property/kit/tests/run_selftest.py
```

Runs without the MCP: draws a synthetic plan, measures it, builds the shell and elements, and checks every payload.
