export const meta = {
  name: 'property-build',
  description: 'Build a PropertyPrompt property from a measured spec: research, shell, areas, lighting/access points through one serial write queue',
  whenToUse: 'After Phase 0 (spec.json reviewed, property created, m-* library published, areas config filled in). Run workflow/review.js afterwards.',
  phases: [
    { title: 'Research', detail: 'real-product research per area, in parallel with the shell' },
    { title: 'Shell', detail: 'outer and inner walls, queued writes, validate + plan render check' },
    { title: 'Areas', detail: 'model each area, one queued write slot per area, validate and fix' },
    { title: 'Lighting', detail: 'sun, sky, exposure, cameras, labels, then room access points after furnishing' },
  ],
}

// ---------------------------------------------------------------- arguments
// Required: propertyId, workDir (durable, never /tmp), kitDir, planImage, brief, startRevision, today, areas
// Optional: market, style, lightBudget, maxCheckRounds, shellCheckRounds, lightingCheckRounds,
//           shell (array of {key,prefix,scope}), skip {research, shell, lighting, areas: [keys]}, cameras
const A = args || {}

// ---------------------------------------------------------------- models per role
// args.models maps a role (optionally "role.<areaKey>") to a tier ("strong" | "standard" | "fast") or a
// Claude model name ("opus" | "sonnet" | "haiku" | "fable"), or {model, effort}. Roles: research, shell-model,
// shell-check, modeller, writer, checker, verify, lighting, access-points, renderer, critic, fixer, final. Unlisted roles use the session model.
const MODELS = A.models || {}
const TIERS = { strong: 'opus', standard: 'sonnet', fast: 'haiku' }
function M(role, opts, key) {
  const spec = (key !== undefined && MODELS[role + '.' + key]) || MODELS[role]
  if (!spec) return opts
  const s = typeof spec === 'string' ? { model: spec } : spec
  const o = { ...opts }
  if (s.model) o.model = TIERS[s.model] || s.model
  if (s.effort) o.effort = s.effort
  return o
}
for (const k of ['propertyId', 'workDir', 'kitDir', 'planImage', 'brief', 'startRevision', 'today', 'areas']) {
  if (A[k] === undefined || A[k] === null) throw new Error(`args.${k} is required (see kit/templates/build.example.json)`)
}
if (/^(\/tmp|\/private\/tmp|\/var\/folders|\/private\/var\/folders)/.test(A.workDir) || A.workDir.indexOf('/scratchpad') >= 0) {
  throw new Error('args.workDir must be durable (e.g. ~/property-build-work/<propertyId>), not a temp or scratchpad directory: temp files can be wiped mid-build')
}
const PID = A.propertyId
const DIR = A.workDir
const KIT = A.kitDir
const PLAN = A.planImage
const PRODUCTS = A.productsDir || DIR.replace(/\/[^/]+\/?$/, '/products')
const AREAS = Array.isArray(A.areas) ? A.areas : (A.areas.areas || [])
for (const a of AREAS) if (!a.key || !a.prefix || !a.scope) throw new Error(`area ${JSON.stringify(a).slice(0, 80)} needs key, prefix and scope`)
const MARKET = A.market || 'USA, USD, US retailers'
const STYLE = A.style || 'see spec.json style'
const LIGHT_BUDGET = A.lightBudget || 15
const MAX_CHECK = A.maxCheckRounds || 3
const SHELL_ROUNDS = A.shellCheckRounds || 3
const LIGHT_ROUNDS = A.lightingCheckRounds || 2
const SKIP = A.skip || {}
const SKIP_AREAS = new Set(SKIP.areas || [])
let rev = A.startRevision

const SHELL_PARTS = A.shell || [
  { key: 'shell-outer', prefix: 'so-', scope: 'the OUTER walls from spec.outerWalls with every window and door opening as a real gap: window frames + glass + interior sills, headers above openings, sill walls below windows, the exterior door leaves drawn open as spec says, thresholds.' },
  { key: 'shell-inner', prefix: 'si-', scope: 'all INTERIOR walls from spec.interiorWalls with real gaps for every interior doorway and bifold/closet opening listed there; door headers; hinged interior door leaves drawn open on the hinge side in spec; bifold doors folded at the jambs.' },
]

// ---------------------------------------------------------------- serial queues
// Every PropertyPrompt write goes through writeChain. Renders go through renderChain (one browser tab).
let writeChain = Promise.resolve()
function queuedWrite(fn) { const p = writeChain.then(fn); writeChain = p.catch(() => {}); return p }
let renderChain = Promise.resolve()
function queuedRender(fn) { const p = renderChain.then(fn); renderChain = p.catch(() => {}); return p }

