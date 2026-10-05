"""Product modules added by kit users: one module per real product (see products/README.md).

discover() finds every module under products/ (any depth) that defines PRODUCT and build(), skipping
the template, tests and private modules, and returns {dotted name: module}:

    import sys; sys.path.insert(0, KIT)
    from products import discover
    for name, mod in discover().items():
        print(name, mod.PRODUCT['item'], mod.PRODUCT['name'], mod.PRODUCT.get('status'))
"""
import importlib
import os
import pkgutil

HERE = os.path.dirname(os.path.abspath(__file__))


def discover(include_template=False):
    found = {}
    for info in pkgutil.walk_packages([HERE], prefix=__name__ + '.'):
        leaf = info.name.rsplit('.', 1)[-1]
        if leaf.startswith('test_') or (leaf.startswith('_') and not (include_template and leaf == '_template')):
            continue
        mod = importlib.import_module(info.name)
        if hasattr(mod, 'PRODUCT') and callable(getattr(mod, 'build', None)):
            found[info.name] = mod
    return found
