# {{PROPERTY_TITLE}} build plan

<!-- Template from the build-property skill kit. Replace every {{PLACEHOLDER}}; delete the HTML comments when done.
     new_property.py fills the measurable placeholders for you. -->

A plan for building {{SHORT_DESCRIPTION, e.g. a two-bedroom, 10 m x 8 m single-storey house}} in PropertyPrompt from a supplied floor plan, using the production PropertyPrompt MCP and the multi-agent workflow in `$KIT/workflow/`. The goals: an exterior that reads as a real building, and an interior with accurately drawn real furniture, flooring, walls and lighting, built as fast as the MCP allows.

Source plan: [{{PLAN_FILE}}]({{PLAN_FILE}})
Property ID: `{{PROPERTY_ID}}`, on the {{production | local}} PropertyPrompt server.
Working directory: `~/property-build-work/{{PROPERTY_ID}}/` (durable; never `/tmp` or a scratchpad).

## The constraint that shapes the workflow

Every PropertyPrompt write needs the current revision number, and every write moves it on. If several agents write at once, almost every write is rejected. Reads, including `render_property` and `validate_property`, don't change the revision.

So agents work in parallel on the slow parts (researching products, working out geometry, generating payloads with Python into the working directory) and every write goes through one queue in the workflow script, one at a time. That avoids conflicts between our agents; it cannot prevent conflicts with edits made in the browser, so nobody edits the property by hand while a workflow runs.

Queue rules:

- At most 100 nodes per `put_scene_nodes`, 20 definitions per `put_scene_resources`, 50 products per `upsert_product` (`{"products": [...]}`) and 1 MiB per request. `lib/payload.py` splits batches and keeps files under 60 KB.
- Each area's resources, catalogue entries and nodes go through **one uninterrupted queue slot**: the server's `cleanup_unused_resources` deletes resources that no node references, so resources published in one slot and nodes that failed in a later one disappear overnight. A resumed batch re-sends its resources.
- Each write uses the revision returned by the previous write, never a guessed increment. On a conflict the writer reads `list_revisions`, not `get_property` (~130 KB).
- `update_settings` always carries `settingsPatch`; `rooms` or `authoring` alone are refused, and `settings` with `settingsPatch` is refused.
- Every write, including one-off settings fixes from the main session, goes through the queue or happens while no workflow runs.

Writers retype every payload byte into a tool call, so payload size sets the build time:

- shaped primitives (rounded boxes, extrusions, cylinders, tubes) instead of raw meshes
- one geometry with `render.instances` for repeated parts (chair legs, planks, battens)
- the shared materials library in `$KIT/lib/materials.json`
- colour variation from materials, not uploaded textures
- reuse of `elements/` modules, and of product cache modules for products research picked

## Scale and layout

- **Scale:** {{OVERALL_DIMENSIONS, e.g. 36 ft x 26 ft = 10.97 m x 7.92 m}}. The source image is {{W}} x {{H}} pixels; the outer footprint spans about {{FW}} x {{FH}} of them (left edge x ~ {{LEFT}}, top edge y ~ {{TOP}}): {{M_PER_PX}} m per pixel, both directions agreeing within {{AGREEMENT}}%. `measure/` records the resolution and footprint bounds in `spec.json`; pixel readings from a resized copy are converted back to the original resolution first.
- **Origin and walls:** origin at the outside {{CORNER, e.g. top-left (north-west)}} corner, +x east, +z south, y up, finished floor y = 0, outside grade y = {{GRADE, e.g. -0.30}}. Outside walls {{OUTER_T}} m, inside walls {{INNER_T}} m.
- **Ceiling height:** {{CEILING, e.g. 2.44 m (8 ft)}}.
- **Floors:** {{single storey: do NOT set a floors list | list the levels}}. An explicit `floors` list hides the named preset camera buttons ("not shown on multi-floor properties"). A walkable outdoor area at another level (e.g. a front path below the porch) needs its own floor plus `settings.stairs`, which also hides presets: decide the trade-off here.

| Space | Approx. x range (m) | Approx. z range (m) | Notes |
|---|---|---|---|
| {{ROOM}} | {{X0 - X1}} | {{Z0 - Z1}} | {{windows, doors, swing side, drawn fixtures}} |