// ---------------------------------------------------------------- shared prompt text
const COMMON = `
Project: PropertyPrompt production MCP (tools named by their PropertyPrompt name, e.g. put_scene_nodes; in Claude Code they are mcp__PropertyPrompt__<name>, loaded with ToolSearch "select:<names>" before calling). Property id: ${PID}.
Durable working directory: ${DIR} (never write build files to /tmp or a scratchpad: they get wiped).
- ${DIR}/spec.json: measured spec sheet (source scale, walls, openings, rooms, drawn item footprints, heights, materials, id prefixes, style). Its coordinates are AUTHORITATIVE: if any figure in a prompt disagrees with spec.json (including an ERRATA_READ_FIRST block), spec.json wins.
- Source plan image: ${PLAN}. Look at it.
- Build brief: ${A.brief}
- Kit: ${KIT} (lib/payload.py, lib/materials.json, products/README.md, elements/, shell/, README.md).
- Product cache: ${PRODUCTS} (product modules modelled in earlier builds, one per real product; may be empty).
- Shared materials already published (use by id): every id in ${KIT}/lib/materials.json (m-oak, m-paint, m-trim, m-glass, ...). Add your own materials/geometry only with your area's prefix.
Read the authoring guides you need with the get_authoring_guide tool (geometry, materials, behavior, editing, validation, limits, examples); reads are safe.
Avoid get_property (its output is ~130 KB): use list_revisions for the current revision, get_scene_nodes (summary true for a placement list) for nodes, ${DIR}/rooms.json for rooms.
HARD RULE: you must NOT call any PropertyPrompt write tool (put_scene_nodes, put_scene_resources, upsert_product, place_product, update_settings, remove_*, restore_revision, rename_property, import/update_property_image) unless your instructions say you are THE WRITER. One queue does every write; an outside write causes "Concurrent edit" partial writes.
`

const PAYLOAD_RULES = `
PAYLOADS (read carefully):
- Generate payloads with a Python generator kept in ${DIR}/gen/, never by hand-typing JSON. In the generator:
    import sys; sys.path.insert(0, "${KIT}/lib"); sys.path.insert(0, "${KIT}")
    from payload import *   # box, rounded_box, cylinder, sphere, torus, extrude, tube, plane, circle, material, node, behavior, render, inst_t, inst_trs, merge, write_payloads, SOLID, SEAT, DECOR, HIDE
  Build one bundle {"resources","products","nodes"} for the whole batch and call write_payloads(bundle, "${DIR}/payloads/<folder>", external=[ids that already exist on the server]). It strips quote and backslash characters from strings, rounds to 3 dp, clamps torus arcs, validates every resource, node, product and settings key against the server's own schema (${DIR}/server-schema.json; errors name the field) and ids/parents/references, FAILS on coplanar layers closer than 2 mm (z-fighting), splits files (<=20 resources, <=50 products, <=100 nodes, <=60 KB) and orders them resources -> products -> nodes (parents first) -> update_settings. Fix what it reports; never bypass it. Return the paths it returns, in order.
- REUSE FIRST: before modelling a product, look in the product cache ${PRODUCTS} (one module per real product, see ${KIT}/products/README.md) and ${KIT}/elements/ (roof, siding, porch, landscape, plants, floor finishes, interior trim). Each product module has a docstring and build(prefix, inst_id, position, rotation_y=0, room_id=None, materials=None, qty=1, ...) -> bundle; load one with: from products import load; mod = load("<room>.<module>", "${PRODUCTS}"). Reuse a cached module only when it is the product your research chose for this property, with its page and price re-checked; never choose a product because a module exists. Merge module bundles with merge().
- File format (write_payloads produces it): ${DIR}/payloads/<folder>/<NN>-<tool>.json = compact JSON {"tool":"<tool name without prefix>","args":{...}}, args WITHOUT propertyId/expectedRevision. Allowed tools: put_scene_resources, upsert_product (args {"products":[...]}), put_scene_nodes, remove_scene_node, remove_scene_resource, remove_product, update_settings (args must include "settingsPatch" - rooms or authoring alone are refused - and never "settings").
- Keep payloads small: writers retype every byte (payload size IS build time). Aim <= 150 KB per area. Use shaped geometry (roundedBox, extrude, cylinder, tube, torus) not buffers; share one geometry across copies with render.instances (inst_t / inst_trs); omit default fields (node() does); colour variation from materials, no image uploads.
- Geometry: box dimensions are [x, y, z]; catalogue dimensions are [width, depth, height]. Box/roundedBox/cylinder/sphere are centred; extrude runs z=0..depth (rotate [-pi/2,0,0] to extrude upward; local y then maps to world -z); plane/circle face +Z. Instance matrices are column-major, translation at 12,13,14.
- Furniture: a root node (parentId null) with container "furnishings", productKey, catalogueKey (= the upsert_product modelKey), roomId, at the item's floor centre; children are parts in local coordinates. Built-ins drawn on the plan (counters, tub, WC, vanity, closet shelving) use role "fixture" on the root. Every solid part gets explicit behavior: big furniture/fixture bodies SOLID (desktop+vr collision), beds/sofas/chairs SEAT (enterable true on root, vr collision only), rugs/decor/floor finishes and floor-standing lamps DECOR (no collision, raycast false where decorative). Door leaves: role "door".
- Cutaway (the plan view and the cutaway camera cut walls at 0.78 m): anything that hangs above that height with nothing under it from the floor uses cutaway "hide" (HIDE): ceilings, ceiling and pendant lights, wall sconces, upper cabinets, range hoods, wall shelves, wall art and mirrors above the counter line, curtains and rods, ceiling fans. Floor-standing items keep "keep", even tall ones (wardrobes, tall pantries, fridges, bookcases, floor lamps), so their footprint stays visible in plan. Put a wall-hung item in its own root assembly, not under a floor-standing one, so it can hide on its own. write_payloads warns about kept assemblies that float above the cutaway height.
- Layers: decals, art, screens, rugs, tile/grout and floor finishes stacked within one assembly need >= 2 mm between same-facing faces AFTER rounding (generate offsets of 3 mm to be safe). A rug on the floor-finish layer: finish y 0.000-0.003, rug from y 0.005.
- Sinks in cabinets: carcases are shells (sides, back, bottom, rails) with the bowl in the gap. Rugs stop short of legs where easy.
- Ceiling lights: root at [x,0,z] with ceilingDrop and positive-Y child offsets. A light source is a node with light {type:"point"|"spot", color, intensity, distance, decay, ...}; no castShadow indoors. Start with point intensity ~2, distance 6, decay 2, colour #ffe2b8.
- Real products: every catalogue entry cites a verified manufacturer/retailer PRODUCT page (not search/category), real price, real dimensions, finish; status "Verified ${A.today}". Model the recognisable silhouette with multipart geometry; a single rounded box is not a sofa.
- IDs: every id you create starts with your prefix. Do not touch other areas' ids.
`

