"""A small JSON Schema (2020-12 subset) validator, stdlib only.

It covers measure/spec.schema.json and the schemas PropertyPrompt's get_schema returns, which are
generated from the server's own types. With optional_defaults=True a property that declares a
default is not treated as required: get_schema describes the server's parsed output, where every
defaulted field is present, while payloads may omit them.
"""
import re


class Validator:
    def __init__(self, schema, optional_defaults=False):
        self.root = schema
        self.optional_defaults = optional_defaults
        self.errors = []

    def ref(self, r):
        node = self.root
        for part in r.lstrip('#/').split('/'):
            node = node[part]
        return node

    @staticmethod
    def is_type(v, t):
        return {'object': lambda: isinstance(v, dict), 'array': lambda: isinstance(v, list),
                'string': lambda: isinstance(v, str), 'boolean': lambda: isinstance(v, bool),
                'null': lambda: v is None,
                'integer': lambda: isinstance(v, int) and not isinstance(v, bool),
                'number': lambda: isinstance(v, (int, float)) and not isinstance(v, bool)}[t]()

    def errors_for(self, v, s, path=''):
        saved = self.errors
        self.errors = []
        self.check(v, s, path)
        found = self.errors
        self.errors = saved
        return found

    def ok(self, v, s):
        return not self.errors_for(v, s)

    def _alternatives(self, v, branches, path):
        if any(self.ok(v, b) for b in branches):
            return
        # Report the closest alternative: one whose const fields (e.g. "type") match, else the one
        # with the fewest errors.
        if isinstance(v, dict):
            tagged = [b for b in branches if all(v.get(k) == p['const'] for k, p in b.get('properties', {}).items()
                                                 if isinstance(p, dict) and 'const' in p)]
            if len(tagged) == 1:
                self.check(v, tagged[0], path)
                return
            if not tagged:
                consts = sorted({str(p['const']) for b in branches for p in b.get('properties', {}).values()
                                 if isinstance(p, dict) and 'const' in p})
                if consts:
                    self.errors.append('%s: no alternative matches (expected one of %s)' % (path or '/', ', '.join(consts)))
                    return
        self.errors.extend(min((self.errors_for(v, b, path) for b in branches), key=len))

    def check(self, v, s, path):
        if '$ref' in s:
            self.check(v, self.ref(s['$ref']), path)
        if 'type' in s:
            ts = s['type'] if isinstance(s['type'], list) else [s['type']]
            if not any(self.is_type(v, t) for t in ts):
                self.errors.append('%s: expected %s, got %s' % (path or '/', '|'.join(ts), type(v).__name__))
                return
        if 'enum' in s and v not in s['enum']:
            self.errors.append('%s: %r not one of %s' % (path, v, s['enum']))
        if 'const' in s and v != s['const']:
            self.errors.append('%s: must be %r' % (path, s['const']))
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if 'exclusiveMinimum' in s and not v > s['exclusiveMinimum']:
                self.errors.append('%s: %s must be > %s' % (path, v, s['exclusiveMinimum']))
            if 'exclusiveMaximum' in s and not v < s['exclusiveMaximum']:
                self.errors.append('%s: %s must be < %s' % (path, v, s['exclusiveMaximum']))
            if 'minimum' in s and v < s['minimum']:
                self.errors.append('%s: %s must be >= %s' % (path, v, s['minimum']))
            if 'maximum' in s and v > s['maximum']:
                self.errors.append('%s: %s must be <= %s' % (path, v, s['maximum']))
        if isinstance(v, str):
            if 'pattern' in s and not re.search(s['pattern'], v):
                self.errors.append('%s: %r does not match %s' % (path, v, s['pattern']))
            if 'minLength' in s and len(v) < s['minLength']:
                self.errors.append('%s: needs >= %d characters' % (path, s['minLength']))
            if 'maxLength' in s and len(v) > s['maxLength']:
                self.errors.append('%s: needs <= %d characters' % (path, s['maxLength']))
        if isinstance(v, list):
            if 'minItems' in s and len(v) < s['minItems']:
                self.errors.append('%s: needs >= %d items' % (path, s['minItems']))
            if 'maxItems' in s and len(v) > s['maxItems']:
                self.errors.append('%s: needs <= %d items' % (path, s['maxItems']))
            if isinstance(s.get('items'), dict):
                for i, x in enumerate(v):
                    self.check(x, s['items'], '%s/%d' % (path, i))
        if isinstance(v, dict):
            props = s.get('properties', {})
            for k in s.get('required', []):
                if k not in v and not (self.optional_defaults and 'default' in props.get(k, {})):
                    self.errors.append('%s: missing required "%s"' % (path or '/', k))
            for k, x in v.items():
                if 'propertyNames' in s:
                    self.check(k, s['propertyNames'], '%s/<key %s>' % (path, k))
                if k in props:
                    self.check(x, props[k], '%s/%s' % (path, k))
                elif isinstance(s.get('additionalProperties'), dict):
                    self.check(x, s['additionalProperties'], '%s/%s' % (path, k))
                elif s.get('additionalProperties') is False:
                    self.errors.append('%s: unexpected key "%s"' % (path or '/', k))
        for sub in s.get('allOf', []):
            self.check(v, sub, path)
        for key in ('anyOf', 'oneOf'):
            if key in s:
                self._alternatives(v, s[key], path)
        if 'if' in s:
            if self.ok(v, s['if']):
                if 'then' in s:
                    self.check(v, s['then'], path)
            elif 'else' in s:
                self.check(v, s['else'], path)
