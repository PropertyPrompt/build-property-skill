# Property build kit

Reusable tooling for building a PropertyPrompt property from a supplied floor plan with a multi-agent workflow over the PropertyPrompt MCP. It holds generic tools, building elements and scaffolding for your own product modules, never another building's geometry or product list: every property is built from its own plan, measured scale and researched products.

## Quick start

`$KIT` below is this folder (`skills/build-property/kit/` in the repo, or wherever the skill is installed).

```sh
# 1. Set up the working directory: measure the plan, draft the spec, plan, areas and create_property args
python3 $KIT/new_property.py --plan path/to/plan.png --id my-house --name "My House" \
    --width-m 10 --depth-m 8 --style "exterior ...; interior ..."
#    (or --width-ft W --depth-ft D; options: --work-root, --ceiling, --market, --currency, --storeys, --crop, --force)

# 2a. Run the build skill (skills/build-property/SKILL.md): `/build-property` in Claude Code, `$build-property` in Codex
/build-property
```

2b. Or by hand: follow `<workDir>/STATUS.md` (review `spec-overlay.png` and save the completed draft as `spec.json`, fill `plan.md` and tailor `areas.json`, call `create_property` with `create_property.draft.json`, publish `lib/materials.json`, set `startRevision` in `build-args.json`, open the viewer tab, then run `workflow/build.js` and `workflow/review.js` with `build-args.json`). The working directory is `<work-root>/<id>/`: `--work-root`, else `$PROPERTY_BUILD_WORK_ROOT`, else `~/property-build-work`. Temp and scratchpad roots are refused.

```sh
python3 $KIT/new_property.py --status my-house       # what is done, and the next step
python3 $KIT/new_property.py --check-spec my-house   # validate spec.json against measure/spec.schema.json
```

Goal: a new property is "measure the plan, fill in the plan template and areas config, run two workflows", with product geometry reused from your product cache wherever research picks a product you have already modelled.

Check the kit itself (no MCP needed): `python3 $KIT/tests/run_selftest.py`.

## How the build runs

The build is staged, and inside each stage agents work in parallel on everything except writing. One rule shapes it all: every write to the property needs the current revision and moves it on, so two agents writing at once reject each other. Reads (`validate_property`, `render_property`, `get_scene_nodes`) never change the revision.

So the work splits into **parallel preparation** and **serial writing**:

- **Preparation agents** (many at once) research products, work out geometry and generate payload files with Python into the working directory. They never call a write tool.
- **One write queue** in the workflow script. A writer agent takes one batch of payload files, sends them in order using the revision each call returns, and hands back the final revision. The next batch waits for it. Renders use a second queue, because the browser tab captures one view at a time.
- **Checker agents** validate after each write and turn problems into new payload files, which go back through the same queue.

```
Stage 0  main session   measure plan -> spec.json; create property; publish materials; open viewer tab
Stage 1  build.js       research x N areas  ||  shell modeller (outer) + shell modeller (inner)
                         shell writes -> shell check (validate + plan render vs source) -> fixes -> GATE
Stage 2  build.js       area modellers x N (in parallel; reuse the product cache and elements/)
                         each area: one queue slot (resources -> products -> nodes) -> checker -> fixes
Stage 3  build.js       lighting + cameras -> write -> check; then room access points after furnishing
Stage 4  review.js      pinned renders (writes paused) -> 3 critics in parallel
                         (plan accuracy | real house / showcase | floating, overlapping, clipped geometry)
                         -> owner fixers per area -> queued writes -> checkers; round 2 re-renders and
                         fixes medium/high only -> final validate
Stage 5  human          walk every room at standing height in the viewer and in VR
```

Agent roles and what each may do:

| Role | Runs | Writes to the property? | Produces |
|---|---|---|---|
| Researcher | one per area, in parallel with the shell | no | `research/<area>.json`: verified product pages, prices, dimensions, construction |
| Shell modeller | outer and inner, in parallel | no | wall/opening payloads (now mostly `shell/shell.py`) |
| Area modeller | one per area, in parallel after the shell gate | no | `gen/<area>.py` and its payloads, built from the product cache and `elements/` where possible |
| Writer | one at a time, through the queue | **yes, the only one** | the final revision, files written or failed |
| Checker | after each write | no | validation report, fix payloads for its own area, `OTHER(<area>)` notes for others |
| Lighting agent | after all areas | no | sun, sky, exposure, cameras, labels, settings |
| Renderer | once per review round, while the queue is held | no | images pinned to one revision |
| Critic | three per round, in parallel | no | issues with owner, severity, evidence and fix |
| Fixer | one per owning area, in parallel | no | targeted fix payloads |

