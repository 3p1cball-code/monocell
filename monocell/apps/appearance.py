"""Appearance editor for the Settings app, modelled on the Windows 95/98 dialog.

A small live desktop shows every UI element. Clicking an element selects it;
clicking its letters picks its text colour, clicking its surface picks its
background. Below the preview the element's two colours are chips, the palette
is shown as the 8 background colours and the 8 text-only colours a kernel VT
has, and the RGB sliders change the colour the chosen chip uses.

The 16 colours are shared paint pots: elements point at pots, so changing a
colour changes every element on that pot ("also used by" says which). Giving an
element another pot is the way to separate them. A background must sit in one
of the 8 background pots; picking a text-only pot as a background swaps it into
the background row when one of those is free of surfaces.
"""
from ..theme import (SLOT_NAMES, SURFACE_ROLES, assign_surface, hex_rgb, rgb_hex,
                     surface_slots)
from ..util import fit, sym
from ..widgets import Menu, MenuItem, TextField, button, hbar, sb_draw, sb_geom

# (key, label, background role or None, text colour: ('role', r) | ('on', surface) | None)
# ('on', s): the element's own text colour, falling back to the global Text colour.
ITEMS = [
    ('desk', 'Desktop', 'desk', ('role', 'desk_fg')),
    ('win', 'Window', 'win', ('on', 'win')),
    ('win_on', 'Window (focused)', 'win_on', ('on', 'win')),
    ('title_on', 'Title bar (active)', 'title_on', ('on', 'title_on')),
    ('title_off', 'Title bar (inactive)', 'title_off', ('on', 'title_off')),
    ('raised', 'Buttons', 'raised', ('on', 'raised')),
    ('raised_on', 'Buttons (focused win)', 'raised_on', ('on', 'raised')),
    ('sel', 'Selection', 'sel', ('on', 'sel')),
    ('accent', 'Highlight', 'accent', ('role', 'on_accent')),
    ('field', 'Input field', 'field', ('on', 'field')),
    ('menu', 'Menu', 'menu', ('on', 'menu')),
    ('bar', 'Taskbar', 'bar', ('on', 'bar')),
    ('shadow', 'Shadow', 'shadow', None),
    None,
    ('text', 'Text', None, ('role', 'text')),
    ('bright', 'Bright text', None, ('role', 'bright')),
    ('dim', 'Dim text / scrollbar', None, ('role', 'dim')),
    ('accent2', 'Highlight 2 (logo)', None, ('role', 'accent2')),
    ('red', 'Red / errors', None, ('role', 'red')),
    ('green', 'Green / ok', None, ('role', 'green')),
    ('yellow', 'Yellow / warnings', None, ('role', 'yellow')),
    ('blue', 'Blue', None, ('role', 'blue')),
    ('magenta', 'Magenta', None, ('role', 'magenta')),
    ('cyan', 'Cyan', None, ('role', 'cyan')),
    ('unread', 'New mail (unread)', None, ('role', 'unread')),
    ('cursor', 'Mouse cursor', None, ('role', 'cursor')),
]
ITEM = {it[0]: it for it in ITEMS if it}
ITEM_KEYS = [it[0] for it in ITEMS if it]
CTRL_ROWS = 9            # rows below the preview
PREVIEW_MAX = 16


class _PvApp:
    """Content callback for a preview window drawn by wm.draw_window."""
    preserve_colors = False

    def __init__(self, fn):
        self.fn = fn
        self.zones = []

    def draw(self, cv, active):
        self.fn(cv, active)


class _PvWin:
    """Just enough of wm.Window for wm.draw_window: the preview uses the real code."""
    borderless = maximized = minimized = False
    snap = modal_for = None
    resizable = True

    def __init__(self, x, y, w, h, title, icon, fn):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.title, self.icon = title, icon
        self.app = _PvApp(fn)

    def rect(self):
        return self.x, self.y, self.w, self.h


