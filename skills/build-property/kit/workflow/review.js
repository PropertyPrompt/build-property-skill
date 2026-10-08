export const meta = {
  name: 'property-review',
  description: 'Review a built PropertyPrompt property: pinned renders, three critics, owner fixers, queued writes, final validation',
  whenToUse: 'After workflow/build.js finishes (or to re-review a property). Needs a signed-in browser tab on the property for render_property.',
  phases: [
    { title: 'Render', detail: 'validate + pinned multi-camera captures' },
    { title: 'Critique', detail: 'accuracy, realism, geometry critics in parallel' },
    { title: 'Fix', detail: 'owner fixers, queued writes, check' },
    { title: 'Final', detail: 'final validation' },
  ],
}

// Required: propertyId, workDir, kitDir, planImage, brief, startRevision, areas
// Optional: reviewRounds (2), fixCheckRounds (2), cameras ([{view, position, target, cutaway}]), knownNonIssues ([...])
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
for (const k of ['propertyId', 'workDir', 'kitDir', 'planImage', 'brief', 'startRevision', 'areas']) {
  if (A[k] === undefined || A[k] === null) throw new Error(`args.${k} is required (see kit/templates/build.example.json)`)
}
if (/^(\/tmp|\/private\/tmp|\/var\/folders|\/private\/var\/folders)/.test(A.workDir) || A.workDir.indexOf('/scratchpad') >= 0) {
  throw new Error('args.workDir must be a durable directory, not a temp or scratchpad directory')
}
const PID = A.propertyId
const DIR = A.workDir
const KIT = A.kitDir
const PLAN = A.planImage
const PRODUCTS = A.productsDir || DIR.replace(/\/[^/]+\/?$/, '/products')
const AREAS = Array.isArray(A.areas) ? A.areas : (A.areas.areas || [])
const ROUNDS = A.reviewRounds || 2
const FIX_CHECKS = A.fixCheckRounds || 2
let rev = A.startRevision

let writeChain = Promise.resolve()
function queuedWrite(fn) { const p = writeChain.then(fn); writeChain = p.catch(() => {}); return p }

const OWNERS = { shell: 'walls, openings, windows, door leaves, bifolds (shell prefixes, e.g. so-*, si-*); generators gen/shell_*.py' }
for (const a of AREAS) OWNERS[a.key] = `${a.title || a.key} (ids ${a.prefix}*); gen/${a.key}.py`
OWNERS.lighting = 'sun/sky lights (ids lt-*) and settings: exposure, backgrounds, presets, labels, description, room accessPoints'

const COMMON = `
PropertyPrompt production MCP (tools named by their PropertyPrompt name, e.g. put_scene_nodes; in Claude Code they are mcp__PropertyPrompt__<name>, loaded with ToolSearch "select:<names>" before calling). Property id: ${PID}. The property is built.
Durable working directory: ${DIR} (never write build files to /tmp or a scratchpad).
- ${DIR}/spec.json: measured spec (walls, openings, rooms, drawn item footprints). Its coordinates are authoritative, including any ERRATA_READ_FIRST block.
- Source plan image: ${PLAN}. Build brief: ${A.brief}
- Generators in ${DIR}/gen/, written payloads in ${DIR}/payloads/<area>/, the write ledger in ${DIR}/queue-ledger.jsonl, research in ${DIR}/research/.
- Kit: ${KIT} (lib/payload.py, elements/). Product cache: ${PRODUCTS}.
- Avoid get_property (~130 KB): list_revisions for the revision, get_scene_nodes (summary true for a placement list; full nodes paginated by parentId) for details, ${DIR}/rooms.json for rooms.
${(A.knownNonIssues || []).length ? `Decided by the owner or settled in an earlier round; never report these or ask to undo them:\n${A.knownNonIssues.map(s => `- ${s}`).join('\n')}` : ''}
HARD RULE: do NOT call any PropertyPrompt write tool (put_scene_nodes, put_scene_resources, upsert_product, update_settings, remove_*, restore_revision, place_product) unless you are told you are THE WRITER. Reads are fine.
`

