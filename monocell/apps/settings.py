"""Settings: 16-colour palette editor, presets, roles, desktop/media/web/calendar options."""
from ..app import App
from .. import gcal
from ..theme import PRESET_CURSOR, PRESETS, ROLE_DEFAULTS, ROLE_HELP, SLOT_NAMES, hex_rgb, rgb_hex
from ..util import fit
from ..widgets import MenuItem, TextField, button

SECTIONS = [('colors', 'Colors'), ('roles', 'Color roles'), ('desktop', 'Desktop'), ('background', 'Background'),
            ('media', 'Images & video'), ('web', 'Web'), ('calendar', 'Calendar'), ('tools', 'Tools'),
            ('about', 'About')]

OPTIONS = {
    'desktop': [
        ('taskbar', 'Taskbar position', ['bottom', 'top']),
        ('frame', 'Window frames', ['flat', 'lines']),
        ('shadows', 'Window shadows', [True, False]),
        ('clock_24h', '24 hour clock', [True, False]),
        ('clock_seconds', 'Clock shows seconds', [False, True]),
        ('desktop_logo', 'Desktop logo', [True, False]),
        ('wallpaper', 'Wallpaper image', 'file'),
        ('symbols', 'Symbols (single-width unicode)', ['auto', 'on', 'off']),
        ('strict_ascii', 'Strict ASCII (transliterate umlauts etc.)', [False, True]),
        ('color_mode', 'Colour output (restart)', ['auto', 'truecolor', 'palette', 'linux', 'ansi']),
    ],
    'background': [
        ('background', 'Background', ['auto', 'logo', 'plain', 'image', 'matrix']),
        ('rain_density', 'Rain density', [1.0, 0.5, 0.8, 1.4, 2.0]),
        ('rain_speed', 'Rain speed', [1.0, 0.6, 1.5, 2.0]),
        ('rain_random', 'Rain randomness', [0.6, 0.2, 0.5, 0.8, 1.0]),
    ],
    'media': [
        ('image_mode', 'Image rendering', ['blend', 'pixel', 'grey', 'ascii', 'mono']),
        ('dither', 'Dithering', ['bayer4', 'bayer8', 'fs', 'none']),
        ('video_mode', 'Video rendering', ['pixel', 'blend', 'grey', 'ascii', 'mono']),
        ('ascii_charset', 'ASCII charset', ['full', 'simple', 'shades']),
        ('cell_aspect', 'Cell aspect h/w (0 = auto)', [0.0, 1.8, 2.0, 2.2, 2.4, 1.5, 1.0]),
        ('video_fps', 'Video frame rate', [15, 10, 12, 20, 24, 30, 8]),
        ('av_offset', 'A/V offset ms (negative = picture earlier)',
         [0, -40, -80, -120, -160, 40, 80, 120]),
        ('audio_sink', 'Audio output', ['auto', 'pacat', 'pw-cat', 'aplay']),
    ],
    'web': [
        ('web_engine', 'Engine', ['auto', 'lite', 'chromium']),
        ('web_images', 'Show images', [True, False]),
        ('web_home', 'Home page', 'text'),
        ('search_url', 'Search URL (%s = query)', 'text'),
        ('chromium', 'Chromium/Brave binary (empty = auto)', 'text'),
    ],
    'calendar': [
        ('gcal_url', 'Google Calendar iCal address', 'text'),
        ('gcal_sync', 'Sync now', 'action'),
    ],
    'tools': [
        ('opencode_cmd', 'opencode command', 'text'),
        ('opencode_host', 'Run over SSH on host (empty = local)', 'text'),
        ('opencode_workdir', 'Working directory (empty = home)', 'text'),
        ('kdeconnect_display', 'KDE Connect headless X display', 'text'),
        ('shot_dir', 'Text screenshot folder', 'text'),
    ],
}

MODE_HELP = {
    'blend': 'two palette colours mixed per cell with density characters (.:+%#@)',
    'pixel': 'one ordered-dithered pixel per cell (background colour only)',
    'ascii': 'classic ASCII art: brightness -> character, coloured',
    'grey': 'like blend, but only the grey ramp (calm; good for wallpapers)',
    'mono': 'ASCII art in a single colour',
}


