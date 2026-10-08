---
name: build-property
description: Build a complete PropertyPrompt property (shell, exterior, furnished rooms with real products, lighting, cameras, review) from a supplied floor-plan image, using the bundled kit and its multi-agent workflows. Use when the user provides a floor plan and asks to build, model or replicate a property from it.
---

# Build a property from a floor plan

You drive the kit in the `kit/` folder next to this file. Below, `$KIT` means that folder's absolute path (the host shows this skill's base directory when it loads; `$KIT` is normally `<base directory>/kit`; Stage 0 covers installs where it is elsewhere) (read its README.md once: "How the build runs" and "Troubleshooting"). The user gives a floor-plan image; you deliver a built, validated, reviewed property on the production PropertyPrompt MCP. Work through the stages in order and keep the user informed with one short line per stage. Invoking this skill is the user's opt-in to run the kit's workflows, with the Workflow tool where the host has it, otherwise with subagents.

The skill runs in Claude Code and in Codex. Tool names below are Claude Code's; use your host's equivalent:
- asking the user (`AskUserQuestion`): Codex has no multiple-choice tool, so ask the same questions as one short numbered list in plain text, with the recommended option first;
- subagents (the Agent tool): Codex's `spawn_agent` and `wait_agent` (multi-agent must be enabled in Codex);
- PropertyPrompt tools (`mcp__PropertyPrompt__<name>` in Claude Code): the same names under the server you connected, for example `put_scene_nodes`.

Hard rules:
- Working files live in `$WORK/<id>/` (Stage 0), a folder that persists between sessions, never `/tmp` or the scratchpad.
- Only writer agents in the workflow queue write to the property while a workflow runs. You write only between workflows, and log every such write in `<workDir>/MANUAL-EDITS.md` with its revision.
- `spec.json` is the single source of coordinates. Never type coordinates into prompts or scopes from memory.
- Never claim a check that did not run (renders, walkthrough, VR).

## Stage 0: check the environment

Do this silently and mention only what needs the user.