// ---------------------------------------------------------------- writer
const WRITE_SCHEMA = {
  type: 'object',
  properties: {
    finalRevision: { type: 'integer' },
    written: { type: 'array', items: { type: 'string' } },
    failed: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, error: { type: 'string' } }, required: ['file', 'error'] } },
    notWritten: { type: 'array', items: { type: 'string' } },
  },
  required: ['finalRevision', 'written', 'failed', 'notWritten'],
}

function writerPrompt(files, startRev, label) {
  return `You are THE WRITER for the PropertyPrompt write queue (batch "${label}"): the only agent allowed to write right now.
Property id: ${PID}. Starting expectedRevision: ${startRev}.
Files to write, strictly in this order (one uninterrupted queue slot: resources and the nodes that use them go together, because the server's cleanup_unused_resources deletes resources nothing references):
${files.map((f, i) => `${i + 1}. ${f}`).join('\n')}

Ledger: ${DIR}/queue-ledger.jsonl records every successful write as one JSON line {"batch","file","revision"}.
1. Make sure these PropertyPrompt tools are loaded: put_scene_resources, put_scene_nodes, upsert_product, remove_scene_node, remove_scene_resource, remove_product, update_settings, list_revisions, get_scene_nodes (in Claude Code: ToolSearch "select:mcp__PropertyPrompt__put_scene_resources,mcp__PropertyPrompt__put_scene_nodes,mcp__PropertyPrompt__upsert_product,mcp__PropertyPrompt__remove_scene_node,mcp__PropertyPrompt__remove_scene_resource,mcp__PropertyPrompt__remove_product,mcp__PropertyPrompt__update_settings,mcp__PropertyPrompt__list_revisions,mcp__PropertyPrompt__get_scene_nodes").
2. Resume check: grep the ledger for batch "${label}". If some of its files are already recorded, this is a resumed batch: skip recorded put_scene_nodes / upsert_product / remove_* / update_settings files, but RESEND every put_scene_resources file of the batch anyway (idempotent; resources may have been swept while their nodes were missing). Check list_revisions so you start from the real current revision.
3. For each file: cat it; it is {"tool":T,"args":A}. Call the PropertyPrompt tool T with A plus propertyId "${PID}" and expectedRevision = the current revision. Copy A EXACTLY (same ids, numbers, nesting); do not improve, reorder, drop or round anything; make sure your tool input is valid JSON. Use the revision the call returns as the new current revision (never guess an increment). Then append the ledger line with a shell command (printf '%s\\n' '{...}' >> ${DIR}/queue-ledger.jsonl).
4. If your own tool input fails to parse, re-read the file and retry (max 2). On a revision conflict: list_revisions, and if the newer revision could be your own earlier call (e.g. after a timeout), check with get_scene_nodes whether that file is already applied before resending; retry at most twice. Never call get_property.
5. If the host refuses a remove_* call (a permission denial, not a server error), do not retry it: record that file under failed with an error starting "NEEDS_MAIN_SESSION", list every remaining file of this batch under notWritten, and stop. write_payloads puts removals first, so nothing else of the batch has landed.
6. On any other rejection (schema, intersection, blocked navigation, missing reference): do NOT edit the payload and do NOT retry. Record the verbatim error (with any plan excerpt and coordinates, up to ~3000 chars) under failed, list every remaining file of this batch under notWritten, and stop.
Return finalRevision (latest revision after your last success, or the starting one), written, failed, notWritten.`
}