class Settings(App):
    name = 'settings'
    title = 'Settings'
    icon = '{o}'
    size = (76, 24)
    min_size = (50, 16)
    single = True

    def __init__(self, wm, win, section='colors', **kw):
        super().__init__(wm, win)
        self.cfg = wm.cfg
        self.section = section
        self.slot = 14
        self.hexfield = TextField(on_enter=self.set_hex)
        self.hexfield.set(self.cfg['palette'][self.slot])
        self.edit = None          # (key, TextField) for text options
        self.drag_chan = None
        self.opt_sel = 0

    def reopen(self, section=None, **kw):
        if section:
            self.section = section

    # ------------------------------------------------------------ apply
    def apply(self):
        self.cfg.save()
        self.wm.apply_theme()
        self.wm.load_wallpaper()

    def set_slot_rgb(self, rgb):
        pal = list(self.cfg['palette'])
        pal[self.slot] = rgb_hex(rgb)
        self.cfg['palette'] = pal
        self.cfg['preset'] = 'Custom'
        self.hexfield.set(pal[self.slot])
        self.apply()

    def set_hex(self, t):
        t = t.strip().lstrip('#')
        if len(t) == 3:
            t = ''.join(c * 2 for c in t)
        try:
            self.set_slot_rgb(hex_rgb(t))
        except ValueError:
            self.wm.toast('Invalid colour: ' + t, 'red')

    def load_preset(self, name):
        self.cfg['palette'] = list(PRESETS[name])
        self.cfg['preset'] = name
        if name == 'Matrix':
            self.cfg['background'] = 'matrix'
        elif self.cfg.get('background') == 'matrix':
            self.cfg['background'] = 'auto'
        self.hexfield.set(self.cfg['palette'][self.slot])
        self.apply()

    # ------------------------------------------------------------ draw
    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        nav = 18
        cv.fill(0, 0, nav, h, th.c('text', 'raised'))
        for i, (key, label) in enumerate(SECTIONS):
            on = key == self.section
            a = th.c('on_accent', 'accent') if on else (th.c('bright', 'sel') if self.hover == ('sec', key) else th.c('text', 'raised'))
            cv.fill(0, 1 + i, nav, 1, a)
            cv.put(2, 1 + i, label, a)
            self.zone(0, 1 + i, nav, 1, ('sec', key))
        self.body_x = nav + 2
        self.body_x = nav + 2
        self.zone_off = (self.body_x, 0)
        body = cv.sub(nav + 2, 0, w - nav - 2, h)
        try:
            getattr(self, 'draw_' + (self.section if self.section in ('colors', 'roles', 'about') else 'options'))(body, focused)
        finally:
            self.zone_off = (0, 0)

    def swatch_attr(self, i):
        th = self.th
        # pick readable text on the swatch
        r, g, b = th.rgb[i]
        lum = 0.3 * r + 0.59 * g + 0.11 * b
        fg = th.r['shadow'] if lum > 110 else th.r['bright']
        return fg | (i << 4)

    def draw_colors(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        cv.put(0, 0, 'Palette', th.c('bright', 'win', 1))
        cv.put(9, 0, '(%s)' % self.cfg.get('preset', 'Custom'), th.c('dim', 'win'))
        sw = max(6, min(12, (w - 1) // 4))
        for i in range(16):
            r, c = divmod(i, 4)
            x, y = c * sw, 2 + r * 2
            a = self.swatch_attr(i)
            cv.fill(x, y, sw - 1, 2, a)
            cv.put(x + 1, y, fit('%d %s' % (i, SLOT_NAMES[i]), sw - 2), a)
            if i == self.slot:
                cv.put(x + 1, y + 1, fit('#' + self.cfg['palette'][i], sw - 2), a)
                cv.putc(x + sw - 2, y + 1, '*', a)
            self.zone(x, y, sw - 1, 2, ('slot', i))
        y = 11
        cv.put(0, y, 'Slot %d: %s' % (self.slot, SLOT_NAMES[self.slot]), th.c('bright', 'win'))
        rgb = th.rgb[self.slot]
        bw = max(10, w - 18)
        for k, (label, v) in enumerate(zip('RGB', rgb)):
            yy = y + 2 + k
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
        yy = y + 6
        cv.put(0, yy, 'Hex', th.c('dim', 'win'))
        self.hexfield.draw(cv, 4, yy, 10, th, focused and self.edit == 'hex')
        self.zone(4, yy, 10, 1, 'hexfield')
        cv.put(16, yy, 'arrows: slot  r/g/b R/G/B: adjust', th.c('dim', 'win'))
        yy += 2
        if yy < h:
            cv.put(0, yy, 'Presets', th.c('dim', 'win'))
            x = 8
            for name in PRESETS:
                if x + len(name) + 3 > w:
                    yy += 1
                    x = 8
                    if yy >= h:
                        break
                x += button(self, cv, x, yy, name, ('preset', name), active=self.cfg.get('preset') == name) + 1

    def draw_roles(self, cv, focused):
        th = self.th
        cv.put(0, 0, 'Which palette slot is used for what', th.c('bright', 'win', 1))
        roles = [r for r in ROLE_DEFAULTS if r in ROLE_HELP]
        two = cv.h - 4 < len(roles)
        colw = cv.w // 2 if two else min(cv.w, 44)
        per = (len(roles) + 1) // 2 if two else len(roles)
        for i, role in enumerate(roles):
            x = (i // per) * colw
            yy = 2 + i % per
            idx = th.r[role]
            on = self.hover == ('role', role)
            cv.put(x, yy, fit(ROLE_HELP[role], colw - 12), th.c('bright' if on else 'text', 'win'))
            cv.fill(x + colw - 11, yy, 9, 1, self.swatch_attr(idx))
            cv.put(x + colw - 10, yy, '%2d' % idx, self.swatch_attr(idx))
            self.zone(x, yy, colw - 2, 1, ('role', role))
        button(self, cv, 0, cv.h - 1, 'Reset roles', 'reset_roles')

    def role_menu(self, role, mx, my):
        items = []
        for i in range(16):
            items.append(MenuItem('%2d %s  #%s' % (i, SLOT_NAMES[i], self.cfg['palette'][i]),
                                  (lambda i=i: self.set_role(role, i)), checked=self.th.r[role] == i))
        self.wm.menu(items, mx, my)

    def set_role(self, role, i):
        roles = dict(self.cfg['roles'])
        roles[role] = i
        self.cfg['roles'] = roles
        self.apply()

    def options(self):
        return OPTIONS.get(self.section, [])

    def draw_options(self, cv, focused):
        th = self.th
        title = dict(SECTIONS)[self.section]
        cv.put(0, 0, title, th.c('bright', 'win', 1))
        y = 2
        lw = min(40, max(len(o[1]) for o in self.options()) + 2)
        for i, (key, label, kind) in enumerate(self.options()):
            on = i == self.opt_sel and focused
            cv.put(0, y, fit(label, lw), th.c('bright' if on else 'text', 'win'))
            vx = lw
            vw = cv.w - vx
            val = self.cfg.get(key)
            hov = self.hover == ('opt', key)
            if isinstance(kind, list):
                s = 'on' if val is True else 'off' if val is False else str(val)
                if key == 'cell_aspect' and not val:
                    s = 'auto (%.2f)' % self.wm.term.cell_aspect
                a = th.c('on_accent', 'accent') if hov or on else th.c('accent', 'raised')
                cv.put(vx, y, ' < %s > ' % s, a)
                self.zone(vx, y, len(s) + 6, 1, ('opt', key))
            elif kind == 'action':
                a = th.c('on_accent', 'accent') if hov else th.c('accent', 'raised')
                cv.put(vx, y, ' [ %s ] ' % label, a)
                self.zone(vx, y, len(label) + 6, 1, ('opt', key))
            elif kind == 'file':
                from ..util import shorten_path
                s = shorten_path(s_val, max(8, vw - 12)) if (s_val := str(val or '')) else 'choose file'
                a = th.c('on_accent', 'accent') if hov else th.c('accent', 'raised')
                cv.put(vx, y, ' < %s > ' % s, a)
                self.zone(vx, y, vw, 1, ('opt', key))
            else:
                if self.edit and self.edit[0] == key:
                    self.edit[1].draw(cv, vx, y, vw, th, focused)
                else:
                    s = str(val or '')
                    if kind == 'secret' and s:
                        s = s[:4] + '*' * 12
                    a = th.c('bright', 'sel') if hov or on else th.c('text', 'field')
                    cv.fill(vx, y, vw, 1, a)
                    cv.put(vx + 1, y, fit(s or '(empty)', vw - 2), a)
                self.zone(vx, y, vw, 1, ('opt', key))
            y += 1
            if key == 'image_mode':
                cv.put(lw, y, fit(MODE_HELP.get(self.cfg['image_mode'], ''), cv.w - lw), th.c('dim', 'win'))
                y += 1
        y += 1
        if self.section == 'background':
            from ..util import wrap_text
            txt = ('The animated rain is visible when Background is "matrix" (or "auto" with '
                   'the Matrix preset). Its colours come from the palette\'s green role, so it '
                   'stays green with the Matrix palette. Rain settings apply live.')
            for i, ln in enumerate(wrap_text(txt, cv.w)):
                if y + i < cv.h:
                    cv.put(0, y + i, ln, th.c('dim', 'win'))
        if self.section == 'media':
            from ..util import wrap_text
            txt = ('One shared setting for all images: viewer, video, browser images and the '
                   'wallpaper. The buttons inside the viewer/video change exactly these values. '
                   'There is no saturation/contrast filter: the 16 palette colours are the only colours.')
            lines = wrap_text(txt, cv.w)
            for i, ln in enumerate(lines):
                if y + i < cv.h - 2:
                    cv.put(0, y + i, ln, th.c('dim', 'win'))
            y += len(lines) + 1
        if self.section == 'media' and y + 6 < cv.h:
            self.preview(cv, 0, y, cv.w, cv.h - y)
        if self.section == 'tools' and y < cv.h:
            from ..util import wrap_text
            txt = ('opencode opens in a terminal window inside monocell. It runs locally by '
                   'default; enter user@host to run a remote instance over SSH instead. If the '
                   'SSH connection fails, the local command is tried. SSH keys must allow the '
                   'connection.')
            for i, ln in enumerate(wrap_text(txt, cv.w)):
                cv.put(0, y + i, ln, th.c('dim', 'win'))
        if self.section == 'calendar' and y < cv.h:
            from ..util import wrap_text
            txt = ('Display only. In Google Calendar open Settings > <calendar> > '
                   '"Secret address in iCal format" and paste that URL here. The Calendar app '
                   'shows those events; there are no local entries and nothing to add or delete. '
                   'Refreshes every 20 min. The address is a secret - it is stored in '
                   '~/.config/monocell/config.json (0600).')
            for i, ln in enumerate(wrap_text(txt, cv.w)):
                cv.put(0, y + i, ln, th.c('dim', 'win'))

    def preview(self, cv, x, y, w, h):
        from .. import imaging
        img = getattr(self, '_prev_img', None)
        if img is None:
            img = imaging.test_card(160, 90)
            self._prev_img = img
        key = (w, h, self.cfg['image_mode'], self.cfg['dither'], tuple(self.cfg['palette']),
               self.cfg['cell_aspect'], self.cfg.get('ascii_charset'))
        if getattr(self, '_prev_key', None) != key:
            self._prev_key = key
            self._prev_cells = imaging.render_fit(self.wm, img, w, h)
        cells, cw, chh = self._prev_cells
        cv.blit_cells(x + (w - cw) // 2, y, cells)

    def draw_about(self, cv, focused):
        th = self.th
        from .. import __version__
        from .. import logo
        bw, _, _ = logo.measure_for(cv.w, hint=False)
        _, lh = logo.draw(cv, max(0, (cv.w - bw) // 2), 1, th, hint=False)
        lines = [
            '', '%s - a text-mode desktop' % ('Monocell ' + __version__),
            'Everything is drawn with ASCII characters in 16 colours.',
            'Runs in any terminal or on the bare Linux console.', '',
            'Terminal: %dx%d cells, colour mode "%s"' % (self.wm.w, self.wm.h, self.wm.term.mode),
            'Cell aspect: %.2f' % self.wm.term.cell_aspect,
            'Config: ~/.config/monocell/config.json',
        ]
        for i, l in enumerate(lines):
            cv.put(0, 1 + lh + i, l, th.c('text' if i != 1 else 'bright', 'win'))
        button(self, cv, 0, cv.h - 1, 'Keyboard shortcuts', 'help')

    # ------------------------------------------------------------ input
    def sync_calendar(self):
        url = (self.cfg.get('gcal_url') or '').strip()
        if not url:
            self.wm.toast('Add the Google Calendar iCal address first', 'red')
            return
        self.cfg.save()
        self.wm.toast('Syncing calendar...')
        def done(res):
            events, data, err = res
            if err:
                self.wm.toast('Calendar: %s' % err, 'red')
            else:
                self.wm.toast('Calendar: %d events (%s)' % (len(events), (data or {}).get('fetched', '')),
                              'green')
        self.thread(lambda: gcal.sync(url), done)

    def cycle_opt(self, key, d=1):
        for k, label, kind in self.options():
            if k == key:
                if kind == 'action':
                    self.sync_calendar()
                elif isinstance(kind, list):
                    cur = self.cfg.get(k)
                    i = kind.index(cur) if cur in kind else -1
                    self.cfg[k] = kind[(i + d) % len(kind)]
                    self.apply()
                elif kind == 'file':
                    def ok(path, k=k):
                        self.cfg[k] = path
                        self.apply()
                    self.wm.pick_file(ok, 'open', title='Choose wallpaper', parent=self.win)
                else:
                    tf = TextField(str(self.cfg.get(k) or ''), password=kind == 'secret')
                    tf.on_enter = lambda t, k=k: self.finish_edit(k, t)
                    self.edit = (k, tf)

    def finish_edit(self, key, text):
        self.cfg[key] = text.strip()
        self.edit = None
        self.apply()

    def on_click(self, key, ev):
        if key[0] == 'sec':
            self.section = key[1]
            self.edit = None
            self.opt_sel = 0
        elif key[0] == 'slot':
            self.slot = key[1]
            self.hexfield.set(self.cfg['palette'][self.slot])
        elif key[0] == 'chan':
            self.drag_chan = key
            self.chan_set(key, ev.x)
        elif key[0] == 'nudge':
            rgb = list(self.th.rgb[self.slot])
            rgb[key[1]] = max(0, min(255, rgb[key[1]] + key[2]))
            self.set_slot_rgb(rgb)
        elif key == 'hexfield':
            self.edit = 'hex'
        elif key[0] == 'preset':
            self.load_preset(key[1])
        elif key[0] == 'role':
            self.role_menu(key[1], self.win.x + 1 + ev.x, self.win.y + 2 + ev.y)
        elif key == 'reset_roles':
            self.cfg['roles'] = dict(ROLE_DEFAULTS)
            self.apply()
        elif key[0] == 'opt':
            if self.edit and self.edit[0] == key[1]:
                return
            self.edit = None
            for i, o in enumerate(self.options()):
                if o[0] == key[1]:
                    self.opt_sel = i
            self.cycle_opt(key[1], -1 if ev.button == 2 else 1)
        elif key == 'help':
            self.wm.show_help()

    def chan_set(self, key, mx):
        _, k, bw, x0 = key
        rel = mx - x0
        v = max(0, min(255, int(rel * 255 / max(1, bw - 1) + 0.5)))
        rgb = list(self.th.rgb[self.slot])
        rgb[k] = v
        self.set_slot_rgb(rgb)

    def on_mouse(self, ev):
        if ev.kind == 'drag' and self.drag_chan:
            self.chan_set(self.drag_chan, ev.x)
            return True
        if ev.kind == 'up':
            self.drag_chan = None
        if ev.button == 2 and ev.kind == 'down':
            k = self.zone_at(ev.x, ev.y)
            if k and k[0] == 'opt':
                self.cycle_opt(k[1], -1)
                return True
        return super().on_mouse(ev)

    def on_key(self, ev):
        k = ev.key
        if self.edit == 'hex':
            if k == 'esc':
                self.edit = None
            elif not self.hexfield.key(ev):
                return False
            if k == 'enter':
                self.edit = None
            self.invalidate()
            return True
        if self.edit:
            key, tf = self.edit
            if k == 'esc':
                self.edit = None
            else:
                tf.key(ev)
            self.invalidate()
            return True
        if k in ('ctrl+pgdn', 'shift+tab', 'tab'):
            keys = [s[0] for s in SECTIONS]
            i = keys.index(self.section)
            self.section = keys[(i + (-1 if k == 'shift+tab' else 1)) % len(keys)]
            self.opt_sel = 0
        elif self.section == 'colors':
            if k in ('left', 'right', 'up', 'down'):
                d = {'left': -1, 'right': 1, 'up': -4, 'down': 4}[k]
                self.slot = (self.slot + d) % 16
                self.hexfield.set(self.cfg['palette'][self.slot])
            elif k in ('r', 'g', 'b', 'R', 'G', 'B'):
                rgb = list(self.th.rgb[self.slot])
                c = 'rgb'.index(k.lower())
                rgb[c] = max(0, min(255, rgb[c] + (8 if k.isupper() else -8)))
                self.set_slot_rgb(rgb)
            elif k == 'enter' or k == '#':
                self.edit = 'hex'
            else:
                return False
        elif self.section in OPTIONS:
            opts = self.options()
            if k == 'up':
                self.opt_sel = (self.opt_sel - 1) % len(opts)
            elif k == 'down':
                self.opt_sel = (self.opt_sel + 1) % len(opts)
            elif k in ('enter', ' ', 'right'):
                self.cycle_opt(opts[self.opt_sel][0], 1)
            elif k == 'left':
                self.cycle_opt(opts[self.opt_sel][0], -1)
            else:
                return False
        else:
            return False
        self.invalidate()
        return True

    def on_paste(self, text):
        if self.edit == 'hex':
            self.hexfield.insert(text.strip())
        elif self.edit:
            self.edit[1].insert(text.strip())
        self.invalidate()


APP = Settings
