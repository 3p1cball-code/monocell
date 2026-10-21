"""Safe expression evaluator shared by Calculator and Sheets (no eval())."""
import math
import random
import re
import time

TOKEN = re.compile(r'''\s*(?:
    (?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?) |
    (?P<str>"(?:[^"]|"")*") |
    (?P<ref>\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?) |
    (?P<name>[A-Za-z_][A-Za-z_0-9.]*) |
    (?P<op><=|>=|<>|!=|==|\*\*|[-+*/^%&=<>(),;!])
)''', re.X)


class FormulaError(Exception):
    pass


def col_index(letters):
    n = 0
    for c in letters.upper():
        n = n * 26 + ord(c) - 64
    return n - 1


def col_name(i):
    s = ''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def parse_ref(ref):
    m = re.fullmatch(r'\$?([A-Za-z]{1,3})\$?(\d+)', ref)
    if not m:
        raise FormulaError('bad ref ' + ref)
    return int(m.group(2)) - 1, col_index(m.group(1))


def tokenize(s):
    pos, out = 0, []
    s = s.rstrip()
    while pos < len(s):
        m = TOKEN.match(s, pos)
        if not m or m.end() == pos:
            raise FormulaError('syntax error at "%s"' % s[pos:pos + 8])
        pos = m.end()
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
    out.append(('end', None))
    return out


def _num(v):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return v
    if v is None or v == '':
        return 0
    if isinstance(v, str):
        try:
            return float(v.replace(',', '.')) if v.strip() else 0
        except ValueError:
            raise FormulaError('not a number: %s' % v)
    if isinstance(v, list):
        raise FormulaError('range used as value')
    return v


def _flat(args):
    for a in args:
        if isinstance(a, list):
            for x in a:
                yield x
        else:
            yield a


def _nums(args):
    return [_num(x) for x in _flat(args) if x not in (None, '') and not (isinstance(x, str) and not _isnum(x))]


def _isnum(s):
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


def _fact(x):
    x = int(_num(x))
    if x < 0 or x > 170:
        raise FormulaError('factorial range')
    return math.factorial(x)


FUNCS = {
    'SUM': lambda *a: sum(_nums(a)),
    'AVERAGE': lambda *a: (sum(_nums(a)) / len(_nums(a))) if _nums(a) else 0,
    'AVG': lambda *a: (sum(_nums(a)) / len(_nums(a))) if _nums(a) else 0,
    'MEAN': lambda *a: (sum(_nums(a)) / len(_nums(a))) if _nums(a) else 0,
    'MIN': lambda *a: min(_nums(a)) if _nums(a) else 0,
    'MAX': lambda *a: max(_nums(a)) if _nums(a) else 0,
    'COUNT': lambda *a: len(_nums(a)),
    'COUNTA': lambda *a: len([x for x in _flat(a) if x not in (None, '')]),
    'PRODUCT': lambda *a: math.prod(_nums(a)),
    'MEDIAN': lambda *a: _median(_nums(a)),
    'ABS': lambda x: abs(_num(x)),
    'ROUND': lambda x, n=0: round(_num(x), int(_num(n))),
    'FLOOR': lambda x: math.floor(_num(x)),
    'CEIL': lambda x: math.ceil(_num(x)),
    'CEILING': lambda x: math.ceil(_num(x)),
    'INT': lambda x: int(math.floor(_num(x))),
    'SQRT': lambda x: math.sqrt(_num(x)),
    'POW': lambda x, y: _num(x) ** _num(y),
    'POWER': lambda x, y: _num(x) ** _num(y),
    'MOD': lambda x, y: _num(x) % _num(y),
    'EXP': lambda x: math.exp(_num(x)),
    'LN': lambda x: math.log(_num(x)),
    'LOG': lambda x, b=10: math.log(_num(x), _num(b)),
    'LOG10': lambda x: math.log10(_num(x)),
    'LOG2': lambda x: math.log2(_num(x)),
    'SIN': lambda x: math.sin(_num(x)),
    'COS': lambda x: math.cos(_num(x)),
    'TAN': lambda x: math.tan(_num(x)),
    'ASIN': lambda x: math.asin(_num(x)),
    'ACOS': lambda x: math.acos(_num(x)),
    'ATAN': lambda x: math.atan(_num(x)),
    'DEG': lambda x: math.degrees(_num(x)),
    'RAD': lambda x: math.radians(_num(x)),
    'FACT': _fact,
    'RAND': lambda: random.random(),
    'PI': lambda: math.pi,
    'IF': lambda c, a=True, b=False: a if _truthy(c) else b,
    'AND': lambda *a: all(_truthy(x) for x in _flat(a)),
    'OR': lambda *a: any(_truthy(x) for x in _flat(a)),
    'NOT': lambda x: not _truthy(x),
    'LEN': lambda s: len(_str(s)),
    'UPPER': lambda s: _str(s).upper(),
    'LOWER': lambda s: _str(s).lower(),
    'CONCAT': lambda *a: ''.join(_str(x) for x in _flat(a)),
    'LEFT': lambda s, n=1: _str(s)[:int(_num(n))],
    'RIGHT': lambda s, n=1: _str(s)[-int(_num(n)):] if int(_num(n)) else '',
    'NOW': lambda: time.strftime('%Y-%m-%d %H:%M'),
    'TODAY': lambda: time.strftime('%Y-%m-%d'),
}


def _median(xs):
    if not xs:
        return 0
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _truthy(v):
    if isinstance(v, str):
        return v.upper() not in ('', 'FALSE', '0')
    return bool(v)