// Batches stopped by a host-refused removal, at most one per owner, for the main session to write after
// the workflow. The owner's later agents fold a pending batch into any new fixes, so the owner's next
// batch supersedes it whatever happens: refused, it replaces it; otherwise it retires it, and whatever
// of the new batch did not land goes through the usual failed/notWritten path. None is replayed.
const deferred = new Map()
function pendingText(owners) {
  const p = owners.map(o => deferred.get(o)).filter(Boolean)
  return p.length ? `PENDING for the main session (stopped at a removal the host refused; NOT landed): ${JSON.stringify(p)}. Do not regenerate it on its own. If you produce any fix files, regenerate its changes into them as well (its removals first), because your new batch replaces it.` : ''
}
function settleDeferred(owners, r, label) {
  const refused = r ? r.failed.filter(f => /^NEEDS_MAIN_SESSION/.test(f.error)) : []
  for (const o of owners) deferred.delete(o)
  if (refused.length) deferred.set(owners[0], { owner: owners[0], batch: label, files: [...refused.map(f => f.file), ...r.notWritten] })
}

// owners: the owner this batch belongs to first, then any other owners whose pending batch it folds in.
async function writeFiles(files, label, phaseName, owners = [label]) {
  if (!files || !files.length) return { finalRevision: rev, written: [], failed: [], notWritten: [] }
  return queuedWrite(async () => {
    const r = await agent(writerPrompt(files, rev, label), M('writer', { label: `write:${label}`, phase: phaseName, schema: WRITE_SCHEMA, effort: 'low' }))
    if (r && typeof r.finalRevision === 'number' && r.finalRevision > rev) rev = r.finalRevision
    settleDeferred(owners, r, label)
    log(`write ${label}: ${r ? r.written.length : 0} ok, ${r ? r.failed.length : '?'} failed, rev now ${rev}`)
    return r || { finalRevision: rev, written: [], failed: [{ file: files[0], error: 'writer agent died' }], notWritten: files }
  })
}

// ---------------------------------------------------------------- schemas
const MODEL_SCHEMA = {
  type: 'object',
  properties: {
    files: { type: 'array', items: { type: 'string' }, description: 'absolute payload paths in write order (from write_payloads)' },
    summary: { type: 'string' },
    products: { type: 'array', items: { type: 'string' } },
    reused: { type: 'array', items: { type: 'string' }, description: 'modules reused (product cache names, elements/...)' },
    newModules: { type: 'array', items: { type: 'string' }, description: 'products modelled from scratch that should be added to the product cache' },
    assumptions: { type: 'array', items: { type: 'string' } },
  },
  required: ['files', 'summary'],
}
const CHECK_SCHEMA = {
  type: 'object',
  properties: {
    done: { type: 'boolean', description: 'true when this owner has no remaining real problems' },
    issues: { type: 'array', items: { type: 'string' } },
    fixFiles: { type: 'array', items: { type: 'string' }, description: 'new payload files to write (empty if done)' },
    summary: { type: 'string' },
  },
  required: ['done', 'issues', 'fixFiles', 'summary'],
}
const VERIFY_SCHEMA = {
  type: 'object',
  properties: { ok: { type: 'boolean' }, open: { type: 'array', items: { type: 'string' } } },
  required: ['ok', 'open'],
}
const RESEARCH_SCHEMA = {
  type: 'object',
  properties: { file: { type: 'string' }, count: { type: 'integer' }, gaps: { type: 'array', items: { type: 'string' } } },
  required: ['file', 'count', 'gaps'],
}

