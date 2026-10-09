# Shell generator

`shell.py` turns a measured `spec.json` into queue payloads for the building shell: outer walls,
interior walls, openings, headers, sills, windows and doors. It has no house-specific code; a
new property needs only its spec.

```sh
# 1. measure the plan (draft; fix its "review" list, add rooms, rename ids)
python3 ../measure/measure_plan.py plan.png --width-ft 36 --depth-ft 26 \
    --out spec.json --debug-png overlay.png
python3 ../measure/compare_spec.py spec.json reference-spec.json   # optional: check against a hand spec

# 2. build the shell payloads
python3 shell.py spec.json --out payloads/shell [--prefix-outer so- --prefix-inner si-] \
    [--only outer|inner] [--bifold-max-projection 0.15] [--emit-glass-material]

# 3. optional: compare with an earlier build, node by node
python3 compare_payloads.py payloads/shell --ref old/shell-outer old/shell-inner old/shell-fixes
```

`measure/spec.schema.json` describes the format. `measure/spec.example.json` is a small synthetic
example house (10 m x 8 m, two bedrooms) used by the kit's self-test.

## Inputs

| spec key | used for |
|---|---|
| `ceilingHeight` | full-height walls and the top of every header |
| `wallThickness.outer` | (outer walls use `abs(inner - line_outer)` per wall) |
| `wallThickness.interior` | interior walls without their own `thickness` |
| `heights.door` | door head when an opening has no `head`; leaf height = head - 2 mm (outer), min(2.025, head - 7 mm) (interior) |
| `heights.windowSill`, `heights.windowHead` | window sill/head when an opening has no `sill`/`head` |
| `outerWalls[]` | `axis`, `line_*_outer`, `inner_*`, `from`, `to`, `openings` |
| `interiorWalls[]` | `axis`, `center_*`, `from`, `to`, `thickness`, `openings` |
| `rooms[].bounds` | which side of a wall a door/bifold opens to when it names a room |
| `interiorDoors` | defaults for every interior hinged door: `leafStyle` (`shaker` or `slab`), `leafMaterial` (`m-trim`), `hardwareMaterial` (`m-brass`); an opening's own keys win |
| `angledWalls[]` | not built here: walls at an angle are hand-modelled (see Limitations) |

Opening types: `window`, `door`, `bifold`, `opening` (header only, no leaf; interior walls).

## Outputs

One bundle (`build_shell(spec)` returns it; the CLI writes it with `lib.payload.write_payloads`,
which rounds to 3 decimals, validates and checks coplanar layers). Ids, with the default prefixes:

| ids | what | role / behaviour |
|---|---|---|
| `so-w-<wall>-<n>` | outer wall pieces between openings, 6 materials `+X,-X,+Y,-Y,+Z,-Z`: local `-Z` exterior `m-siding`, `+Z` interior `m-paint`, ends `m-paint` (reveals) or `m-siding` (building corners) | wall; ceiling height, cutaway reduce, collides |
| `so-hd-<opening>` | header from head to ceiling | wall; fixed, cutaway hide, collides |
| `so-sl-<window>` | sill wall from floor to sill | wall; fixed, cutaway keep, collides |
| `so-win-<window>` (+ `-fr`, `-gl`, `-st`) | black extruded frame with mullions (one sash per started metre, two panes high), instanced glass panes (`m-glass`, glazing, `raycast:false`, `castShadow:false`), interior stool (`m-trim`, 40 mm proud) | glazing hidden in cutaway, stool kept |
| `so-door-<name>` (+ `-lf -gl -mv -mh -pn -rs -lv -nk -hb`) | exterior door leaf drawn open: half-glazed with 2x2 muntins, raised lower panels, black rosettes/levers/hinges; `so-th-<name>` threshold | door; no collision |
| `si-w-<wall>[-n]` | interior partition segments (`m-paint`) | wall; ceiling, cutaway reduce, collides |
| `si-h-<opening>` | interior headers | wall; fixed, cutaway hide, collides |
| `si-d-<name>` (+ `-frame [-panels] -rose -stem -knob`) | two-panel shaker leaf or flat slab (`leafStyle`, `leafMaterial`, default `m-trim`), knobs (`hardwareMaterial`, default `m-brass`), drawn open | door; no collision, `roomId` set |
| `si-b-<name>` | bifold: two pairs of panels folded from the jambs into the room (instanced) | door; no collision |

Pieces that meet end to end overlap by `JOINT_OV` (2 mm), because boxes that only touch leave
hairline cracks: z-axis outer walls run into the x-axis walls at corners, partitions run into the
solid wall they meet (not where it has an opening), and headers and sill walls run into the wall
pieces beside them. The server does not report wall-wall overlaps as intersections.