1. **Find the kit.** If `<base directory>/kit/new_property.py` does not exist (some hosts save only a skill's instructions), look for a copy: `find / -path '*build-property/kit/new_property.py' -not -path '*/node_modules/*' 2>/dev/null | head`. Use its folder as `$KIT`. If there is none, stop and ask the user to put the skill's `kit/` folder somewhere this session can read (for example the project's shared files).
2. **Pillow.** `python3 -c "import PIL"`. If that fails, run `python3 -m pip install --quiet Pillow` (retry with `--user`, then `--break-system-packages`, if refused). Stop and tell the user if it still fails.
3. **Working root (`$WORK`).** Locally, `~/property-build-work`. In a hosted or cloud session where the home folder is wiped between sessions, use a folder that persists, such as `<project shared files>/property-build-work` (for example `/mnt/project-files/property-build-work`). If you cannot tell which folders persist, ask. Pass `--work-root $WORK` on every `new_property.py` call (or `export PROPERTY_BUILD_WORK_ROOT=$WORK` in the same command).
4. **Workflow tool.** Note whether it is available (only Claude Code has it). Without it, Stages 6 and 7 follow the scripts step by step with subagents (see "Without the Workflow tool" in Stage 6); tell the user in one line that the build will be slower and use more of this session's context.
5. **PropertyPrompt MCP.** Check that its tools are available and signed in. If not, stop and tell the user how to connect it: in Claude Code, add `https://propertyprom.pt/mcp` as a connector; in Codex, `codex mcp add PropertyPrompt --url https://propertyprom.pt/mcp` then `codex mcp login PropertyPrompt`.

## Stage 1: inputs (ask only what you cannot read from the plan)

These are the inputs, with their defaults:
- overall dimensions, if the plan does not label them (needed for scale; never guess);
- property id (3-60 chars, lowercase, hyphens) and display name;
- style brief, covering each of these so the modellers do not guess:
  - exterior: cladding, roof, trim colour;
  - flooring per room type (dry rooms, wet rooms);
  - interior doors: shaker or flat slab, finish, hardware finish;
  - kitchen cabinets and worktop;
  - bathroom fixtures and tile;
  - metal finish for lights and hardware;
  - furniture mood.

  The answers go in `plan.md` and the brief, and the interior door answers go in `spec.json` (Stage 3).
- market/currency (default USA, USD);
- ceiling height (default 2.44 m / 8 ft);
- extras not on the plan (porch, landscaping, dining set, TV): these become labelled "proposed additions";
- walkable outdoor path: yes means an extra floor level, which hides the room camera buttons in the viewer; default no.
- models: the default model plan, in tiers (strong for the area modellers, critics and fixers; standard for research, writers, checks and settings; fast for rendering). Claude Code maps them to Opus, Sonnet and Haiku. Changes go into `models` in `build-args.json` (see the README, "Choosing models per role").

Ask them all at once. AskUserQuestion takes at most 4 questions with 2-4 options each, so group them into one call (in Codex, the same four as a numbered list):
1. **Style:** 2-3 complete presets that suit the plan, the recommended one first. Each option's description lists its choice for every style item above; the user overrides single items through "Other".
2. **Extras** (multi-select): the proposed additions and the walkable outdoor path, all off by default.
3. **Setup:** "Use these" first, with its description giving the proposed id and display name, market/currency, ceiling height and model plan; the other option is "Change some". If the user picks it, ask what to change in plain text.
4. **Dimensions:** only if the plan does not label them. If the plan has a scale bar, offer the dimensions measured from it as the first option; never offer a typical or guessed size. The user types the real figures through "Other".

## Stage 2: set up the working folder

```
python3 $KIT/new_property.py --plan <plan.png> --id <id> --name "<name>" --width-ft <W> --depth-ft <D> --ceiling <h> --market "<market>" --style "<style>" --work-root $WORK
```

It measures the plan and writes: `spec.draft.json`, `spec-overlay.png`, `plan.md`, `areas.json`, `create_property.draft.json`, `build-args.json` and `STATUS.md`. Use `python3 $KIT/new_property.py --status <id> --work-root $WORK` at any point to see what is done and what is next.

## Stage 3: finish the spec (you look at the plan)

1. Read the plan image and `spec-overlay.png`. Check every detected wall and opening: type (window, door, bifold, plain opening), hinge jamb and swing side for doors, and anything flagged in the draft's `review` list.
2. Fill in what measurement cannot detect:
   - `rooms`: id, name, bounds (from the wall faces), label and dimensions as printed on the plan;
   - `drawnItems`: footprints of every drawn fixture and furniture item, in plan pixels converted with the spec's m/px;
   - sill and head heights (for example a higher sill over a sink or in a bathroom);
   - entrance;
   - interior doors: `interiorDoors` (`leafStyle` `shaker` or `slab`, `leafMaterial`, `hardwareMaterial`) from the brief, and `openDeg` on any door whose leaf would hit a fitted unit;
   - walls at an angle to the plan axes (cut corners, bays): `angledWalls` with start and end points, thickness and openings measured along the wall, and a `polygon` on every room they bound. `shell.py` skips these walls; the shell modeller hand-models them, and the floors and ceilings follow the polygons.
   Every enclosed space must be a room with a way in; infer and label missing openings.
3. Save as `spec.json` and run `python3 $KIT/new_property.py --check-spec <id> --work-root $WORK` until it passes.
4. **Gate:** show the user a short summary: scale and how well the two axes agree, rooms with sizes, openings per wall, and every assumption. Do not continue until the user confirms or corrects it.

## Stage 4: plan and areas

- Fill the remaining `{{...}}` placeholders in `<workDir>/plan.md`.
- Tailor `areas.json` to the rooms that actually exist:
  - drop areas with no rooms, split large ones;
  - note in each area's scope which `elements/` modules fit;
  - set the light budgets so they total about 15.
- Check the product cache (`productsDir` in `build-args.json`, normally `$WORK/products`) for modules from earlier builds: `python3 -c "import sys; sys.path.insert(0,'$KIT'); from products import discover; [print(k, m.PRODUCT['item'], m.PRODUCT['name']) for k, m in discover('<productsDir>').items()]"`. The cache only saves modelling time: research still picks each product for this brief, a cached module is used only when research chose that same product, and its price and page are re-checked. Everything else is researched and modelled new, following `products/README.md` and `products/_template.py`.
- Fill `knownNonIssues` in `build-args.json` with the user's Stage 1 decisions that a critic could mistake for faults, one line each: declined extras ("No landscaping or porch: the owner declined extras"), the chosen style and product lines where they limit options, and the walkable-path choice. The review passes them to every critic and fixer.

## Stage 5: create the property

Do this in the main session, before any workflow:
1. Read `get_schema` and the authoring guides: geometry, materials, behavior, editing, validation, limits. Save the `schemas` object from `get_schema` as `<workDir>/server-schema.json`, exactly as returned (where the host saved the tool result to a file, copy that file). `write_payloads` validates every payload against it, so schema errors show up locally, naming the field, before anything reaches the server.
2. Call `create_property` from `create_property.draft.json` plus the spec rooms. Give rooms `accessPoint`s on clear floor. Pass **no `floors` list** unless the property has several storeys or the user chose a walkable path.
3. Call `put_scene_resources` with the `resources` array from `$KIT/lib/materials.json`.
4. Set `startRevision` in `build-args.json` to the revision returned.
5. Ask the user to open the viewer URL in a signed-in browser tab and keep it in front. Then test-render the plan view. If the render returns `ok:false`, follow its `next` field before continuing.

## Stage 6: build

First call `list_revisions` and set `startRevision` in `build-args.json` to the current revision: server housekeeping can move it between steps. Do the same before every later workflow run.

If the Workflow tool is available, run it (otherwise see "Without the Workflow tool" below):
- `scriptPath`: `$KIT/workflow/build.js`
- `args`: the contents of `build-args.json`, passed as a JSON object, not a string.
- Its `models` setting picks the model and effort per role. Leave it as written unless the user asked for changes.

It runs, in order:
1. research in parallel with the shell;
2. the shell gate;
3. the areas, each in one queue slot, then checked and fixed;
4. lighting, cameras and room access points.

While it runs:
- Check progress by reading the run's `journal.jsonl` in the transcript directory (Claude Code), or `<workDir>/STATUS.md` and `queue-ledger.jsonl`.
- Do not write to the property.
- If the user reports a visual problem, diagnose it read-only and apply the fix after the workflow ends.
- If it stops on a usage limit or a sign-out, resume with `resumeFromRunId`, the same `scriptPath` and the same args. Finished agents replay from cache and the write ledger skips files that already landed.

When it finishes, write any `mainSessionWrites` yourself: batches a writer stopped because the host refused a removal, at most one per owner (a later batch for the same owner carries the earlier one's changes and replaces it). Write each batch's files in order from the current revision, log them in `MANUAL-EDITS.md`, then validate.

### Without the Workflow tool

`build.js` and `review.js` are the procedure; run them by hand instead of skipping the stage. A missing Workflow tool is never a reason to skip a step, to do an agent's task yourself while a subagent tool is available, or to report a step as not done:
1. Read the script. Each `phase(...)` is a step, each `agent(prompt, opts)` is a task with that exact prompt (substitute the args from `build-args.json`), and `schema` is the shape its answer must take.
2. Run each task as a subagent (the Agent tool in Claude Code, `spawn_agent` in Codex), choosing the model from `models` in `build-args.json`: map each tier to your host's strongest, standard and fastest model, and use the session model where the host cannot set one per subagent. Without any subagent tool, do the task yourself.
3. Keep the script's order and gates. Tasks it runs in `parallel` or `pipeline` may run at the same time (several subagents at once), but **writes stay serial**: only one writer task at a time, each starting from the revision the previous one returned. Renders are serial too.
4. Keep the loops and their round limits (`maxCheckRounds`, `shellCheckRounds`, `lightingCheckRounds`): check, write the fixes, check again.
5. After each step, tick it in `<workDir>/STATUS.md` with the revision, so a new session can carry on from there. The write ledger (`queue-ledger.jsonl`) still skips files that already landed.

## Stage 7: review

Run `$KIT/workflow/review.js` (by hand, as above, without the Workflow tool) with the same args and `startRevision` set to the current revision from `list_revisions`. It does:
1. pinned renders;
2. three critics in parallel: plan accuracy, real house / showcase, and geometry (by hand, three subagents at once, each with its prompt from `review.js`);
3. owner fixers, through the queue;
4. a second round for medium and high severity issues;
5. a final validation.

Afterwards, write any `mainSessionWrites` as after the build, then take one render batch of the round-2 fixes yourself, because the second round's fixes are not re-rendered by the workflow.

## Stage 8: report and hand over

Report to the user:
- the viewer URL;
- the final revision;
- validation results: errors, warnings and room reachability;
- what each review round fixed;
- anything left open;
- everything not checked: the walkthrough at standing height, VR, the cover image and realistic-mode variants.

Ask the user to walk every room at standing height, and in VR if they can.

## Stage 9: feed the kit

Add every product that was modelled new in this build to the product cache (`productsDir`), so a later build that chooses the same product can reuse its geometry. The cache stays outside the kit: the kit itself never ships products.
1. Copy `$KIT/products/_template.py` to `<productsDir>/<room>/<product>.py`, fill `PRODUCT` with the researched data and move the geometry from `<workDir>/gen/<area>.py` into `build()`, dropping property-specific placement (`products/README.md`).
2. Copy `$KIT/products/test_template.py` beside it as `test_<product>.py`, change its import to the new module, and run it with `PROPERTY_BUILD_KIT=$KIT python3 <productsDir>/<room>/test_<product>.py`.
3. Add a generic building element to the kit's `elements/` only if it is parameterised by the spec, never by this property's coordinates.
4. Add any new, generally useful lesson to the README's troubleshooting list (no property names).

Changes to the kit itself (elements, lessons) are staged only if the user agrees, and never committed without asking.