class AppearanceMixin:
    """The 'Appearance' section of Settings (expects self.cfg, self.th, self.wm)."""

    def init_appearance(self):
        self.item = 'title_on'
        self.target = 'bg'           # 'bg' | 'fg': which colour of the item is edited
        self.loose = None            # a pot opened with right-click: edit without assigning
        self.hexfield = TextField(on_enter=self.set_hex)
        self.pv_rect = None          # (x, y, w, h) of the preview in body coordinates
        self.chips_pos = (0, 0)
        self.pv_chars = None
        self.sync_hex()

    # ------------------------------------------------------------ model
    def item_text(self, key):
        """(slot, follows_global) of an item's text colour, (None, False) if it has none."""
        spec = ITEM[key][3]
        if spec is None:
            return None, False
        kind, name = spec
        if kind == 'role':
            return self.th.r[name], False
        if name in self.th.on:
            return self.th.on[name], False
        return self.th.r['text'], True

    def item_label(self, key):
        label = ITEM[key][1]
        if key == 'shadow' and not self.cfg.get('shadows', True):
            label += ' (off)'           # Settings > Desktop > Window shadows
        return label

    def item_bg(self, key):
        role = ITEM[key][2]
        return self.th.r[role] if role else None

    def target_ok(self):
        """The item's targets; fall back when the current one does not exist."""
        it = ITEM[self.item]
        if self.target == 'bg' and not it[2]:
            self.target = 'fg'
        elif self.target == 'fg' and not it[3]:
            self.target = 'bg'

    @property
    def pot(self):
        if self.loose is not None:
            return self.loose
        self.target_ok()
        if self.target == 'bg':
            return self.item_bg(self.item)
        return self.item_text(self.item)[0]

    def owner_role(self):
        """The role the current item/target writes (None for a per-element text colour)."""
        if self.target == 'bg':
            return ITEM[self.item][2]
        kind, name = ITEM[self.item][3]
        return name if kind == 'role' else None

    def pot_shared(self, slot):
        """True when something else paints with this colour."""
        own = self.owner_role()
        own_on = None
        if self.target == 'fg' and ITEM[self.item][3][0] == 'on':
            own_on = ITEM[self.item][3][1]
        for r, v in self.th.r.items():
            if v == slot and r != own:
                return True
        for k, v in self.th.on.items():
            if v == slot and k != own_on:
                return True
        return False

    def free_pots(self):
        used = set(self.th.r.values()) | set(self.th.on.values())
        return [p for p in range(16) if p not in used]

    def nearest_pot(self, rgb, exclude, low_only):
        pots = [p for p in (self.th.layout[:8] if low_only else range(16)) if p not in exclude]
        return min(pots, key=lambda p: sum((a - b) ** 2 for a, b in zip(rgb, self.th.rgb[p])))

    def pot_users(self, pot):
        """Elements that change when this colour changes."""
        out, seen = [], set()
        for it in ITEMS:
            if not it:
                continue
            key, label, bg, spec = it
            if bg and self.th.r[bg] == pot:
                out.append(label)
            if spec and spec not in seen:
                seen.add(spec)
                slot, follows = self.item_text(key)
                if slot == pot and not follows:
                    out.append(label if not bg else label + ' text')
        return out

    def select_item(self, key, target=None):
        self.item = key
        self.loose = None
        it = ITEM[key]
        self.target = target or ('bg' if it[2] else 'fg')
        self.target_ok()
        self.sync_hex()

    def sync_hex(self):
        p = self.pot
        if p is not None:
            self.hexfield.set(self.cfg['palette'][p])

    def touched(self):
        """Element colours changed: a saved theme is no longer what is shown."""
        if self.cfg.get('preset') in self.saved:
            self.cfg['preset'] = 'Custom'
        self.apply()

    def assign(self, slot):
        """Put the current item's current target on palette entry `slot`."""
        key = self.item
        self.loose = None
        self.target_ok()
        if self.target == 'bg':
            role = ITEM[key][2]
            try:
                roles, lay = assign_surface(self.cfg['roles'], self.th.layout, role, slot)
            except ValueError:
                self.wm.toast('All 8 background colours are in use - free one first', 'red')
                return
            self.cfg['roles'] = roles
            self.cfg['layout'] = lay
        else:
            kind, name = ITEM[key][3]
            if kind == 'role':
                roles = dict(self.cfg['roles'])
                roles[name] = slot
                self.cfg['roles'] = roles
            else:
                on = dict(self.cfg['on'])
                if slot is None:
                    on.pop(name, None)
                else:
                    on[name] = slot
                self.cfg['on'] = on
        self.touched()
        self.sync_hex()

    def step_pot(self, d):
        self.target_ok()
        pots = list(self.th.layout[:8]) if self.target == 'bg' else list(self.th.layout)
        cur = self.pot
        i = pots.index(cur) if cur in pots else -1
        self.assign(pots[(i + d) % len(pots)])

    def step_item(self, d):
        i = ITEM_KEYS.index(self.item)
        self.select_item(ITEM_KEYS[(i + d) % len(ITEM_KEYS)])

    def separate(self):
        """Give the current colour its own palette entry ("a completely new colour").

        Uses a palette entry nothing else paints with. When all 16 are in use it
        offers to free one: the elements on it move to the nearest remaining colour.
        """
        self.loose = None
        self.target_ok()
        cur = self.pot
        if cur is None:
            return
        if not self.pot_shared(cur):
            self.wm.toast('%s already has its own colour' % self.item_label(self.item))
            return
        for p in self.free_pots():
            if self.take_own(p, cur):
                return
        low = [p for p in self.th.layout[:8]
               if p != cur and self.pot_shared(p)]
        if not low:
            self.wm.toast('Every colour is in use - nothing can be freed', 'red')
            return
        self.free_menu(cur, low if self.target == 'bg' else
                       [p for p in range(16) if p != cur and self.pot_shared(p)])

    def take_own(self, p, cur):
        """Copy the current colour into the unused entry `p` and point the element at it."""
        roles, lay = dict(self.cfg['roles']), list(self.th.layout)
        if self.target == 'bg':
            try:
                roles, lay = assign_surface(roles, lay, ITEM[self.item][2], p)
            except ValueError:
                return False
        pal = list(self.cfg['palette'])
        pal[p] = pal[cur]                       # same look, now independent
        self.cfg['palette'] = pal
        self.cfg['roles'], self.cfg['layout'] = roles, lay
        self.assign(p)
        self.wm.toast('Colour %d now belongs to %s only - adjust it below'
                      % (self.layout_pos(p), self.item_label(self.item)))
        return True

    def free_menu(self, cur, pots):
        """All 16 entries are in use: choose which colour the element may take over."""
        def cost(q):
            """How much a colour is missed: body text first, a shadow barely."""
            roles, ons = self.pot_users_detail(q)
            score = sum(5 if r in ('text', 'dim', 'bright') else 1 for r in roles + ons)
            return (score, len(roles) + len(ons), q)
        items = []
        for p in sorted(pots, key=cost):
            roles, ons = self.pot_users_detail(p)
            users = [ITEM[r][1] for r in roles if r in ITEM]
            users += [ITEM[k][1] + ' text' for k in ons if k in ITEM]
            items.append(MenuItem('%2d #%s  %s' % (self.layout_pos(p), self.cfg['palette'][p],
                                                   fit(', '.join(users) or 'nothing', 46)),
                                  (lambda q=p: self.take_pot(q, cur))))
        x, y = self.chips_pos
        # the chips row says which element this is for; the menu sits right below it
        self.wm.menu(items, self.win.x + 1 + self.body_x + x, self.win.y + 2 + y + 1)

    def pot_users_detail(self, slot):
        roles = [r for r, v in self.th.r.items() if v == slot]
        ons = [k for k, v in self.th.on.items() if v == slot]
        return roles, ons

    def take_pot(self, p, cur):
        """Take entry `p` for the element; everything else on it moves to the closest colour."""
        own = self.owner_role()
        own_on = ITEM[self.item][3][1] if (self.target == 'fg'
                                          and ITEM[self.item][3][0] == 'on') else None
        roles = dict(self.cfg['roles'])
        ons = dict(self.cfg['on'])
        moved = []
        for r, v in list(roles.items()):
            if v == p and r != own:
                roles[r] = self.nearest_pot(self.th.rgb[p], {p}, r in SURFACE_ROLES)
                moved.append(r)
        for k, v in list(ons.items()):
            if v == p and k != own_on:
                ons[k] = self.nearest_pot(self.th.rgb[p], {p}, k in SURFACE_ROLES)
                moved.append(k)
        pal = list(self.cfg['palette'])
        pal[p] = pal[cur]
        self.cfg['palette'] = pal
        self.cfg['roles'], self.cfg['on'] = roles, ons
        self.assign(p)
        text = ', '.join(ITEM[r][1] for r in moved if r in ITEM) or 'nothing'
        self.wm.toast('Colour %d freed; %s now use the nearest colour'
                      % (self.layout_pos(p), text))

    def set_pot_rgb(self, rgb):
        p = self.pot
        if p is None:
            return
        pal = list(self.cfg['palette'])
        pal[p] = rgb_hex(rgb)
        self.cfg['palette'] = pal
        self.cfg['preset'] = 'Custom'
        self.hexfield.set(pal[p])
        self.apply()

    def set_hex(self, t):
        t = t.strip().lstrip('#')
        if len(t) == 3:
            t = ''.join(c * 2 for c in t)
        try:
            self.set_pot_rgb(hex_rgb(t))
        except ValueError:
            self.wm.toast('Invalid colour: ' + t, 'red')

    def item_menu(self, mx, my):
        items = []
        for it in ITEMS:
            if it is None:
                items.append(None)
                continue
            items.append(MenuItem(self.item_label(it[0]), (lambda k=it[0]: self.select_item(k)),
                                  checked=it[0] == self.item))
        self.wm.menu(items, mx, my, title='Element')

    def reset_elements(self):
        from ..theme import DEFAULT_ON, ROLE_DEFAULTS, derive_layout
        self.cfg['roles'] = dict(ROLE_DEFAULTS)
        self.cfg['on'] = dict(DEFAULT_ON)
        self.cfg['layout'] = derive_layout(self.cfg['roles'])
        self.touched()
        self.sync_hex()

    # ------------------------------------------------------------ drawing helpers
    def swatch_attr(self, i):
        th = self.th
        r, g, b = th.rgb[i]
        lum = 0.3 * r + 0.59 * g + 0.11 * b
        fg = th.r['shadow'] if lum > 110 else th.r['bright']
        if fg == i:
            fg = th.r['text'] if th.r['text'] != i else th.r['dim']
        return fg | (i << 4)

    def text_attr(self, slot, bg_slot=None):
        """A text colour on a background that keeps it visible (for 'Aa' chips)."""
        th = self.th
        if bg_slot is None:
            bg_slot = th.r['win']
            r, g, b = th.rgb[slot]
            wr, wg, wb = th.rgb[bg_slot]
            if abs((0.3 * r + 0.59 * g + 0.11 * b) - (0.3 * wr + 0.59 * wg + 0.11 * wb)) < 40:
                bg_slot = th.r['field'] if th.r['field'] != bg_slot else th.r['raised']
        return slot | (bg_slot << 4)

    def draw_pot(self, cv, x, y, pw, slot, low):
        """One palette entry: a numbered block (background pots) or 'Aa' (text-only)."""
        cur = slot == self.pot
        if low:
            a = self.swatch_attr(slot)
            cv.fill(x, y, pw, 1, a)
            n = str(self.layout_pos(slot))
            cv.put(x + (pw - len(n)) // 2, y, n, a)
            if cur:
                cv.putc(x, y, '[', a)
                cv.putc(x + pw - 1, y, ']', a)
        else:
            a = self.text_attr(slot)
            cv.fill(x, y, pw, 1, a)
            cv.put(x + (pw - 2) // 2, y, 'Aa', a)
            if cur:
                cv.putc(x, y, '[', a)
                cv.putc(x + pw - 1, y, ']', a)
        self.zone(x, y, pw, 1, ('pot', slot))

    # ------------------------------------------------------------ the page
    def draw_colors(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        self.target_ok()
        cv.put(0, 0, 'Appearance', th.c('bright', 'win', 1))
        cur = self.current_theme()
        x = 12
        self.theme_pos = (x, 0)
        if w > 30:
            nw = min(22, max(6, w - 40))
            x += button(self, cv, x, 0, fit(cur, nw - 2) + ' v', ('theme', 'menu')) + 1
            if x + 12 < w:
                x += button(self, cv, x, 0, 'Save as...', ('theme', 'save')) + 1
            if x + 8 < w:
                button(self, cv, x, 0, 'Delete', ('theme', 'del'), enabled=cur in self.saved)
        ph = min(PREVIEW_MAX, h - 2 - CTRL_ROWS)
        if ph >= 7:
            self.pv_rect = (0, 1, w, ph)
            y = 2 + ph
        else:
            self.pv_rect = None
            cv.put(0, 1, fit('(enlarge the window for the preview)', w), th.c('dim', 'win'))
            y = 2
        self.draw_controls(cv, y, focused)

    def draw_controls(self, cv, y, focused):
        th = self.th
        w = cv.w
        it = ITEM[self.item]
        # item chooser
        cv.put(0, y, 'Item', th.c('dim', 'win'))
        lw = min(26, max(12, w - 30))
        a = th.c('on_accent', 'accent') if self.hover == 'item' else th.c('text', 'field')
        cv.fill(6, y, lw, 1, a)
        cv.put(7, y, fit(self.item_label(self.item), lw - 4), a)
        cv.put(6 + lw - 2, y, 'v', a)
        self.zone(6, y, lw, 1, 'item')
        bx = 7 + lw
        bx += button(self, cv, bx, y, '<', ('istep', -1)) + 1
        bx += button(self, cv, bx, y, '>', ('istep', 1)) + 1
        if bx + 24 < w:
            cv.put(bx + 1, y, fit('or click the preview', w - bx - 1), th.c('dim', 'win'))
        # the two colours of the item
        y += 1
        x = 0
        for tgt, label in (('bg', 'Background'), ('fg', 'Text')):
            on = self.target == tgt and self.loose is None
            if tgt == 'bg':
                slot = self.item_bg(self.item)
                follows = False
            else:
                slot, follows = self.item_text(self.item)
            la = (th.c('on_accent', 'accent') if on else
                  th.c('bright', 'sel') if self.hover == ('target', tgt) else th.c('text', 'raised'))
            s = ' %s ' % label
            cv.put(x, y, s, la)
            x += len(s)
            if slot is None:
                cv.put(x + 1, y, 'n/a', th.c('dim', 'win'))
                self.zone(x - len(s), y, len(s) + 4, 1, ('target', tgt))
                x += 6
                continue
            if tgt == 'bg':
                cv.fill(x, y, 4, 1, self.swatch_attr(slot))
            else:
                bg = self.item_bg(self.item)
                cv.put(x, y, ' Aa ', self.text_attr(slot, bg))
            cv.put(x + 5, y, '#' + self.cfg['palette'][slot], th.c('text', 'win'))
            self.zone(x - len(s), y, len(s) + 12, 1, ('target', tgt))
            x += 13
            if tgt == 'fg' and it[3] and it[3][0] == 'on':
                if follows:
                    cv.put(x, y, '(= Text)', th.c('dim', 'win'))
                    x += 9
                elif x + 10 < w:
                    x += button(self, cv, x, y, '= Text', 'follow') + 1
            x += 2
        self.chips_pos = (0, y)
        if self.pot is not None and self.pot_shared(self.pot):
            bx = w - 14
            if bx > x + 1:
                button(self, cv, bx, y, 'New colour', 'separate')
        # who else uses this colour
        y += 1
        p = self.pot
        if p is not None:
            users = self.pot_users(p)
            pre = ('colour %d (not applied - left-click to use)  ' % self.layout_pos(p)
                   if self.loose is not None else '')
            txt = pre + 'used by: ' + (', '.join(users) if users else 'nothing')
            cv.put(0, y, fit(txt, w), th.c('dim', 'win'))
        # the palette: 8 background + 8 text-only colours
        lay = self.th.layout
        pw = max(3, min(5, (w - 22) // 8))
        painted = len(surface_slots(th.r))
        colours = len(set(th.r.values()) | set(th.on.values()))
        for row, (label, pots, low) in enumerate((('Backgrounds', lay[:8], True),
                                                  ('Text only', lay[8:], False))):
            yy = y + 1 + row
            cv.put(0, yy, label, th.c('dim', 'win'))
            for i, slot in enumerate(pots):
                self.draw_pot(cv, 12 + i * (pw + 1), yy, pw, slot, low)
            ex = 12 + 8 * (pw + 1)
            note = ('%d/8 painted' % painted if low else '%d/16 used' % colours)
            if ex + len(note) < w:
                cv.put(ex + 1, yy, note, th.c('dim', 'win'))
        # RGB of the edited colour
        y += 3
        if p is None:
            return
        rgb = th.rgb[p]
        bw = max(10, w - 18)
        for k, (label, v) in enumerate(zip('RGB', rgb)):
            yy = y + k
            cv.put(0, yy, label, th.c('dim', 'win'))
            n = int(bw * v / 255 + 0.5)
            cv.fill(3, yy, bw, 1, th.c('dim', 'field'), '-')
            cv.fill(3, yy, n, 1, th.c('accent', 'field'), '=')
            cv.putc(3 + min(bw - 1, n), yy, '|', th.c('bright', 'accent'))
            cv.put(4 + bw, yy, '%3d' % v, th.c('bright', 'win'))
            self.zone(3, yy, bw, 1, ('chan', k, bw, self.body_x + 3))
            cv.put(8 + bw, yy, '-', th.c('text', 'raised'))
            cv.put(10 + bw, yy, '+', th.c('text', 'raised'))
            self.zone(8 + bw, yy, 1, 1, ('nudge', k, -8))
            self.zone(10 + bw, yy, 1, 1, ('nudge', k, 8))
        yy = y + 3
        cv.put(0, yy, 'Hex', th.c('dim', 'win'))
        self.hexfield.draw(cv, 4, yy, 10, th, focused and self.edit == 'hex')
        self.zone(4, yy, 10, 1, 'hexfield')
        cv.put(16, yy, fit('up/down item  space bg/text  left/right colour  R/G/B +-8  n new',
                           w - 16), th.c('dim', 'win'))

    def layout_pos(self, slot):
        return self.th.layout.index(slot) + 1

    # ------------------------------------------------------------ preview
    def post_draw(self, cv, active):
        """Called by the window manager after the focus lift: the preview keeps the
        colours it shows (its own focused window is lifted by the same code)."""
        if self.section != 'colors' or not self.pv_rect:
            self.pv_chars = None
            return
        x, y, w, h = self.pv_rect
        pv = cv.sub(self.body_x + x, y, w, h)
        self.zone_off = (self.body_x + x, y)
        try:
            self.draw_preview(pv)
        finally:
            self.zone_off = (0, 0)
        # remember the characters, so a click can tell letters from surface
        rows = []
        for yy in range(h):
            ay = pv.oy + yy
            if pv.y0 <= ay < pv.y1:
                rows.append(''.join(pv.buf.ch[ay][max(pv.ox, pv.x0):min(pv.ox + w, pv.x1)]))
            else:
                rows.append('')
        self.pv_chars = rows

    def pz(self, x, y, w, h, item, target='auto'):
        self.zone(x, y, w, h, ('pv', item, target))

    def draw_preview(self, pv):
        th, wm = self.th, self.wm
        W, H = pv.w, pv.h
        top = self.cfg['taskbar'] == 'top'
        bar_y = 0 if top else H - 1
        dy = 1 if top else 0
        DH = H - 1
        # desktop
        pv.fill(0, dy, W, DH, th.c('desk_fg', 'desk'))
        self.pz(0, dy, W, DH, 'desk')
        # geometry: an inactive window behind a focused one, a menu, a toast
        ax = 3 if W < 56 else 5
        aw = max(24, min(W - ax - 11, 62))
        iw = max(20, aw - 6)
        ah = max(4, DH - 3)
        ih = max(3, DH - 2)
        iy, ay = dy, dy + 2
        label = 'Desktop'
        lx = ax + aw + 2
        if lx + len(label) <= W:
            pv.put(lx, dy + 1, label, th.c('desk_fg', 'desk'))
            self.pz(lx, dy + 1, len(label), 1, 'desk', 'fg')

        # ---- inactive window
        def inactive(cv, active):
            cv.put(1, 0, 'Window text', th.c('text', 'win'))
            cv.put(14, 0, 'dim', th.c('dim', 'win'))
            cv.put(19, 0, ' Button ', th.c('text', 'raised'))
        wi = _PvWin(1, iy, iw, ih, 'Inactive window', sym('files', '[/]'), inactive)
        wm.draw_window(pv, wi, False)
        self.pz(1, iy, iw, ih, 'win')
        self.pz(1, iy, iw, 1, 'title_off')
        self.pz(2, iy + 1, 11, 1, 'win', 'fg')
        self.pz(15, iy + 1, 3, 1, 'dim', 'fg')
        self.pz(20, iy + 1, 8, 1, 'raised')
        self.shadow_zones(1, iy, iw, ih)

        # ---- focused window with the common widgets
        ox, oy = ax + 1, ay + 1

        def focused(cv, active):
            cw, ch = cv.w, cv.h
            x = 1
            for m in ('File', 'Edit', 'View'):
                s = ' %s ' % m
                cv.put(x, 0, s, th.c('text', 'win'))
                x += len(s)
            if ch > 1:
                x = 1
                for s, a in (('Normal', th.c('text', 'win')), ('Disabled', th.c('dim', 'win')),
                             ('Bright', th.c('bright', 'win', 1)),
                             (' Selected ', th.c('bright', 'sel')),
                             ('Link', th.c('accent', 'win', 2))):
                    cv.put(x, 1, s, a)
                    x += len(s) + 1
            if ch > 2:
                tf = TextField('Search')
                tf.draw(cv, 1, 2, 14, th, True)
                cv.put(16, 2, ' OK ', th.c('accent', 'raised'))
                cv.put(21, 2, ' Cancel ', th.c('text', 'raised'))
                cv.put(30, 2, ' Hover ', th.c('on_accent', 'accent'))
                cv.put(38, 2, ' Off ', th.c('dim', 'raised'))
            n_list = ch - 5
            names = ('Documents', 'Pictures', 'New mail', 'Videos', 'Downloads', 'Projects')
            for i in range(max(0, n_list)):
                a = (th.c('bright', 'sel') if i == 1 else
                     th.c('unread', 'win', 1) if i == 2 else th.c('text', 'win'))
                if i == 1:
                    cv.fill(0, 3 + i, cw - 1, 1, a)
                cv.put(1, 3 + i, names[i % len(names)], a)
            if n_list > 1:
                sb_draw(cv, sb_geom(cw - 1, 3, n_list, 12, n_list, 3), th)
            if ch > 4:
                cv.put(1, ch - 2, 'Progress', th.c('dim', 'win'))
                hbar(cv, 10, ch - 2, 14, 0.6, th)
            if ch > 3:
                x = 1
                for s, role in (('error', 'red'), ('ok', 'green'), ('warning', 'yellow'),
                                ('blue', 'blue'), ('magenta', 'magenta'), ('cyan', 'cyan'),
                                ('logo', 'accent2')):
                    cv.put(x, ch - 1, s, th.c(role, 'win'))
                    x += len(s) + 1
        wa = _PvWin(ax, ay, aw, ah, 'Active window', sym('editor', '[=]'), focused)
        wm.draw_window(pv, wa, True)
        self.pz(ax, ay, aw, ah, 'win_on')
        self.pz(ax, ay, aw, 1, 'title_on')
        if self.cfg['frame'] != 'lines':
            self.pz(ax, ay, 1, 1, 'accent', 'bg')
        self.pz(ax + 2, ay, len(wa.icon), 1, 'accent', 'bg')
        self.shadow_zones(ax, ay, aw, ah)
        cw, ch = aw - 2, ah - 2

        def fz(x, y, w, h, item, tgt='auto'):
            # a zone inside the focused window's body, clipped to it
            x0, y0 = max(x, ox), max(y, oy)
            x1, y1 = min(x + w, ox + cw), min(y + h, oy + ch)
            if x1 > x0 and y1 > y0:
                self.pz(x0, y0, x1 - x0, y1 - y0, item, tgt)
        fz(ox + 1, oy, 18, 1, 'win_on')
        if ch > 1:
            x = ox + 1
            for s, item, tgt in (('Normal', 'text', 'fg'), ('Disabled', 'dim', 'fg'),
                                 ('Bright', 'bright', 'fg'), (' Selected ', 'sel', 'auto'),
                                 ('Link', 'accent', 'bg')):
                fz(x, oy + 1, len(s), 1, item, tgt)
                x += len(s) + 1
        if ch > 2:
            fz(ox + 1, oy + 2, 14, 1, 'field')
            fz(ox + 1 + 1 + len('Search'), oy + 2, 1, 1, 'accent', 'bg')
            fz(ox + 16, oy + 2, 4, 1, 'raised_on')
            fz(ox + 17, oy + 2, 2, 1, 'accent', 'bg')
            fz(ox + 21, oy + 2, 8, 1, 'raised_on')
            fz(ox + 30, oy + 2, 7, 1, 'accent')
            fz(ox + 38, oy + 2, 5, 1, 'raised_on', 'bg')
            fz(ox + 39, oy + 2, 3, 1, 'dim', 'fg')
        n_list = ch - 5
        if n_list > 1:
            fz(ox, oy + 4, cw - 1, 1, 'sel')
        if n_list > 2:
            fz(ox + 1, oy + 5, len('New mail'), 1, 'unread', 'fg')
            g = sb_geom(cw - 1, 3, n_list, 12, n_list, 3)
            fz(ox + cw - 1, oy + 3, 1, n_list, 'field', 'bg')
            if g:
                fz(ox + cw - 1, oy + 3 + g.ty, 1, g.th, 'dim', 'fg')
        if ch > 4:
            fz(ox + 1, oy + ch - 2, 8, 1, 'dim', 'fg')
            fz(ox + 10, oy + ch - 2, 8, 1, 'accent', 'bg')
            fz(ox + 18, oy + ch - 2, 6, 1, 'raised_on', 'bg')
        if ch > 3:
            x = ox + 1
            for s, role in (('error', 'red'), ('ok', 'green'), ('warning', 'yellow'),
                            ('blue', 'blue'), ('magenta', 'magenta'), ('cyan', 'cyan'),
                            ('logo', 'accent2')):
                fz(x, oy + ch - 1, len(s), 1, role, 'fg')
                x += len(s) + 1

        # ---- an open context menu (the real popup widget), right of the samples
        mitems = [MenuItem('Undo', hint='^Z'), MenuItem('Copy', hint='^C'), None,
                  MenuItem('Paste', enabled=False), MenuItem('Select all')]
        m = Menu(wm, mitems, 0, 0)
        mx, my = W - m.w - 2, oy + 3
        if mx > ox + 20 and (my + m.h < bar_y or top):
            m.x, m.y = mx, my
            m.sel = 1
            m.draw(pv)
            self.pz(m.x, m.y, m.w, m.h, 'menu')
            self.pz(m.x + 1, m.y + 2, m.w - 2, 1, 'accent')
            self.pz(m.x + 1, m.y + 3, m.w - 2, 1, 'sel', 'bg')     # separator: selection colour
            self.pz(m.x + 2, m.y + 4, 5, 1, 'dim', 'fg')
            for yy in (m.y + 1, m.y + 2):
                self.pz(m.x + m.w - 4, yy, 2, 1, 'dim' if yy == m.y + 1 else 'accent', 'fg')
            if self.cfg['shadows']:
                self.pz(m.x + 1, m.y + m.h, m.w, 1, 'shadow', 'bg')
                self.pz(m.x + m.w, m.y + 1, 1, m.h, 'shadow', 'bg')
            # the mouse pointer rests on the highlighted entry
            cx, cy = m.x + 4, m.y + 2
            cur = th.r['cursor']
            pv.set_attr(cx, cy, 1, 1, th.c(th.contrast(cur), cur))
            self.pz(cx, cy, 1, 1, 'cursor', 'fg')

        # ---- a toast above the taskbar
        msg = 'Saved'
        tw = len(msg) + 4
        ty = dy + DH - 2 if not top else dy + 1
        tx = W - tw - 1
        if tx > ax + 10:
            pv.fill(tx, ty, tw, 1, th.c('bright', 'raised'))
            pv.put(tx, ty, ' ', th.c('on_accent', 'accent'))
            pv.put(tx + 2, ty, msg, th.c('bright', 'raised'))
            self.pz(tx, ty, tw, 1, 'raised')
            self.pz(tx, ty, 1, 1, 'accent', 'bg')
            self.pz(tx + 2, ty, len(msg), 1, 'bright', 'fg')

        # ---- taskbar
        self.draw_mini_taskbar(pv, bar_y, W)

    def shadow_zones(self, x, y, w, h):
        if self.cfg['shadows']:
            self.pz(x + w, y + 1, 1, h, 'shadow', 'bg')
            self.pz(x + 1, y + h, w, 1, 'shadow', 'bg')

    def draw_mini_taskbar(self, pv, y, W):
        th = self.th
        pv.fill(0, y, W, 1, th.c('text', 'bar'))
        self.pz(0, y, W, 1, 'bar')
        s = ' :: '
        pv.put(0, y, s, th.c('accent', 'raised', 1))
        self.pz(0, y, len(s), 1, 'raised', 'bg')
        self.pz(1, y, 2, 1, 'accent', 'bg')
        x = len(s) + 1
        for title, icon, kind in (('Active', sym('editor', '[=]'), 'focus'),
                                  ('Inactive', sym('files', '[/]'), 'other'),
                                  ('Hidden', sym('music', '(~)'), 'min')):
            bw = min(16, max(8, (W - 30) // 3))
            if x + bw > W - 16:
                break
            if kind == 'focus':
                a, ia, item = th.c('bright', 'raised'), th.c('accent', 'raised'), 'raised'
            elif kind == 'other':
                a, ia, item = th.c('text', 'bar'), th.c('dim', 'bar'), 'bar'
            else:
                a, ia, item = th.c('dim', 'bar'), th.c('dim', 'bar'), 'bar'
            pv.fill(x, y, bw, 1, a)
            pv.put(x + 1, y, icon[:3], ia)
            pv.put(x + 2 + min(3, len(icon)), y, fit(title, bw - 6), a)
            self.pz(x, y, bw, 1, item)
            if kind == 'focus':
                self.pz(x + 1, y, len(icon[:3]), 1, 'accent', 'bg')
                self.pz(x + 2 + min(3, len(icon)), y, bw - 6, 1, 'bright', 'fg')
            elif kind == 'min':
                self.pz(x, y, bw, 1, 'dim', 'fg')
            x += bw + 1
        clock, date = ' 12:00 ', ' Mon 26 Oct '
        cx = W - len(clock)
        pv.put(cx, y, clock, th.c('bright', 'bar'))
        self.pz(cx + 1, y, 5, 1, 'bright', 'fg')
        dx = cx - len(date)
        if dx > x:
            pv.put(dx, y, date, th.c('dim', 'bar'))
            self.pz(dx + 1, y, len(date) - 2, 1, 'dim', 'fg')

    # ------------------------------------------------------------ input
    def preview_click(self, key, ev):
        _, item, tgt = key
        if tgt == 'auto':
            ch = ' '
            if self.pv_chars and self.pv_rect:
                rx, ry = ev.x - self.body_x - self.pv_rect[0], ev.y - self.pv_rect[1]
                if 0 <= ry < len(self.pv_chars) and 0 <= rx < len(self.pv_chars[ry]):
                    ch = self.pv_chars[ry][rx]
            tgt = 'fg' if ch != ' ' and ITEM[item][3] else 'bg'
        self.select_item(item, tgt)

    def appearance_click(self, key, ev):
        """Clicks on the Appearance page; True when handled."""
        if key == 'item':
            self.item_menu(self.win.x + 1 + ev.x, self.win.y + 2 + ev.y)
        elif key[0] == 'istep':
            self.step_item(key[1])
        elif key[0] == 'pv':
            self.preview_click(key, ev)
        elif key[0] == 'target':
            self.loose = None
            self.target = key[1]
            self.target_ok()
            self.sync_hex()
        elif key == 'separate':
            self.separate()
        elif key == 'follow':
            self.target = 'fg'
            self.assign(None)
        elif key[0] == 'pot':
            if ev.button == 2:
                self.loose = key[1]
                self.sync_hex()
            else:
                self.assign(key[1])
        elif key[0] == 'chan':
            self.drag_chan = key
            self.chan_set(key, ev.x)
        elif key[0] == 'nudge':
            rgb = list(self.th.rgb[self.pot])
            rgb[key[1]] = max(0, min(255, rgb[key[1]] + key[2]))
            self.set_pot_rgb(rgb)
        elif key == 'hexfield':
            self.edit = 'hex'
        else:
            return False
        return True

    def chan_set(self, key, mx):
        _, k, bw, x0 = key
        rel = mx - x0
        v = max(0, min(255, int(rel * 255 / max(1, bw - 1) + 0.5)))
        rgb = list(self.th.rgb[self.pot])
        rgb[k] = v
        self.set_pot_rgb(rgb)

    def appearance_key(self, k):
        if k == 'up':
            self.step_item(-1)
        elif k == 'down':
            self.step_item(1)
        elif k in ('left', 'right'):
            self.step_pot(-1 if k == 'left' else 1)
        elif k == ' ':
            self.loose = None
            self.target = 'fg' if self.target == 'bg' else 'bg'
            self.target_ok()
            self.sync_hex()
        elif k in ('r', 'g', 'b', 'R', 'G', 'B'):
            rgb = list(self.th.rgb[self.pot])
            c = 'rgb'.index(k.lower())
            rgb[c] = max(0, min(255, rgb[c] + (8 if k.isupper() else -8)))
            self.set_pot_rgb(rgb)
        elif k in ('enter', '#'):
            self.edit = 'hex'
        elif k == 'n':
            self.separate()
        elif k == 'i':
            self.item_menu(self.win.x + 8, self.win.y + 4)
        elif k == 't':
            self.theme_menu()
        elif k == 's':
            self.save_theme_dialog()
        elif k == 'd':
            self.delete_theme_dialog()
        else:
            return False
        return True


__all__ = ['AppearanceMixin', 'ITEMS', 'ITEM', 'ITEM_KEYS', 'SLOT_NAMES']