`<name>` is the opening id without its `door-` / `bifold-` prefix. Materials are the shared `m-*`
library (`lib/materials.json`); publish it first. `--emit-glass-material` adds `m-glass` with
default values (roughness 0.15, metalness 0, envMapIntensity 0.3).

## Geometry rules and assumptions

- Units are metres, y up, floor top y = 0. Footprint and every wall are axis-aligned.
- Corners belong to the x-axis (north/south) outer walls; z-axis walls stop at their inner faces.
- Openings are real gaps: wall pieces run between openings; headers fill head to ceiling; windows
  also get a sill wall from 0 to the sill.
- Interior wall ends snap to the face of whatever they meet (an outer inner face, or the near face
  of a perpendicular interior wall within 1 cm of its band), so `from`/`to` may be given at the
  face or the centreline.
- Windows: frame 50 mm members, 70 mm deep, centred in the wall; glass 6 mm; the stool runs from
  the frame's inner face to 40 mm proud of the interior face.
- Outer doors: leaf = opening - 10 mm, 45 mm thick, 8 mm above the floor. An inward leaf hangs
  19 mm inside the inner face; an outward one on the outer face line. Leaf/threshold materials:
  `entrance: true` or an id containing `front` gets walnut + oak, others `m-trim` + `m-steel`
  (override with `leafMaterial`, `thresholdMaterial`).
- Interior doors: leaf = opening - 10 mm (760 mm when within 5 mm of it), 35 mm thick, hung
  3 mm off the hinge jamb and 5 mm proud of the wall face on the swing side. Style and materials
  come from the opening, else `spec.interiorDoors`, else a shaker leaf in `m-trim` with brass knobs;
  set them from the interior brief.
- Open angle: `openDeg` if given; otherwise outer doors 90 deg and interior doors the widest angle
  <= 90 deg whose leaf and knobs clear every wall (rectangle test, 2 mm clearance; furniture is not
  considered, so set `openDeg` when a leaf would hit a fitted unit).
- Bifolds: four panels of `(width - 12 mm) / 4`, 25 mm thick, 2.0 m tall, 15 mm off the floor,
  folded `foldDeg` out of the wall plane. Default: the largest whole angle (max 45 deg) at which a
  panel reaches no more than `--bifold-max-projection` (0.15 m) into the room. A steep fold blocks
  a closet when furniture stands close to it and a shallow one makes it look shut; 0.15 m is about
  31 deg for 0.29 m panels.

## How hinge and swing are read

Structured fields win; the free-text `swing`/`note` strings are the fallback (the format
measure_plan.py writes).

| question | structured | text fallback |
|---|---|---|
| hinge jamb | `hinge: "from"` (west or north jamb) / `"to"` (east or south jamb) | `hinge on east jamb`, `hinge at the south jamb` (on/at, optional "the"); the compass word must lie along the wall |
| outer door side | `swingSide: "interior"` / `"exterior"` | `inward` / `outward` anywhere in `swing` |
| interior door/bifold side | `swingSide: "+z"/"-z"/"+x"/"-x"` or `north/south/east/west` | `room` (its bounds' centre vs the wall line), else a room id or name found in `swing`/`note` (`into bedroom 2`), else a compass word after into/facing/to the/opening |

The leaf is drawn from the hinge jamb, swung `openDeg` from the closed position toward the swing
side; `roomId` is the resolved room. If anything cannot be resolved the generator stops with the
opening id rather than guessing.

## Verification

`python3 tests/run_selftest.py` (from the kit root) builds the shell of `measure/spec.example.json`
together with every element module, and validates the result with `lib.payload` (ids, parents,
references, layer separation, file splitting). `compare_payloads.py` compares generated payloads
with an earlier build of the same property, node by node, when you change the generator.

## Limitations

- One storey, walls along x or z only; no curved walls. Walls at an angle (a cut corner, a bay)
  go in `spec.angledWalls` (start and end points, thickness, openings measured along the wall)
  and the shell modeller hand-models them as yaw-rotated pieces with the same roles and behaviour
  as the table above. The rooms they bound take a `polygon`, which `elements/floor_finish.py` and
  the ceilings clip to.
- Windows are always two panes high with one sash per started metre; no transoms, sliders or
  picture windows. The exterior door style is fixed (half-glazed); interior leaves are shaker or slab.
- No sliding, pocket or double doors; `opening` gives a cased gap with a header only.
- The door-clearance test ignores furniture and other leaves.
- Exterior dressing (porch, roof, siding battens, trim) belongs to `elements/`, floors elsewhere.