const PAYLOAD_RULES = `
FIX PAYLOADS:
- Prefer small targeted edits. Edit the generator in ${DIR}/gen/ (or write a small fix script there) and emit ONLY the changed nodes/resources through ${KIT}/lib/payload.py:
    import sys; sys.path.insert(0, "${KIT}/lib"); from payload import *
    write_payloads(bundle, "${DIR}/payloads/<owner>-review-r<N>/", external=[existing parent/resource/product ids])
  It strips quote/backslash characters, rounds to 3 dp, clamps torus arcs, validates against the server's schema (${DIR}/server-schema.json) and references, FAILS on coplanar layers < 2 mm apart, splits and orders files. Never bypass it.
- Change a node by replacing it under its existing id (reuse the old child ids for the new parts). Use bundle "removeNodes" only for parts that must disappear: the host may refuse removals.
- put_scene_nodes replaces transforms (omitted position/rotation/scale reset) and supplying render replaces it: send complete nodes, copying every field you are not changing (map get_scene_nodes object/userData/appearance output to input fields; never echo raw objects).
- Resources must ship in the same files/batch as the nodes that use them: the server sweeps unreferenced resources.
- Settings: bundle "settingsPatch" (and "rooms" when access points change; the update_settings call always carries settingsPatch, never "settings").
- Keep ids in the owner's prefix. Keep collision flags honest; never disable collision to hide a problem.
- A module in the product cache ${PRODUCTS} for the same researched product, or in ${KIT}/elements/, is a better replacement for a weak product model than ad-hoc tweaks.
`

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

// Batches stopped by a host-refused removal, at most one per owner, for the main session to write after
// the workflow. The owner's later agents fold a pending batch into any new fixes, so the owner's next
// batch supersedes it whatever happens: refused, it replaces it; otherwise it retires it, and whatever
// of the new batch did not land goes through the usual failed/notWritten path. None is replayed.
const deferred = new Map()
function pendingText(owner) {
  const p = deferred.get(owner)
  return p ? `PENDING for the main session (stopped at a removal the host refused; NOT landed): ${JSON.stringify(p)}. Do not regenerate it on its own. If you produce any fix files, regenerate its changes into them as well (its removals first), because your new batch replaces it.` : ''
}

async function writeFiles(files, label, owner) {
  if (!files || !files.length) return { finalRevision: rev, written: [], failed: [], notWritten: [] }
  return queuedWrite(async () => {
    const r = await agent(`You are THE WRITER for the PropertyPrompt write queue (batch "${label}"): the only agent allowed to write right now.
Property id: ${PID}. Starting expectedRevision: ${rev}.
Files, strictly in order (one uninterrupted slot; resources travel with their nodes):
${files.map((f, i) => `${i + 1}. ${f}`).join('\n')}
1. Make sure these PropertyPrompt tools are loaded: put_scene_resources, put_scene_nodes, upsert_product, remove_scene_node, remove_scene_resource, remove_product, update_settings, list_revisions, get_scene_nodes (in Claude Code: ToolSearch "select:mcp__PropertyPrompt__put_scene_resources,mcp__PropertyPrompt__put_scene_nodes,mcp__PropertyPrompt__upsert_product,mcp__PropertyPrompt__remove_scene_node,mcp__PropertyPrompt__remove_scene_resource,mcp__PropertyPrompt__remove_product,mcp__PropertyPrompt__update_settings,mcp__PropertyPrompt__list_revisions,mcp__PropertyPrompt__get_scene_nodes").
2. Resume check: grep ${DIR}/queue-ledger.jsonl for batch "${label}". If some files are recorded, skip recorded non-resource files but RESEND every put_scene_resources file of the batch; start from list_revisions.
3. For each file: cat it; it is {"tool":T,"args":A}. Call the PropertyPrompt tool T with A plus propertyId "${PID}" and expectedRevision = current revision. Copy A EXACTLY; make sure your tool input is valid JSON. Use the revision each call returns for the next, then append {"batch":"${label}","file":<path>,"revision":<rev>} as one line to ${DIR}/queue-ledger.jsonl with a shell command.
4. If your own input fails to parse, re-read and retry (max 2). On a revision conflict, list_revisions (check with get_scene_nodes whether the file already landed) and retry once. Never call get_property. If the host refuses a remove_* call (a permission denial, not a server error), do not retry: record that file under failed with an error starting "NEEDS_MAIN_SESSION", list the remaining files as notWritten, stop. On any server rejection (schema, intersection, navigation, missing reference) do NOT edit or retry: record the verbatim error (up to ~3000 chars incl. coordinates), list remaining files as notWritten, stop.
Return finalRevision, written, failed, notWritten.`, M('writer', { label: `write:${label}`, phase: 'Fix', schema: WRITE_SCHEMA, effort: 'low' }))
    if (r && typeof r.finalRevision === 'number' && r.finalRevision > rev) rev = r.finalRevision
    const refused = r ? r.failed.filter(f => /^NEEDS_MAIN_SESSION/.test(f.error)) : []
    deferred.delete(owner)
    if (refused.length) deferred.set(owner, { owner, batch: label, files: [...refused.map(f => f.file), ...r.notWritten] })
    log(`write ${label}: ${r ? r.written.length : 0} ok, ${r ? r.failed.length : '?'} failed, rev ${rev}`)
    return r || { finalRevision: rev, written: [], failed: [{ file: files[0], error: 'writer agent died' }], notWritten: files }
  })
}