function failedText(w, key, round) {
  if (!w || !w.failed || !w.failed.length) return 'The last queued write succeeded.'
  if (w.failed.every(f => /^NEEDS_MAIN_SESSION/.test(f.error))) return 'The last queued write stopped at a removal the host refused; it is listed as PENDING below.'
  return `The last queued write FAILED:\n${JSON.stringify(w.failed, null, 1)}\nNot written: ${JSON.stringify(w.notWritten)}
Correct those files (regenerate with the generator into a NEW folder ${DIR}/payloads/${key}-fix-r${round}/) and resubmit them together with every not-written file still needed AND every put_scene_resources file whose resources those nodes use (resources whose nodes never landed may have been swept).`
}

// Check-and-fix loop. Lesson: the loop must never end with generated-but-unwritten fixes.
// Every fix set is written; after the last round a verify-only agent reports what remains.
async function checkLoop(key, prefix, scopeText, writeResult, phaseName, maxRounds) {
  let last = writeResult
  let result = null
  let wroteAfterLastCheck = false
  for (let round = 1; round <= maxRounds; round++) {
    result = await agent(`${COMMON}
You are the CHECKER/FIXER for "${key}" (id prefix "${prefix}"), round ${round}/${maxRounds}. Scope: ${scopeText}
${failedText(last, key, round)}
${pendingText([key])}
1. validate_property (plan true, maxIssues 200). Save the full output to ${DIR}/validation/${key}-r${round}.json.
2. Read the character plan and reachability report: every room accessPoint must be reachable from the entrance; no sealed floor ('!') caused by your nodes; no blocked doorways. Look into EVERY solid intersection and mesh-collision candidate involving "${prefix}" ids and decide: real problem (interpenetration, through a wall, floating, blocking a route) or harmless surface contact. An accessPoint that your furniture now covers is NOT yours to fix: report it as "ACCESSPOINT:<roomId>" (the lighting phase moves access points after furnishing).
3. Use get_scene_nodes for details; your generator is in ${DIR}/gen/ and payloads in ${DIR}/payloads/.
4. For real problems in YOUR area, edit the generator and emit only the changed nodes/resources with write_payloads into ${DIR}/payloads/${key}-fix-r${round}/ and list them in fixFiles (put_scene_nodes replaces whole nodes: complete transforms. Change a node by replacing it under its existing id, reusing the old child ids for the new parts; use bundle "removeNodes" only for parts that must disappear, because the host may refuse removals). Problems owned by another area: list in issues as "OTHER(<area>): ...". Never disable collision to hide a problem.
${PAYLOAD_RULES}
Return done=true only when nothing real remains for your area (fixFiles empty).`, M('checker', { label: `check:${key}:r${round}`, phase: phaseName, schema: CHECK_SCHEMA }, key))
    if (!result) break
    if (result.done || !result.fixFiles.length) { wroteAfterLastCheck = false; break }
    last = await writeFiles(result.fixFiles, `${key}-fix-r${round}`, phaseName, [key])
    wroteAfterLastCheck = true
  }
  if (wroteAfterLastCheck) {
    const v = await agent(`${COMMON}
You VERIFY (read-only) the final fixes for "${key}" (prefix "${prefix}"). Last write: ${JSON.stringify(last)}.
validate_property (plan true, maxIssues 200), save to ${DIR}/validation/${key}-final.json. Report ok=true only if the write succeeded and nothing real remains for "${prefix}" ids. List every open problem, including files that were NOT written (with their paths) so the review phase can pick them up. Do not generate payloads.`, M('verify', { label: `verify:${key}`, phase: phaseName, schema: VERIFY_SCHEMA, effort: 'low' }, key))
    const unwritten = (last && last.notWritten) || []
    return { ...(result || {}), done: !!(v && v.ok), issues: [...((result && result.issues) || []), ...((v && v.open) || [])], unwritten }
  }
  return result
}

// ---------------------------------------------------------------- Research (parallel with the shell)
phase('Research')
const researchPromises = {}
for (const a of AREAS) {
  if (SKIP.research || SKIP_AREAS.has(a.key) || !a.research) { researchPromises[a.key] = Promise.resolve(null); continue }
  researchPromises[a.key] = agent(`${COMMON}
You are the product researcher for area "${a.key}". Market: ${MARKET}. Style: ${STYLE}. Today is ${A.today}.
Research for this property's brief first. If a product you choose already has a module in the product cache ${PRODUCTS} (compare its PRODUCT dict), note it as kitModule and re-verify its page and price; a cached module is never a reason to pick a product.
Find real, currently sold products for: ${a.research}.
Area scope (for sizing; coordinates come from spec.json): ${a.scope}
For each product open the manufacturer's or retailer's PRODUCT page (WebSearch/WebFetch) and verify: exact name, retailer, URL (the product page), price, overall dimensions in metres (w, d, h), finish/colour, and the visible construction a 3D modeller needs (legs, arms, cushions, handles, panel style, materials, hex colour guesses). If a page cannot be verified, pick another product. Choose sizes that fit spec.json's rooms and drawn footprints.
Write JSON to ${DIR}/research/${a.key}.json: {"area":"${a.key}","researchDate":"${A.today}","products":[{"key","item","name","retailer","url","price","qty","dimensions_m":{"w","d","h"},"finish","construction","colours":{},"verified":true,"kitModule":"<cache module name> if one matches","notes"}],"gaps":[]}. Write no quote characters inside strings (write 76x80 in, not 76x80").
Do not write anything to PropertyPrompt. Return the file path, product count and gaps.`, M('research', { label: `research:${a.key}`, phase: 'Research', schema: RESEARCH_SCHEMA }, a.key))
}