Why it is staged:

- **The shell gate.** Nothing is furnished until the walls match the source plan, because every later agent places things against them. Research does not depend on walls, so it starts immediately and is usually finished by the time the gate opens.
- **One queue slot per area.** An area's resources and the nodes that use them are written together, so the server's sweep cannot delete resources whose nodes failed (troubleshooting 3).
- **Fixes go back to their owner.** The agent with an area's generator fixes that area; critics and checkers only report.
- **Bounded loops.** At most a few check-and-fix rounds per area and two review rounds. Anything still open is reported, never claimed as passed.

The main session coordinates. It edits the property only while no workflow runs, and records those edits in `MANUAL-EDITS.md`.

## Choosing models per role

Every agent call in `workflow/build.js` and `workflow/review.js` takes its model and effort from `args.models`. `new_property.py` writes these defaults into `build-args.json`:

| Role | Default | Why |
|---|---|---|
| `research` | standard, medium | finding and checking product pages |
| `shell-model`, `shell-check` | standard, medium | mostly runs `shell/shell.py` and compares with the plan |
| `modeller` | **strong, high** | writes the 3D product models: the quality you see |
| `modeller.finishes` | standard, medium | floors, trim, ceilings from `elements/` |
| `modeller.exterior` | standard, high | roof, siding, porch, landscape from `elements/` |
| `writer` | standard, low | copies payloads into tool calls; the biggest token cost |
| `checker`, `verify`, `final` | standard | reads validation output, small targeted fixes |
| `lighting`, `access-points` | standard | settings |
| `renderer` | fast, low | calls `render_property` and copies files |
| `critic`, `fixer` | **strong, high** | judges renders against the plan and fixes what it finds |

A value is a tier (`"strong"`, `"standard"`, `"fast"`), a model name, or `{"model", "effort"}` with effort `low` to `max`. The workflows map tiers to Claude models (strong Opus, standard Sonnet, fast Haiku) and pass model names such as `"fable"` through unchanged. Run step by step on another host, such as Codex, each tier maps to that host's strongest, standard and fastest model, or to the session model where subagents cannot choose one. `"role.<areaKey>"` overrides one area, for example `"modeller.kitchen": "strong"`. Roles left out use the session's model. The fast tier as a writer is cheaper but risky on long exact JSON.

## Prerequisites

