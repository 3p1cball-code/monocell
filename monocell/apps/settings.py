"""Settings: appearance editor (palette + element colours), desktop/media/web/calendar options."""
from ..app import App
from .. import gcal
from ..theme import PRESETS, delete_theme, save_theme, saved_themes
from ..util import fit
from ..widgets import MenuItem, TextField, button
from .appearance import AppearanceMixin

SECTIONS = [('colors', 'Appearance'), ('desktop', 'Desktop'), ('background', 'Background'),
            ('media', 'Images & video'), ('maps', 'Maps'), ('web', 'Web'),
            ('calendar', 'Calendar'), ('tools', 'Tools'),
            ('about', 'About')]

OPTIONS = {
    'desktop': [
        ('taskbar', 'Taskbar position', ['bottom', 'top']),
        ('frame', 'Window frames', ['flat', 'lines']),
        ('shadows', 'Window shadows', [False, True]),
        ('clock_24h', '24 hour clock', [False, True]),
        ('clock_seconds', 'Clock shows seconds', [False, True]),
        ('desktop_logo', 'Desktop logo', [False, True]),
        ('media_prompt', 'Ask to mount removable media', [True, False]),
        ('wallpaper', 'Wallpaper image', 'file'),
        ('symbols', 'Symbols (single-width unicode)', ['auto', 'on', 'off']),
        ('strict_ascii', 'Strict ASCII (transliterate umlauts etc.)', [False, True]),
        ('color_mode', 'Colour output (restart)', ['auto', 'truecolor', 'palette', 'linux', 'ansi']),
    ],
    'background': [
        ('background', 'Background', ['auto', 'logo', 'plain', 'image', 'matrix']),
        ('rain_density', 'Rain density', [0.5, 0.8, 1.0, 1.4, 2.0]),
        ('rain_speed', 'Rain speed', [0.6, 1.0, 1.5, 2.0]),
        ('rain_random', 'Rain randomness', [0.2, 0.5, 0.6, 0.8, 1.0]),
    ],
    'media': [
        ('image_mode', 'Image rendering', ['blend', 'pixel', 'grey', 'ascii', 'mono']),
        ('dither', 'Dithering', ['bayer4', 'bayer8', 'fs', 'none']),
        ('video_mode', 'Video rendering', ['pixel', 'blend', 'grey', 'ascii', 'mono']),
        ('ascii_charset', 'ASCII charset', ['full', 'simple', 'shades']),
        ('cell_aspect', 'Cell aspect h/w (0 = auto)', [0.0, 1.0, 1.5, 1.8, 2.0, 2.2, 2.4]),
        ('video_fps', 'Video frame rate',
         [8, 10, 12, 15, 20, 24, 25, 30, 40, 50, 60, 75, 90, 100, 120, 144, 165, 240]),
        ('av_offset', 'A/V offset ms (negative = picture earlier)',
         [-160, -120, -80, -40, 0, 40, 80, 120]),
        ('audio_sink', 'Audio output', ['auto', 'pacat', 'pw-cat', 'aplay']),
        ('gfx_look', 'Graphics mode look (g in the player)',
         ['vga', 'ega', 'theme', 'cga', 'amber', 'green']),
        ('gfx_dither', 'Graphics dithering', ['bayer', 'none']),
        ('gfx_scanlines', 'Graphics scanlines', [False, True]),
        ('gfx_fps', 'Graphics frame rate (0 = auto)', [0, 12, 15, 20, 25, 30]),
    ],
    'maps': [
        ('map_tiles', 'Tile source (preset or {z}/{x}/{y} URL)', 'text'),
        ('map_zoom', 'Zoom (2-19)', list(range(2, 20))),
    ],
    'web': [
        ('web_engine', 'Engine', ['auto', 'lite', 'chromium']),
        ('web_images', 'Show images', [False, True]),
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


class Settings(AppearanceMixin, App):
    name = 'settings'
    title = 'Settings'
    icon = '{o}'
    size = (92, 28)
    min_size = (50, 16)
    single = True

    def __init__(self, wm, win, section='colors', **kw):
        super().__init__(wm, win)
        self.cfg = wm.cfg
        self.section = section
        if self.section == 'roles':           # the old role page is part of Appearance now
            self.section = 'colors'
        self.body_x = 20
        self.edit = None          # (key, TextField) for text options
        self.drag_chan = None
        self.opt_sel = 0
        self.saved = saved_themes()   # user themes from the theme folder
        self.theme_pos = (12, 0)     # body coords of the theme button, for the popup
        self.init_appearance()

    def reopen(self, section=None, **kw):
        if section:
            self.section = 'colors' if section == 'roles' else section
        self.reload_saved()

    # ------------------------------------------------------------ apply
    def apply(self):
        self.cfg.save()
        self.wm.apply_theme()
        self.wm.load_wallpaper()

    def reload_saved(self):
        """Re-read the theme folder: it is plain files, so it can change under us."""
        self.saved = saved_themes()
        return self.saved

    def current_theme(self):
        return self.cfg.get('preset', 'Custom')

    def load_preset(self, name):
        if name in PRESETS:
            self.cfg['palette'] = list(PRESETS[name])
            self.cfg['preset'] = name
            if name == 'Matrix':
                self.cfg['background'] = 'matrix'
            elif self.cfg.get('background') == 'matrix':
                self.cfg['background'] = 'auto'
        else:
            data = self.reload_saved().get(name)
            if not data:
                self.wm.toast('No such theme: ' + name, 'red')
                return
            self.cfg['palette'] = list(data['palette'])
            self.cfg['roles'] = dict(data['roles'])
            self.cfg['layout'] = list(data['layout'])
            self.cfg['on'] = dict(data['on'])
            self.cfg['preset'] = name
        self.apply()
        self.sync_hex()

    # ------------------------------------------------------- theme files
    def theme_menu(self):
        cur = self.current_theme()
        self.reload_saved()
        items = [MenuItem(name, (lambda n=name: self.load_preset(n)), checked=cur == name)
                 for name in PRESETS]
        if self.saved:
            items.append(None)
            items += [MenuItem(name, (lambda n=name: self.load_preset(n)), checked=cur == name)
                      for name in sorted(self.saved)]
        items.append(None)
        items.append(MenuItem('Save current as...', self.save_theme_dialog))
        items.append(MenuItem('Delete "%s"' % cur, self.delete_theme_dialog,
                              enabled=cur in self.saved))
        items.append(MenuItem('Reset element colours', self.reset_elements))
        x, y = self.theme_pos
        self.wm.menu(items, self.win.x + 1 + self.body_x + x, self.win.y + 2 + y, title='Themes')

    def save_theme_dialog(self):
        cur = self.current_theme()
        default = cur if cur not in PRESETS and cur != 'Custom' else 'My theme'
        self.wm.prompt('Save theme', 'Name for this theme:', default, self.do_save_theme,
                       parent=self.win)

    def do_save_theme(self, name):
        name = ' '.join((name or '').split())
        if not name:
            self.wm.toast('A theme needs a name', 'red')
        elif name in PRESETS:
            self.wm.toast('"%s" is a built-in preset' % name, 'red')
        elif name in self.reload_saved():
            self.wm.confirm('Overwrite theme', 'Replace "%s"?' % name,
                            lambda: self.write_theme(name), yes='Replace', parent=self.win)
        else:
            self.write_theme(name)

    def write_theme(self, name):
        try:
            name = save_theme(name, self.cfg['palette'], self.th.r,
                              layout=self.th.layout, on=self.th.on)
        except (OSError, ValueError) as e:
            self.wm.toast('Theme not saved: %s' % e, 'red')
            return
        self.cfg['preset'] = name
        self.reload_saved()
        self.apply()
        self.wm.toast('Saved theme %s' % name, 'green')

    def delete_theme_dialog(self):
        cur = self.current_theme()
        if cur not in self.reload_saved():
            self.wm.toast('Only saved themes can be deleted', 'red')
            return
        self.wm.confirm('Delete theme', 'Remove "%s" from the theme folder?' % cur,
                        self.do_delete_theme, yes='Delete', parent=self.win)

    def do_delete_theme(self):
        cur = self.current_theme()
        if delete_theme(cur):
            self.cfg['preset'] = 'Custom'
            self.reload_saved()
            self.apply()
            self.wm.toast('Deleted theme %s' % cur, 'yellow')
        else:
            self.wm.toast('Could not delete %s' % cur, 'red')

    def theme_action(self, what):
        if what == 'menu':
            self.theme_menu()
        elif what == 'save':
            self.save_theme_dialog()
        elif what == 'del':
            self.delete_theme_dialog()

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
        self.zone_off = (self.body_x, 0)
        body = cv.sub(nav + 2, 0, w - nav - 2, h)
        try:
            getattr(self, 'draw_' + (self.section if self.section in ('colors', 'about') else 'options'))(body, focused)
        finally:
            self.zone_off = (0, 0)

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
        elif key[0] == 'theme':
            self.theme_action(key[1])
        elif self.section == 'colors' and self.appearance_click(key, ev):
            pass
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
            if k and self.section == 'colors' and k[0] == 'pot':
                self.appearance_click(k, ev)
                self.invalidate()
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
            if not self.appearance_key(k):
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