// ---------------------------------------------------------------- Shell
phase('Shell')
const SHELL_RULES = `
SHELL RULES:
- Prefer the kit's shell generator in ${KIT}/shell/ (read its docstring/README): it builds walls, gaps, headers, sills, windows and doors from spec.json. Only hand-model what it does not cover.
- Full-height wall segments: role "wall", behavior {height:"ceiling",cutaway:"reduce",cutawayHeight:0.78,desktopCollision:true,vrCollision:true}, authored at ceiling height with centre y = ceiling/2, roots, no children, yaw only. Outer walls use 6-slot box materials (+X,-X,+Y,-Y,+Z,-Z) so the exterior face gets the exterior finish and the interior face m-paint.
- Openings are real gaps: wall segments stop at the jambs. Headers above openings: role "wall", behavior {height:"fixed",cutaway:"hide"}, collision true. Sill walls below windows: role "wall", behavior {height:"fixed",cutaway:"keep"}, collision true. A role "wall" node WITHOUT explicit behavior is stretched to the ceiling and seals the opening, so every shell part gets explicit behavior.
- Windows: frames, glass (m-glass, role glazing, cutaway hide, no collision, raycast false), interior stool. Doors: leaves role "door", drawn open on the hinge side in spec (openDeg, default 90 or the widest angle that clears the walls), in the style the brief and spec ask for, no collision; bifolds folded at the jambs so the opening stays usable.
- Interior walls end exactly at the faces of the walls they meet; no overlapping wall solids. Container "shell".
- Angled walls (spec.angledWalls; shell.py builds only walls along x or z): hand-model each as yaw-rotated wall segments with the same roles and behavior as above, gaps at its openings, headers and sills rotated with it, and meet the neighbouring walls without overlapping solids. The rooms they bound carry a spec "polygon"; the floor finishes clip to it.`