These are rounded readings. `spec.json` holds the measured values and is authoritative: when this table, an areas scope or an agent prompt disagrees with it, `spec.json` wins. Each reading is recorded as an assumption in the property's authoring record.

## Workflow

### Phase 0: setup (main session, about 10 minutes)

1. Measure the plan with `$KIT/measure/` into `<workDir>/spec.json`; review it against the image (footprint, every opening, swing sides, drawn fixtures).
2. Read the authoring guides: geometry, materials, behaviour, editing, validation, limits, examples.
3. `create_property` with the footprint, rooms (access points are provisional: they are corrected after furnishing), entrance and the unit conversion recorded as assumptions. Save the rooms array to `<workDir>/rooms.json`. Only set `floors` if the property needs more than one level.
4. Publish `lib/materials.json` with one `put_scene_resources` call (20 materials).
5. Copy `templates/areas.example.json` to `<workDir>/areas.json` and rewrite the areas; scopes refer to `spec.json` entries, not typed coordinates.
6. The owner opens the property in a signed-in browser tab and leaves it open, so `render_property` can capture.

### Phase 1: research and shell (`workflow/build.js`)

Product research for every area starts at once and runs alongside the shell.

- **Outer walls:** real gaps for every window and door, frames, glass, sills, headers, exterior doors.
- **Inner walls:** interior doorways, door leaves drawn open, bifold closet doors.
- **Check:** `validate_property` plus a plan render compared with the source plan. Nothing else starts until the shell matches. Every round's fixes are written, and the last round is followed by a verify-only check.

### Phase 2: areas in parallel

Each area agent researches its products for this brief, reuses a product cache module only for a product research picked, models the rest from verified product pages (new ones are added to the product cache afterwards), adds catalogue entries and places its own lights. Its payloads go through one queue slot, then `validate_property`, then a check-and-fix loop (at most {{MAX_CHECK_ROUNDS, default 3}} rounds; fixes are always written and verified).

| Area | Prefix | Lights | Scope |
|---|---|---|---|
| {{AREA}} | `{{xx-}}` | {{N}} | {{SCOPE}} |

### Phase 3: lighting, cameras and access points

- Sun with shadows, sky light, exposure, backgrounds.
- A camera preset per room plus an exterior kerb view (hidden if the property has a floors list).
- Labels, description, area stats, shopping notes, model notes.
- **Room access points, set after furnishing**: creation-time guesses end up under furniture. Every room is checked reachable afterwards.

### Phase 4: review and fixes (`workflow/review.js`, at most {{REVIEW_ROUNDS, default 2}} rounds)

- `validate_property` on the final revision first.
- Renders are queued (one browser tab), pinned to one revision, up to four cameras per capture; the camera set is saved so round 2 compares like with like.
- Three critics in parallel: accuracy against the source plan; real-house and showcase realism; floating, overlapping, clipped or flickering geometry.
- Confirmed issues go to the owning area's fixer; fixes go through the same queue, then a check.
- Renders can't show walkability or VR. Before the build is called finished, someone walks every room at standing height in the interactive viewer, ideally in VR too. Anything not checked is listed as not checked, never as passed.

**Expected time:** {{ESTIMATE}}. Queued writes dominate the run time, and a long run can be stopped by usage limits and resumed (see "What to expect" in the kit README).

## Defaults

1. **Ceiling height:** {{CEILING}}.
2. **Market:** {{CURRENCY and retailers}}.
3. **Style:** {{EXTERIOR STYLE}}; {{INTERIOR STYLE}}.
4. **Proposed additions:** {{items not on the plan, e.g. dining table, TV, porch, landscaping}}. These are design assumptions, not source evidence: recorded in `authoring.assumptions` as proposed additions, and as owner requests only if the owner asks for them.
5. **Property ID:** `{{PROPERTY_ID}}`.

## Models

Models per role come from `build-args.json` `models` (defaults in `new_property.py`; see the README, "Choosing models per role"). Changes for this property: {{MODEL_CHANGES, default "none"}}.