- **Production MCP auth.** The PropertyPrompt MCP (`https://propertyprom.pt/mcp`; tools `mcp__PropertyPrompt__*` in Claude Code) connected and authorised in the session that runs the workflows. In Codex: `codex mcp add PropertyPrompt --url https://propertyprom.pt/mcp`, then `codex mcp login PropertyPrompt`.
- **A signed-in browser tab** on the property's viewer page (the same account), left open and in front for the whole run. `render_property` captures from it, one capture at a time. Without it the shell check and the review cannot see anything.
- Python 3.9+, standard library only (the plan measurer also needs Pillow), and Node for syntax checks.
- A durable working directory: `~/property-build-work/<propertyId>/` by default. In a hosted or cloud session whose home folder is wiped between sessions, use a folder that persists (such as the project's shared files) with `--work-root` or `PROPERTY_BUILD_WORK_ROOT`. Never `/tmp` or a scratchpad (see troubleshooting 1).
- Claude Code's Workflow tool runs `workflow/build.js` and `workflow/review.js`. Where it is missing (Codex, some hosted sessions), the skill follows the same scripts step by step, handing each agent's task to a subagent (SKILL.md, "Without the Workflow tool").

## Steps

1. **Measure the plan.** `measure/` turns the plan image into `<workDir>/spec.json` (pixels to metres, outer and interior walls, openings with swing sides, rooms, drawn item footprints, heights, id prefixes, style). Record the original resolution; convert readings from resized copies back first.
2. **Review the spec** against the image yourself: footprint, every opening, swing sides, drawn fixtures. `spec.json` is authoritative for every later agent; fix it now rather than adding errata later.
3. **Fill in the plan and areas.** Copy `templates/PLAN-TEMPLATE.md` to `docs/<PROPERTY>-PLAN.md` and fill it in. Copy `templates/areas.example.json` to `<workDir>/areas.json` and rewrite the areas (key, prefix, lights, research, scope). Scopes name `spec.json` entries; do not type coordinates.
4. **Create the property and publish materials** (main session):
   - read `get_schema` and the authoring guides, and save get_schema's `schemas` object as `<workDir>/server-schema.json` (`write_payloads` validates against it);
   - `create_property` with footprint, rooms, entrance and assumptions; **no `floors` list** unless the property needs several levels (troubleshooting 9); save the rooms array to `<workDir>/rooms.json`;
   - `put_scene_resources` with `lib/materials.json` `resources` (20 materials, one call);
   - open the viewer in a signed-in tab and leave it open.
5. **Run `workflow/build.js`** with the Workflow tool: `{scriptPath: "<kit>/workflow/build.js", args: {...}}`, args as in `templates/build.example.json` with `areas` set to the contents of `areas.json` and `startRevision` the current revision from `list_revisions`. It runs research alongside the shell, the shell gate, the areas (one queue slot each, then a check-and-fix loop), then lighting, cameras and room access points.
   Write any `mainSessionWrites` it returns (batches stopped because the host refused a removal) from the main session.
6. **Run `workflow/review.js`** with the same args, `startRevision` from `list_revisions` and `knownNonIssues` listing the owner's decisions (declined extras, chosen product lines). Two rounds of pinned renders, three critics, owner fixers and a final validation. Then walk every room in the interactive viewer (and VR); the workflow cannot check that and says so.
7. **Add new products to your product cache.** The build result lists `newModules` per area. Turn each into a cache module with its researched `PRODUCT` dict and a test, and drop property-specific placement (see "Adding your own products"). Update `lib/materials.json` if a new shared material proved useful.

To resume after a usage limit or a kill: relaunch the Workflow with `resumeFromRunId` (cached agent results replay instantly). Because all files live in the durable `workDir`, cached results still point at real files. Writers keep `<workDir>/queue-ledger.jsonl`; a resumed batch skips files already written and re-sends its resources. If you restart from scratch instead, use `args.skip` (`research`, `shell`, `lighting`, `areas: [keys done]`).

## Directory map

| Path | What |
|---|---|
| `new_property.py` | One-command setup of a working directory from a plan image; `--status`, `--check-spec`. |
| `lib/schema_check.py` | A small JSON Schema validator for `spec.schema.json` and the server's schema. |
| `lib/payload.py` | Builders (`box`, `rounded_box`, `cylinder`, `sphere`, `torus`, `extrude`, `tube`, `plane`, `circle`, `ring`, `material`), `node`, `behavior`, `render`, instance matrices (`inst_t`, `inst_trs`, `inst_euler`), `merge`, `check_layers`, `check_cutaway`, `aabb`, `validate`, `write_payloads`. `--selftest`; `--lint <payload dir> [external ids]`. |
| `lib/materials.json` | The shared `m-*` materials library, publish-ready. Its ids count as external in `payload.py`. |
| `workflow/build.js`, `workflow/review.js` | Research, shell, areas, lighting and access points through one write queue; review rounds. |
| `templates/` | `PLAN-TEMPLATE.md`, `areas.example.json`, `build.example.json`. |
| `measure/` | Plan image to draft `spec.json` (`measure_plan.py`), spec comparison, the schema and a synthetic `spec.example.json`. |
| `shell/` | Walls, openings, headers, sills, windows and doors from `spec.json`. |
| `elements/` | Building elements parameterised by spec: roof, siding, porch, landscape, plants, floor finishes, interior trim. No products of their own. |
| `products/` | The product module template, its test, a README and `discover()`/`load()` for your product cache (modules themselves live outside the kit). |
| `tests/` | `run_selftest.py` (whole-kit check), `make_synthetic_plan.py` (draws a plan from a spec) and `server-schema.fixture.json` (a copy of get_schema's schemas for the self-test; refresh it when the server's schema changes). |

Working files (outside the repo), per property, in `~/property-build-work/<propertyId>/`: `spec.json`, `server-schema.json`, `rooms.json`, `areas.json`, `research/<area>.json`, `gen/<area>.py`, `payloads/<folder>/NN-<tool>.json`, `queue-ledger.jsonl`, `validation/`, `renders/`, `review/cameras.json`, `MANUAL-EDITS.md` (anything written outside the workflow, with revision numbers).

## Module contract (`products/`, `elements/`)

Every module returns a **bundle**, the same plain dict `lib/payload.py` consumes:

```python
{"resources": [...], "products": [...], "nodes": [...]}   # optional: "external", "removeNodes", "settingsPatch", "rooms", "layerIgnore"
```

Product modules:

```python
PRODUCT = {"key", "item", "name", "retailer", "url", "price", "currency", "dimensions_m": [w, d, h], "size", "finish", "construction", "researched", "status", "notes"}

def build(prefix, inst_id, position, rotation_y=0.0, room_id=None, materials=None, qty=1, **options) -> bundle
```

- The root node is `f"{prefix}{inst_id}"` at the item's floor centre (+z is its front), `container: "furnishings"`, with `productKey`/`catalogueKey`; parts are children in local coordinates. Every id starts with `prefix`.
- Geometry ids are shared by every instance of the product within one prefix, so placing two ships the geometry once: `merge()` and `write_payloads()` keep identical resources/products with the same id once and fail on conflicting duplicates.
- `materials` maps a role to an existing material id (used as-is) or a dict merged into the default.
- The module emits one catalogue entry (`upsert_product` shape: `modelKey, room, item, name, retailer, price, qty, size, fit, status, url, dimensions [w, d, h]`).
- Quantities are not summed: identical catalogue entries merge into one. Pass the total on every call (`qty=2` for both chairs), or the total on the first call and `qty=0` on the rest (no catalogue entry).
- Two merge helpers exist: `lib.payload.merge` keeps identical duplicates once and rejects conflicting ones (use it to combine independent products); `elements._common.merge` is last-write-wins (use it when a later bundle deliberately overrides an earlier one).
- Element modules take spec.json-derived parameters (wall runs, footprint, openings) and a prefix, and return a bundle the same way. They add catalogue entries only for researched product dicts you pass in.

Typical generator:

```python
import sys; sys.path.insert(0, KIT)
from lib.payload import *
from products import load
oak_side_table = load("living.oak_side_table")      # a module in your product cache
b = merge(oak_side_table.build("lv-", "side-table", (0.9, 0, 6.4), rotation_y=1.571, room_id="living"),
          bundle([box("lv-g-shelf", (1.2, 0.03, 0.3))], [], [node("lv-shelf", None, "Shelf", (2, 1.4, 0.2), container="furnishings", geom="lv-g-shelf", mat="m-oak", behavior=DECOR)]))
paths = write_payloads(b, WORK + "/payloads/living", external=[])
```

## Adding your own products

Researched, modelled products accumulate in your product cache, outside the kit: `$PROPERTY_BUILD_PRODUCTS`, else `<work root>/products`. The kit ships no products, so it stays a generic toolkit; the cache saves modelling when a later build's research picks a product you already modelled, and its price and page are re-checked then. For each new product:

1. Research it on its real product page: price, dimensions, finish, construction (`products/README.md`).
2. Copy `products/_template.py` to `<cache>/<room>/<product>.py`, fill `PRODUCT`, and model the silhouette in `build()` following the contract above.
3. Copy `products/test_template.py` to `test_<product>.py` beside it, change its import to the module, and run it with `PROPERTY_BUILD_KIT=<kit> python3 <cache>/<room>/test_<product>.py`.

Modeller agents find modules with `products.discover(<cache>)` and `products.load(<name>, <cache>)`.

## What to expect

- **Time:** queued writes are the bottleneck: one writer at a time, each retyping every payload byte. An area of ~150 KB of payload takes several minutes to write; renders and validation checkpoints add to it. Usage limits can stop a long run; it resumes from cache.
- **Biggest levers:** smaller payloads (instances, shared geometry, reused modules) and fewer fix rounds (self-checks in the generator, `write_payloads` validation).

## Troubleshooting

1. **Working files vanish.** Files in `/tmp` or a scratchpad can be wiped mid-build. The workflows refuse temp and scratchpad `workDir`s; keep everything in a durable work root (`~/property-build-work/<propertyId>/` locally; a persistent shared folder in hosted sessions, where the home folder is wiped too).
2. **Usage limits stop the workflow.** Resume with `resumeFromRunId`; cached results reference files in the durable `workDir`. The ledger lets a resumed writer skip what landed. Keep main-session edits in `MANUAL-EDITS.md`.
3. **Resources disappear.** The server's `cleanup_unused_resources` deletes resources no node references. An area's resources and nodes go in one queue slot (`write_payloads` orders them, the workflow writes an area in one `writeFiles` call), and resumed or corrected batches re-send resources. Fix payloads ship the resources their nodes use.
4. **Writer agents mis-escape strings.** Inch marks (`"`) and backslashes break tool inputs. `payload.py` strips `"` and `\` and transliterates to ASCII (write `76x80 in`).
5. **Flickering layers (z-fighting).** 0.5 mm layer offsets collapse when rounded to 3 decimals. `write_payloads` fails when same-facing faces of layers in one assembly are under 2 mm apart after rounding; generate 3 mm offsets. Use bundle `layerIgnore` only for faces that can never be seen. Parts turned to an angle are checked in their own orientation, so boards along an angled wall are not flagged just because their bounding boxes overlap.
6. **Torus rejected.** `arc` 6.2832 > 2*pi. `torus()` clamps to 6.283185 and omits a full arc.
7. **`update_settings` refused.** `rooms`/`authoring` alone are refused: send `settingsPatch` too (`{}` is fine), never `settings` and `settingsPatch` together. `plan_calls` does this for bundle `rooms`.
8. **"Concurrent edit" partial write.** A write from outside the queue races it. Every write goes through the queue; nobody edits in the browser during a run. Writers re-read the revision with `list_revisions`; `get_property` (~130 KB) is avoided.
9. **Named camera presets missing.** An explicit `floors` list hides extra presets ("not shown on multi-floor properties"). Only set floors when needed. An outdoor walkable area at another level (a front path below the porch) needs its own floor plus `settings.stairs`, which also hides presets: choose between walkable grounds and preset buttons, and say which in the plan.
10. **Access points under furniture.** Creation-time guesses end up under furniture or a door leaf, which then rejects a write. The build sets access points in the lighting phase, after furnishing, and checks reachability; checkers report covered ones as `ACCESSPOINT:<room>` instead of moving furniture.
11. **Fixes generated but never written.** A check loop that stops after generating fixes drops them. Both workflows write every round's fixes and end with a verify-only check, or report the files as unwritten.
12. **Writers are the cost bottleneck.** Every byte is retyped. Keep files under 60 KB and areas around 150 KB; prefer instances and reused modules; omit defaults (`node()` and `material()` do).
13. **A fix stops at a removal.** Hosts can refuse `remove_scene_node` to subagents. Fixers replace nodes under their existing ids instead; `write_payloads` puts any removals first in a batch, so a refused removal stops the batch before anything else lands, and the workflow returns it under `mainSessionWrites` for the main session. The owner's later fixes carry the pending changes, so each owner has at most one pending batch, and the owner's next batch supersedes it whether that batch lands, partly lands or is refused: a stale batch is never replayed over newer work.
14. **Ceilings, upper cabinets or pendants cover the plan view.** They hang above the 0.78 m cutaway height but use cutaway `keep` (the `DECOR` and `SOLID` presets). Give anything with nothing under it from the floor `HIDE`, in its own root assembly; floor-standing items keep, however tall. `write_payloads` warns (`cutaway:`) about room assemblies that float above the cutaway height and keep parts.
15. **A finish rejected for cutaway `reduce`.** The server refuses `reduce` (and height `ceiling`) on instanced nodes, nodes with children, tilted nodes and nodes under a raised or tilted parent. `write_payloads` rejects these before anything reaches the server; give instanced boards, battens and tiles `keep` or `hide`.
16. **Hairline cracks along wall joints.** Boxes that only touch leave cracks. `shell.py` overlaps every end-to-end wall joint by 2 mm (`JOINT_OV`); hand-modelled walls should do the same, never into an opening. Wall-wall overlaps are not intersections to the server.

Also: give every shell part explicit `behavior`. Headers and sill walls are then role `wall` with `height: "fixed"` (headers cutaway `hide`), door leaves role `door`, glass role `glazing`. Without explicit behavior a `wall` is stretched to the ceiling and seals the opening, which is the legacy rule older notes describe. Never type coordinates into prompts; `spec.json` is the single source.