let shellCheck = null
const SHELL_OWNERS = ['shell', ...SHELL_PARTS.map(s => s.key)]
if (!SKIP.shell) {
  const shellModels = await parallel(SHELL_PARTS.map(s => () => agent(`${COMMON}
You are the ${s.key} modeller (id prefix "${s.prefix}"). Build ${s.scope}
${SHELL_RULES}
${PAYLOAD_RULES}
Generator: ${DIR}/gen/${s.key.replace(/-/g, '_')}.py. Payloads: ${DIR}/payloads/${s.key}/. Before returning, sanity-check in Python: gap positions vs spec, no wall overlaps, all ids prefixed. Return the ordered file list.`, M('shell-model', { label: `model:${s.key}`, phase: 'Shell', schema: MODEL_SCHEMA }))))

  for (let i = 0; i < SHELL_PARTS.length; i++) {
    const m = shellModels[i]
    if (!m) { log(`shell model ${SHELL_PARTS[i].key} failed`); continue }
    await writeFiles(m.files, SHELL_PARTS[i].key, 'Shell')
  }

  const SHELL_CHECK_SCHEMA = {
    type: 'object',
    properties: { matches: { type: 'boolean' }, issues: { type: 'array', items: { type: 'string' } }, fixFiles: { type: 'array', items: { type: 'string' } }, summary: { type: 'string' } },
    required: ['matches', 'issues', 'fixFiles', 'summary'],
  }
  let wroteFix = false
  let lastFixWrite = null
  for (let round = 1; round <= SHELL_ROUNDS; round++) {
    shellCheck = await queuedRender(() => agent(`${COMMON}
You are the SHELL CHECKER, round ${round}/${SHELL_ROUNDS}. Current revision ~ ${rev}.${lastFixWrite ? `\nLast fix write: ${JSON.stringify(lastFixWrite)}` : ''}
${pendingText(SHELL_OWNERS)}
1. validate_property (plan true, maxIssues 200), save to ${DIR}/validation/shell-r${round}.json. Every room accessPoint reachable; no sealed floor; doorways passable; no wall-wall intersections.
2. render_property: ONE call with view "plan" (cutaway true, 1600x1200), then ONE call with up to 4 cameras at cutaway false: two aerial views from opposite corners of the footprint (spec.json footprint; ~12 m up, ~6 m outside the corner, targeting the centre) and two eye-level interior views (1.6 m) from the entrance and from the far end of the main room. Save each returned image as ${DIR}/renders/shell-r${round}-<n>.jpg (where the tool result names a saved file, as "[Image: source: ...]" in Claude Code, cp that exact path).
3. Compare the plan render with the source plan wall by wall: footprint, each wall position/thickness, each opening position/width, door swing sides, bifolds, room extents. In 3D: holes, floating headers/sills, wrong heights, missing glass, flipped faces.
4. If something is wrong, write fix payloads with the shell generators (${DIR}/gen/) via write_payloads into ${DIR}/payloads/shell-fix-r${round}/ and list them in fixFiles. You are not the writer.
${SHELL_RULES}
${PAYLOAD_RULES}
Return matches=true only if the shell matches the source plan with no real problems.`, M('shell-check', { label: `check:shell:r${round}`, phase: 'Shell', schema: SHELL_CHECK_SCHEMA })))
    if (!shellCheck) break
    log(`shell check r${round}: matches=${shellCheck.matches}, ${shellCheck.issues.length} issues`)
    if (shellCheck.matches || !shellCheck.fixFiles.length) { wroteFix = false; break }
    lastFixWrite = await writeFiles(shellCheck.fixFiles, `shell-fix-r${round}`, 'Shell', SHELL_OWNERS)
    wroteFix = true
  }
  if (wroteFix) {
    // the last round's fixes were written without a check: verify, never leave them unchecked
    const v = await agent(`${COMMON}
VERIFY (read-only) the last shell fixes. Write result: ${JSON.stringify(lastFixWrite)}. validate_property (plan true), save to ${DIR}/validation/shell-final.json. ok=true only if the write succeeded and no shell problem remains; list open problems and any unwritten files.`, M('verify', { label: 'verify:shell', phase: 'Shell', schema: VERIFY_SCHEMA, effort: 'low' }))
    shellCheck = { ...shellCheck, matches: !!(v && v.ok), issues: [...shellCheck.issues, ...((v && v.open) || [])] }
  }
  if (!shellCheck || !shellCheck.matches) log('WARNING: the shell did not fully match after the check rounds; continuing, open issues are in the result.')
}

// ---------------------------------------------------------------- Areas
phase('Areas')
const lightNote = `Light budget: at most the number of real light sources stated for your area (point/spot), no shadows. Total interior+exterior budget is ~${LIGHT_BUDGET} so the viewer stays fast.`
const areaResults = await pipeline(
  AREAS.filter(a => !SKIP_AREAS.has(a.key)),
  async (a) => {
    const res = await researchPromises[a.key]
    return agent(`${COMMON}
You are the modeller for area "${a.key}" (id prefix "${a.prefix}"). Scope: ${a.scope}
${lightNote} Your area's light budget: ${a.lights || 0}.
Research: ${DIR}/research/${a.key}.json (${res ? res.count + ' products; gaps: ' + JSON.stringify(res.gaps) : 'no research result: verify real product pages yourself with WebSearch/WebFetch'}). Use those real products at their real dimensions, place each per spec.json (rooms, drawnItems, openings), and add one catalogue entry per product (modelKey "${a.prefix}<name>", room, item, name, retailer, price, qty, size, fit, status "Verified ${A.today}", url, dimensions [w,d,h]).
The shell is built (ids of the shell prefixes); read exact wall faces with get_scene_nodes if needed, but plan from spec.json.
${PAYLOAD_RULES}
Generator: ${DIR}/gen/${a.key}.py. Payloads: ${DIR}/payloads/${a.key}/ (ONE write_payloads call for the whole area so resources and nodes stay in one queue slot). Before returning, self-check in Python: ids prefixed "${a.prefix}", no part inside a wall (world AABBs vs spec walls), no two of your solid parts interpenetrating, items within their rooms, >= 0.75 m aisles to every door, nothing on a doorway or door swing. Return files in write order, summary, products, reused kit modules, new products worth extracting to the kit, assumptions (mark proposed additions).`, M('modeller', { label: `model:${a.key}`, phase: 'Areas', schema: MODEL_SCHEMA }, a.key))
  },
  async (m, a) => {
    if (!m) return null
    const w = await writeFiles(m.files, a.key, 'Areas')
    return { model: m, write: w }
  },
  async (mw, a) => {
    if (!mw) return { key: a.key, failed: true }
    const check = await checkLoop(a.key, a.prefix, a.scope, mw.write, 'Areas', MAX_CHECK)
    return { key: a.key, model: mw.model, check }
  },
)

