"""Product modules: one module per real product (see products/README.md).

The kit ships only the template. Modules you model live in your product cache, outside the kit:
$PROPERTY_BUILD_PRODUCTS, else <work root>/products ($PROPERTY_BUILD_WORK_ROOT or ~/property-build-work).
That keeps the kit generic, and a cached module is a starting point whose price and page are re-checked
on every reuse, never a catalogue to pick from.

discover() returns {name: module} for every cache module that defines PRODUCT and build(), skipping
tests and private modules; names are paths relative to the cache, dotted (living.oak_side_table):

    import sys; sys.path.insert(0, KIT)
    from products import discover, load
    for name, mod in discover().items():
        print(name, mod.PRODUCT['item'], mod.PRODUCT['name'], mod.PRODUCT.get('status'))
    table = load('living.oak_side_table')
"""
import glob
import importlib
import importlib.util
import os
import sys


def cache_dir():
    root = os.environ.get('PROPERTY_BUILD_WORK_ROOT') or '~/property-build-work'
    return os.path.abspath(os.path.expanduser(os.environ.get('PROPERTY_BUILD_PRODUCTS') or os.path.join(root, 'products')))


def discover(root=None, include_template=False):
    found = {}
    if include_template:
        found['_template'] = importlib.import_module(__name__ + '._template')
    root = os.path.abspath(os.path.expanduser(root or cache_dir()))
    for path in sorted(glob.glob(os.path.join(root, '**', '*.py'), recursive=True)):
        leaf = os.path.basename(path)[:-3]
        if leaf.startswith('test_') or leaf.startswith('_'):
            continue
        name = os.path.relpath(path, root)[:-3].replace(os.sep, '.')
        mod_name = 'product_cache.' + name
        spec = importlib.util.spec_from_file_location(mod_name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        if hasattr(mod, 'PRODUCT') and callable(getattr(mod, 'build', None)):
            found[name] = mod
    return found


def load(name, root=None):
    mods = discover(root)
    if name not in mods:
        raise KeyError('no product module %r in %s' % (name, root or cache_dir()))
    return mods[name]