def _str(v):
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, bool):
        return 'TRUE' if v else 'FALSE'
    return '' if v is None else str(v)


class Parser:
    def __init__(self, text, ref=None, rng=None, names=None):
        self.toks = tokenize(text)
        self.i = 0
        self.ref = ref
        self.rng = rng
        self.names = names or {}

    def peek(self):
        return self.toks[self.i]

    def take(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, v):
        t = self.take()
        if t[1] != v:
            raise FormulaError('expected %s' % v)

    def parse(self):
        v = self.cmp()
        if self.peek()[0] != 'end':
            raise FormulaError('unexpected "%s"' % self.peek()[1])
        return v

    def cmp(self):
        a = self.concat()
        while self.peek()[1] in ('=', '==', '<>', '!=', '<', '>', '<=', '>='):
            op = self.take()[1]
            b = self.concat()
            if isinstance(a, str) or isinstance(b, str):
                x, y = _str(a), _str(b)
            else:
                x, y = _num(a), _num(b)
            a = {'=': x == y, '==': x == y, '<>': x != y, '!=': x != y, '<': x < y, '>': x > y,
                 '<=': x <= y, '>=': x >= y}[op]
        return a

    def concat(self):
        a = self.add()
        while self.peek()[1] == '&':
            self.take()
            a = _str(a) + _str(self.add())
        return a

    def add(self):
        a = self.mul()
        while self.peek()[1] in ('+', '-'):
            op = self.take()[1]
            b = self.mul()
            a = _num(a) + _num(b) if op == '+' else _num(a) - _num(b)
        return a

    def mul(self):
        a = self.unary()
        while self.peek()[1] in ('*', '/') or (self.peek()[0] in ('num', 'name', 'ref') or self.peek()[1] == '(') and self._implicit():
            if self.peek()[1] in ('*', '/'):
                op = self.take()[1]
            else:
                op = '*'
            b = self.unary()
            if op == '*':
                a = _num(a) * _num(b)
            else:
                d = _num(b)
                if d == 0:
                    raise FormulaError('division by zero')
                a = _num(a) / d
        return a

    def _implicit(self):
        # implicit multiplication like 2pi or 3(4+5) only for the calculator
        return self.names.get('__implicit__', False)

    def unary(self):
        if self.peek()[1] == '-':
            self.take()
            return -_num(self.unary())
        if self.peek()[1] == '+':
            self.take()
            return _num(self.unary())
        return self.power()

    def power(self):
        a = self.postfix()
        if self.peek()[1] in ('^', '**'):
            self.take()
            b = self.unary()
            try:
                a = _num(a) ** _num(b)
            except OverflowError:
                raise FormulaError('overflow')
            if isinstance(a, complex):
                raise FormulaError('complex result')
        return a

    def postfix(self):
        a = self.primary()
        while self.peek()[1] in ('%', '!'):
            op = self.take()[1]
            a = _num(a) / 100.0 if op == '%' else _fact(a)
        return a

    def primary(self):
        kind, v = self.take()
        if kind == 'num':
            f = float(v)
            return int(f) if f.is_integer() and 'e' not in v.lower() and '.' not in v else f
        if kind == 'str':
            return v[1:-1].replace('""', '"')
        if kind == 'op' and v == '(':
            a = self.cmp()
            self.expect(')')
            return a
        if kind == 'ref':
            if ':' in v:
                if not self.rng:
                    raise FormulaError('ranges not supported here')
                a, b = v.split(':')
                return self.rng(parse_ref(a), parse_ref(b))
            if self.peek()[1] == '(':
                kind, v = 'name', v   # e.g. LOG2(…)
            elif self.ref:
                return self.ref(parse_ref(v))
            else:
                raise FormulaError('unknown name %s' % v)
        if kind == 'name':
            up = v.upper()
            if self.peek()[1] == '(':
                self.take()
                args = []
                if self.peek()[1] != ')':
                    args.append(self.cmp())
                    while self.peek()[1] in (',', ';'):
                        self.take()
                        args.append(self.cmp())
                self.expect(')')
                fn = FUNCS.get(up)
                if not fn:
                    raise FormulaError('unknown function %s' % v)
                try:
                    return fn(*args)
                except FormulaError:
                    raise
                except TypeError:
                    raise FormulaError('wrong arguments for %s' % up)
                except (ValueError, ZeroDivisionError, OverflowError) as e:
                    raise FormulaError('%s: %s' % (up, e))
            if v.lower() in self.names:
                return self.names[v.lower()]
            if up in ('TRUE', 'FALSE'):
                return up == 'TRUE'
            if up == 'PI':
                return math.pi
            if up == 'E':
                return math.e
            raise FormulaError('unknown name %s' % v)
        raise FormulaError('unexpected %s' % (v or 'end'))


def evaluate(text, ref=None, rng=None, names=None):
    return Parser(text, ref, rng, names).parse()


def fmt_number(v, maxlen=16):
    if isinstance(v, bool):
        return 'TRUE' if v else 'FALSE'
    if isinstance(v, int):
        s = str(v)
        if len(s) <= maxlen:
            return s
        v = float(v)
    if isinstance(v, float):
        if math.isnan(v):
            return 'NaN'
        if math.isinf(v):
            return '-Inf' if v < 0 else 'Inf'
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        for prec in range(12, 2, -1):
            s = ('%.' + str(prec) + 'g') % v
            if len(s) <= maxlen:
                return s
        return '%.3g' % v
    return str(v)
