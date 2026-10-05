"""Building-element generators (roof, siding, porch, landscape, plants, floor finishes, interior trim).

Each module takes a spec (spec.json format) plus parameters and returns bundles
{"resources", "products", "nodes"}; combine them with lib.payload.merge (or elements._common.merge
when a later bundle deliberately overrides an earlier one) and write them with
lib.payload.write_payloads. Elements carry no products of their own: catalogue entries come only
from researched product dicts the caller passes. tests/run_selftest.py builds every element on the
synthetic example spec and is the worked example. Standard library only.
"""
