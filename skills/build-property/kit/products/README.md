# Products

One Python module per real product, so a product researched and modelled once can be placed again
when a later build chooses the same product. The kit holds only the scaffolding:

| File | What |
|---|---|
| `_template.py` | a fully commented, runnable module for a made-up side table: copy it |
| `test_template.py` | its test, and the pattern for a new module's test |
| `__init__.py` | `discover()` and `load()`: the modules in your product cache that define `PRODUCT` and `build()` |

Your modules live in a **product cache** outside the kit: `$PROPERTY_BUILD_PRODUCTS`, else
`<work root>/products` (normally `~/property-build-work/products`; `productsDir` in `build-args.json`).
The kit stays a generic toolkit rather than a furniture catalogue, and a cached module is a shortcut
for modelling, never a reason to choose a product: each build researches its own products for its
brief, and reuses a module only for the product research picked.

Put modules in `<cache>/<room>/<product_name>.py` or directly in the cache. Module and key names
describe the product (`oak_dining_table`), not the property it was first used in. Load one by its
dotted path: `from products import load; mod = load('living.oak_dining_table')`.

## 1. Research the product

Every product is a real, currently sold item, checked on the page that sells it:

- **Product page.** The manufacturer's or a retailer's page for this exact item and variant. Not a
  search result, category page, marketplace listing without dimensions, or an image found elsewhere.
- **Price** as listed, with the currency, and the date you checked it.
- **Dimensions** from the page or its spec sheet (width, depth, height; seat height, arm height,
  leg height, mattress thickness where they matter), converted to metres.
- **Finish** (colour, material, variant name) and **construction** (what it is made of, how the parts
  meet: tapered legs, plinth base, slatted back, open shelves).
- Photos from the page for the silhouette: count the legs, panels, drawers and handles.

If a figure is missing, say so in `notes` and model a conservative estimate; never invent a price or
a URL. Re-verify the page and price whenever a module is reused in a new property.

## 2. Model it

- **Units and origin.** Metres. The origin is the item's floor centre (y = 0 on the floor), +z is its
  front (the side you use it from), so `rotation_y` turns it about its own footprint.
- **Recognisable silhouette, multipart.** A sofa is a plinth, seat cushions, back cushions, arms and
  legs, not one rounded box. Use shaped primitives (`rounded_box`, `cylinder`, `extrude`, `tube`) and
  keep part counts modest.
- **Shared geometry.** Geometry ids are `<prefix>g-<product key>-<part>`, the same for every instance
  under one prefix; repeated parts (legs, slats, handles) are one node with `render.instances`.
- **Honest collision.** Furniture you walk around collides (`SOLID`); beds, sofas and chairs block VR
  but not the desktop camera (`SEAT`); rugs, cushions, art and small decor never collide (`DECOR`).
- **Layer separation.** Thin parts lying on each other (rug on floor, cushion on seat, art on canvas,
  door panel on frame) keep same-facing faces at least 2-3 mm apart after rounding to 3 decimals.
  `write_payloads` fails below 2 mm; generate 3 mm.
- **Materials.** Use the shared `m-*` library (`lib/materials.json`) where it fits; otherwise define
  materials with the prefix. Colour variation comes from materials, not uploaded textures.
- **Strings.** ASCII, no double quotes or backslashes (`76x80 in`, not `76x80"`); `lib.payload`
  cleans them, but write them clean.

## 3. The module contract

```python
PRODUCT = {"key", "item", "name", "retailer", "url", "price", "currency", "dimensions_m": [w, d, h],
           "size", "finish", "construction", "researched", "status", "notes"}

def build(prefix, inst_id, position, rotation_y=0.0, room_id=None, materials=None, qty=1, **options) -> bundle
```

- Returns a bundle `{"resources": [...], "products": [...], "nodes": [...]}` (`lib.payload.bundle`).
- The root node is `f"{prefix}{inst_id}"` at `position`, `container: "furnishings"`, with
  `productKey`/`catalogueKey` = `prefix + PRODUCT["key"]`; parts are its children in local coordinates.
  Every id starts with `prefix`.
- `materials` maps a role to an existing material id (used as-is) or a dict merged into the default.
- `**options` are real variants of the product (size, colour, with or without a shelf).
- The module emits one catalogue entry in the `upsert_product` shape: `modelKey, room, item, name,
  retailer, price, qty, size, fit, status, url, dimensions [w, d, h]`.
- **qty convention.** Identical catalogue entries merge into one and quantities are not summed. Pass
  the total on every call (two chairs: `qty=2` on both), or the total on the first call and `qty=0`
  on the rest (no catalogue entry).

## 4. Test it

Copy `test_template.py` to `test_<product>.py` next to the module and change the import to
`import <product_name> as product`. It builds
two instances, merges them with `lib.payload.merge`, and asserts that geometry and the catalogue
entry are shared, every id is prefixed, `validate` reports no errors, `check_layers` finds no
z-fighting, and `write_payloads` writes queue files to a temporary directory. Add checks for the
product's own key dimensions (top height, seat height, overall footprint). Run it with
`PROPERTY_BUILD_KIT=$KIT python3 <cache>/<room>/test_<product_name>.py`.