const RENDER_SCHEMA = {
  type: 'object',
  properties: {
    revision: { type: 'integer' },
    images: { type: 'array', items: { type: 'object', properties: { path: { type: 'string' }, view: { type: 'string' } }, required: ['path', 'view'] } },
    problems: { type: 'array', items: { type: 'string' } },
  },
  required: ['revision', 'images', 'problems'],
}
const CRITIC_SCHEMA = {
  type: 'object',
  properties: {
    issues: { type: 'array', items: { type: 'object', properties: {
      owner: { type: 'string', enum: Object.keys(OWNERS) },
      severity: { type: 'string', enum: ['high', 'medium', 'low'] },
      description: { type: 'string' },
      evidence: { type: 'string' },
      fix: { type: 'string' },
    }, required: ['owner', 'severity', 'description', 'evidence', 'fix'] } },
    verdict: { type: 'string' },
  },
  required: ['issues', 'verdict'],
}
const FIX_SCHEMA = {
  type: 'object',
  properties: {
    files: { type: 'array', items: { type: 'string' } },
    fixed: { type: 'array', items: { type: 'string' } },
    rejected: { type: 'array', items: { type: 'string' }, description: 'issues judged not real, with reason' },
    deferred: { type: 'array', items: { type: 'string' }, description: 'real issues not fixed, with reason' },
  },
  required: ['files', 'fixed', 'rejected', 'deferred'],
}
const CHECK_SCHEMA = {
  type: 'object',
  properties: { ok: { type: 'boolean' }, fixFiles: { type: 'array', items: { type: 'string' } }, notes: { type: 'array', items: { type: 'string' } } },
  required: ['ok', 'fixFiles', 'notes'],
}

const CRITICS = [
  { key: 'accuracy', lens: 'ACCURACY AGAINST THE SOURCE PLAN: compare the plan and cutaway renders with the source plan wall by wall, opening by opening, fixture by fixture (spec.json drawnItems: beds, nightstands, tub, WC, vanity, kitchen runs, island, sofa, chairs, tables, laundry machines, closet units). Report wrong positions, sizes, orientation, missing or extra items, wrong door swing sides.' },
  { key: 'realism', lens: 'DOES IT LOOK LIKE A REAL HOUSE AND A SHOWCASE INTERIOR: exterior roof/siding/porch/landscaping credibility, material contrast (flat all-white rooms, bedding indistinguishable from the bed), lighting and exposure (too dark or blown out), product fidelity against research/*.json (do the sofa, beds, kitchen, appliances look like the real products?), styling, curtains, art, plants, rugs.' },
  { key: 'geometry', lens: 'FLOATING, OVERLAPPING OR CLIPPED GEOMETRY: items hovering above or sinking into the floor, poking through walls or each other, z-fighting/flicker (coplanar layers), gaps between roof and walls, holes in the shell, misaligned trim, curtains through furniture; read the validation file and investigate its possible_intersection and room-unreachable warnings.' },
]

