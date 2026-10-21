"""Calculator: clickable keypad + expression line + history."""
from ..app import App
from ..formula import FormulaError, evaluate, fmt_number
from ..util import fit
from ..widgets import TextField

KEYS = [
    ['C', '(', ')', '%', '/'],
    ['sqrt', '7', '8', '9', '*'],
    ['x^2', '4', '5', '6', '-'],
    ['^', '1', '2', '3', '+'],
    ['pi', '+/-', '0', '.', '='],
]
SCI = ['sin', 'cos', 'tan', 'ln', 'log', 'e', 'n!', 'ans']


class Calc(App):
    name = 'calc'
    title = 'Calculator'
    icon = '[+]'
    size = (36, 19)
    min_size = (26, 14)

    def __init__(self, wm, win, **kw):
        super().__init__(wm, win)
        self.field = TextField(placeholder='expression', on_enter=lambda t: self.equals())
        self.history = []
        self.result = '0'
        self.error = ''
        self.ans = 0

    def press(self, k):
        f = self.field
        if k == 'C':
            if f.text:
                f.set('')
            else:
                self.result = '0'
            self.error = ''
        elif k == '=':
            self.equals()
        elif k == 'sqrt':
            f.insert('sqrt(')
        elif k == 'x^2':
            f.insert('^2')
        elif k == '+/-':
            f.set('-(%s)' % f.text if f.text else '-')
        elif k == 'n!':
            f.insert('!')
        elif k in ('sin', 'cos', 'tan', 'ln', 'log'):
            f.insert(k + '(')
        else:
            f.insert(k)
        self.live()

    def live(self):
        t = self.field.text.strip()
        if not t:
            return
        try:
            v = evaluate(self._close(t), names={'ans': self.ans, '__implicit__': True})
            self.result = fmt_number(v, 20)
            self.error = ''
        except (FormulaError, ValueError, OverflowError, ZeroDivisionError):
            pass

    def _close(self, t):
        return t + ')' * max(0, t.count('(') - t.count(')'))

    def equals(self):
        t = self.field.text.strip()
        if not t:
            return
        try:
            v = evaluate(self._close(t), names={'ans': self.ans, '__implicit__': True})
            self.ans = v
            self.result = fmt_number(v, 20)
            self.history.append((t, self.result))
            self.history = self.history[-50:]
            self.field.set('')
            self.error = ''
        except (FormulaError, ValueError, OverflowError, ZeroDivisionError) as e:
            self.error = str(e)
        self.invalidate()

    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        # display
        cv.fill(0, 0, w, 3, th.c('text', 'field'))
        hist_h = 0
        res = self.error or self.result
        cv.put(w - 1 - len(res[:w - 2]), 1, res[-(w - 2):], th.c('red' if self.error else 'bright', 'field', 1))
        self.field.draw(cv, 0, 2, w, th, focused, bg='field')
        last = self.history[-1] if self.history else None
        if last:
            cv.put(1, 0, fit('%s = %s' % last, w - 2), th.c('dim', 'field'))
        # keypad
        rows = KEYS
        top = 4
        sci_rows = 1 if h - top >= len(rows) * 2 + 2 else 0
        avail = h - top - sci_rows * 2
        kh = max(1, min(4, avail // len(rows)))
        block = len(rows) * kh + (sci_rows * 2 if sci_rows else 0)
        top += max(0, (avail - block) // 2)
        cols = len(rows[0])
        kw = (w + 1) // cols
        for r, row in enumerate(rows):
            for c, k in enumerate(row):
                x = c * kw
                bw = kw - 1 if c < cols - 1 else w - x
                y = top + r * kh
                hov = self.hover == ('k', k)
                if hov:
                    a = th.c('on_accent', 'accent')
                elif k == '=':
                    a = th.c('on_accent', 'accent')
                elif k in '0123456789.':
                    a = th.c('bright', 'raised')
                else:
                    a = th.c('accent' if k in '/*-+^%' else 'text', 'sel' if k == 'C' else 'raised')
                bh = max(1, kh - (1 if kh >= 2 else 0))
                cv.fill(x, y, bw, bh, a)
                cv.put(x + (bw - len(k)) // 2, y + (bh - 1) // 2, k, a)
                self.zone(x, y, bw, bh, ('k', k))
        if sci_rows:
            y = top + len(rows) * kh
            sw = (w + 1) // len(SCI)
            if sw >= 4:
                for i, k in enumerate(SCI):
                    x = i * sw
                    a = th.c('on_accent', 'accent') if self.hover == ('k', k) else th.c('dim', 'win')
                    cv.put(x, y, fit(k, sw - 1, 'center'), a)
                    self.zone(x, y, sw - 1, 1, ('k', k))

    def on_click(self, key, ev):
        if key and key[0] == 'k':
            self.press(key[1])

    def on_key(self, ev):
        k = ev.key
        if k == 'esc' and self.field.text:
            self.field.set('')
        elif k == 'enter' or k == '=':
            self.equals()
        elif k == 'ctrl+c' and not self.field.text:
            self.wm.set_clipboard(self.result)
            self.wm.toast('Copied ' + self.result)
        elif k == 'up' and self.history:
            self.field.set(self.history[-1][0])
        elif self.field.key(ev):
            self.live()
        else:
            return False
        self.invalidate()
        return True

    def on_paste(self, text):
        self.field.insert(text.strip())
        self.live()
        self.invalidate()


APP = Calc