// ---------------------------------------------------------------- Lighting, cameras, access points
phase('Lighting')
let lightModel = null
let lightCheck = null
let access = null
if (!SKIP.lighting) {
  const summaries = areaResults.filter(Boolean).map(r => ({ key: r.key, summary: r.model && r.model.summary, assumptions: r.model && r.model.assumptions, open: r.check && r.check.issues }))
  lightModel = await agent(`${COMMON}
You are the lighting & cameras agent (id prefix "lt-"). All areas are built; current revision ~ ${rev}.
Prepare payloads (one bundle, write_payloads into ${DIR}/payloads/lighting/):
1. Scene light nodes (roots, container "scene"): a directional sun (warm white, castShadow true, shadow {mapSize 2048, bias -0.0005, normalBias 0.02, near 0.5, far 60, extent ~ max footprint + 2}) high from the south-west targeting an empty target node at the footprint centre; a hemisphere sky light (sky #dfe9f5, ground #8a7d68).
2. settingsPatch (bundle key "settingsPatch"): defaultLighting false; appearance {simpleBackground, realisticBackground} (leave simpleExposure and realisticExposure at the server's defaults; the review changes them only when renders show the exposure is wrong); presets: dollhouse, plan and walk (walk from the entrance looking into the house) plus one per room at eye height 1.55-1.65 m from a doorway looking across the room, and an exterior kerb view. NOTE: extra presets are hidden when the property has an explicit floors list; if spec.json/rooms show floors, still author them but say so in modelNotes. labels per zone; description, subtitle, areaUnit, areaStats, researchDate "${A.today}", shoppingNotes per area, modelNotes (keep existing notes and append build notes/approximations). Read the current settings via get_property ONCE only if you must, otherwise from ${DIR}/rooms.json and spec.json.
Area summaries: ${JSON.stringify(summaries)}
${PAYLOAD_RULES}
Return the files (nodes first, then the update_settings file).`, M('lighting', { label: 'model:lighting', phase: 'Lighting', schema: MODEL_SCHEMA }))
  if (lightModel) {
    const lw = await writeFiles(lightModel.files, 'lighting', 'Lighting')
    lightCheck = await checkLoop('lighting', 'lt-', 'sun, sky, exposure, presets, labels and settings', lw, 'Lighting', LIGHT_ROUNDS)
  }

  // Access points are set AFTER furnishing: creation-time guesses end up under furniture.
  const accessModel = await agent(`${COMMON}
You are the ACCESS POINT agent. The property is furnished; current revision ~ ${rev}.
1. validate_property (plan true, maxIssues 200); save to ${DIR}/validation/access-points.json. Read the floor plan and reachability report.
2. Rooms: ${DIR}/rooms.json holds the complete rooms array used at creation (update it if it is stale: get_property once). For every room, choose an accessPoint [x, z] that is on open floor (no furniture, door leaf or fixture footprint within ~0.35 m; use get_scene_nodes summary true for placements), inside the room, and reachable from the entrance.
3. Write ${DIR}/rooms.json with the corrected array and a payload ${DIR}/payloads/access-points/01-update_settings.json = {"tool":"update_settings","args":{"settingsPatch":{},"rooms":[...complete array...]}} (settingsPatch is required alongside rooms; never "settings"). Strings without quote or backslash characters. You are not the writer.
Return files=[that path] and a summary listing each room's old and new accessPoint.`, M('access-points', { label: 'model:access-points', phase: 'Lighting', schema: MODEL_SCHEMA }))
  if (accessModel && accessModel.files.length) {
    const aw = await writeFiles(accessModel.files, 'access-points', 'Lighting')
    access = await checkLoop('access-points', 'lt-', 'room accessPoints only: every room reachable, no accessPoint under furniture', aw, 'Lighting', 2)
  }
}

return {
  finalRevision: rev,
  workDir: DIR,
  shell: shellCheck,
  areas: areaResults.map(r => r && ({ key: r.key, summary: r.model && r.model.summary, products: r.model && r.model.products, reused: r.model && r.model.reused, newModules: r.model && r.model.newModules, assumptions: r.model && r.model.assumptions, done: r.check && r.check.done, open: r.check && r.check.issues, unwritten: r.check && r.check.unwritten })),
  lighting: { summary: lightModel && lightModel.summary, open: lightCheck && lightCheck.issues, accessPoints: access },
  mainSessionWrites: [...deferred.values()],
  next: `${deferred.size ? 'Write mainSessionWrites from the main session first, then run' : 'Run'} workflow/review.js with startRevision from list_revisions. Then extract newModules into the product cache.`,
}