const CAMERA_FILE = `${DIR}/review/cameras.json`
const cameraText = A.cameras
  ? `Use exactly these cameras (also write them to ${CAMERA_FILE}): ${JSON.stringify(A.cameras)}`
  : `Camera set: if ${CAMERA_FILE} exists, use it unchanged (so rounds compare like with like). Otherwise derive it from spec.json and save it there as [{"view","position","target","cutaway"}]:
  a) view "plan" (single call, cutaway true);
  b) cutaway true aerials from the four footprint corners: ~6 m outside each corner, ~13-14 m up, targeting the footprint centre at y 0;
  c) cutaway false exteriors: kerb view from the street side at 1.7 m (~10 m out, looking at the entrance), an entrance close-up, a rear aerial, the back/side door;
  d) cutaway false interiors at eye level 1.6 m: one per room (from the doorway or a corner, looking across to the far corner, targets at ~1 m), plus a view of the entrance from inside.`

const rounds = []
for (let round = 1; round <= ROUNDS; round++) {
  phase('Render')
  // the write queue is paused while capturing: every image is pinned to one revision
  const renderRes = await queuedWrite(() => agent(`${COMMON}
You are the RENDERER for review round ${round}. Current revision: ${rev}. The write queue is paused while you work.
1. validate_property (plan true, maxIssues 200); save the full output to ${DIR}/validation/review-r${round}.json.
2. ${cameraText}
3. render_property, ALWAYS with expectedRevision ${rev} (if it rejects because the revision moved, list_revisions and recapture everything at the new revision). width 1200, height 900, up to 4 cameras per call, cutaway calls separate from non-cutaway calls. Do not use settings.presets if the property has a floors list (they are hidden there); use cameras.
4. Save each image as ${DIR}/renders/review-r${round}-<view>.jpg (where the tool result names a saved file, as "[Image: source: ...]" in Claude Code, cp that exact path, one cp per image). Glance at them and note blank or broken captures; if render_property returns ok:false, follow its next field (usually: ask the owner to bring the signed-in property tab forward) and report it under problems.
Return the revision captured, the copied image paths with view names, and problems.`, M('renderer', { label: `render:r${round}`, phase: 'Render', schema: RENDER_SCHEMA })))
  if (!renderRes || !renderRes.images.length) { log('render agent failed or captured nothing; stopping review'); rounds.push({ round, renderProblems: renderRes ? renderRes.problems : ['render agent died'] }); break }

  phase('Critique')
  const prev = round > 1 ? `Previous round issues and what the fixers did: ${JSON.stringify(rounds[round - 2].summary)}. Check whether the fixed ones are resolved. Issues the fixers rejected or deferred, with their reasons, are settled: do not report them again unless your images show something new.` : ''
  const critiques = await parallel(CRITICS.map(c => () => agent(`${COMMON}
You are a CRITIC (${c.key}), review round ${round}. Lens: ${c.lens}
Images (revision ${renderRes.revision}): ${JSON.stringify(renderRes.images)}. Open and look at every image. Also read ${DIR}/validation/review-r${round}.json and the source plan ${PLAN}. ${prev}
Report only issues you can see evidence for (cite image and location). Assign each to its owner: ${JSON.stringify(Object.fromEntries(Object.entries(OWNERS).map(([k, v]) => [k, v.split(';')[0]])))}. Give a concrete fix. Severity: high = obviously wrong or broken (floating, through walls, wrong room, missing major item); medium = clearly visible quality problem; low = polish. Do not write anything.`, M('critic', { label: `critic:${c.key}:r${round}`, phase: 'Critique', schema: CRITIC_SCHEMA }, c.key))))
  const issues = critiques.filter(Boolean).flatMap(c => c.issues)
  const toFix = issues.filter(i => i.severity !== 'low' || round === 1)
  const dropped = issues.length - toFix.length
  log(`round ${round}: ${issues.length} issues, ${toFix.length} sent to owners${dropped ? `, ${dropped} low-severity left open` : ''}`)
  const byOwner = {}
  for (const i of toFix) (byOwner[i.owner] = byOwner[i.owner] || []).push(i)

  phase('Fix')
  const fixResults = await pipeline(
    Object.keys(byOwner),
    (owner) => agent(`${COMMON}
You are the FIXER for "${owner}" (${OWNERS[owner]}), review round ${round}.
Critic issues (renders in ${DIR}/renders/review-r${round}-*.jpg; validation in ${DIR}/validation/review-r${round}.json):
${JSON.stringify(byOwner[owner], null, 1)}
Critics work independently, so several entries can describe one problem: treat those as one issue, using all of their evidence, and list it once in fixed, rejected or deferred.
${pendingText(owner)}
Verify each issue first (open the cited image, read the nodes). Reject issues that are not real, with a reason. For real ones write fix payloads into ${DIR}/payloads/${owner}-review-r${round}/ and list them in files, in write order. Defer (with reason) anything that would need a large rebuild.
${PAYLOAD_RULES}`, M('fixer', { label: `fix:${owner}:r${round}`, phase: 'Fix', schema: FIX_SCHEMA }, owner)),
    async (fx, owner) => {
      if (!fx || !fx.files.length) return { owner, fx, write: null, check: null, unwritten: [] }
      let w = await writeFiles(fx.files, `${owner}-review-r${round}`, owner)
      let check = null
      // every round's corrective files are written; the loop ends on a check, never on unwritten fixes
      for (let k = 1; k <= FIX_CHECKS; k++) {
        const last = k === FIX_CHECKS
        check = await agent(`${COMMON}
You are the CHECKER for "${owner}" (${OWNERS[owner]}) after review-round-${round} fixes (check ${k}/${FIX_CHECKS}${last ? ', the LAST one: report only, generate no payloads' : ''}).
Write result: ${JSON.stringify(w)}
Fixes intended: ${JSON.stringify(fx.fixed)}
${pendingText(owner)}
1. If the write stopped at a removal the host refused (NEEDS_MAIN_SESSION), that batch is PENDING above: do not regenerate it; go to step 2. If it failed otherwise, read the verbatim error${last ? ' and report it with the unwritten file paths in notes' : ` and produce corrected payloads (new folder ${DIR}/payloads/${owner}-review-r${round}-c${k}/) including every not-written file still needed and the resource files those nodes use`}.
2. Otherwise validate_property (plan true, maxIssues 200): confirm 0 errors, every room reachable, and no new issue involving your ids.${last ? ' Report regressions in notes.' : ' If something regressed, produce corrective payloads in that folder.'}
Return ok=true with empty fixFiles when everything is fine.
${last ? '' : PAYLOAD_RULES}`, M('checker', { label: `check:${owner}:r${round}:${k}`, phase: 'Fix', schema: CHECK_SCHEMA }, owner))
        if (!check || check.ok || !check.fixFiles.length || last) break
        w = await writeFiles(check.fixFiles, `${owner}-review-r${round}-c${k}`, owner)
      }
      return { owner, fx, write: w, check, unwritten: (w && w.notWritten) || [] }
    },
  )
  rounds.push({
    round,
    revision: renderRes.revision,
    renderProblems: renderRes.problems,
    verdicts: critiques.filter(Boolean).map(c => c.verdict),
    issues,
    lowLeftOpen: issues.filter(i => !toFix.includes(i)),
    summary: fixResults.filter(Boolean).map(f => ({ owner: f.owner, fixed: f.fx && f.fx.fixed, rejected: f.fx && f.fx.rejected, deferred: f.fx && f.fx.deferred, writeFailed: f.write && f.write.failed, unwritten: f.unwritten, checkOk: f.check && f.check.ok, checkNotes: f.check && f.check.notes })),
  })
  if (!toFix.length) break
}

phase('Final')
const final = await queuedWrite(() => agent(`${COMMON}
Final check. validate_property (plan true, maxIssues 200) on the current revision; save to ${DIR}/validation/final.json. Report: revision, error count, warnings by code with details, whether every room is reachable, and anything unresolved. Do not write.`, M('final', { label: 'final-validate', phase: 'Final', schema: { type: 'object', properties: { revision: { type: 'integer' }, errors: { type: 'integer' }, allRoomsReachable: { type: 'boolean' }, warnings: { type: 'array', items: { type: 'string' } }, unresolved: { type: 'array', items: { type: 'string' } } }, required: ['revision', 'errors', 'allRoomsReachable', 'warnings', 'unresolved'] } })))

return {
  finalRevision: rev,
  rounds,
  final,
  mainSessionWrites: [...deferred.values()],
  notChecked: ['Interactive walkthrough at standing height in every room', 'VR test'],
}
