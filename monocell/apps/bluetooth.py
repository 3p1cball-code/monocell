"""Bluetooth: adapter state, discovery, pairing, connecting and forgetting devices."""
import time

from ..app import App
from ..bluetooth import Session, device_tag, display_name, primary_action, sort_key, VERSIONS
from ..util import fit, which
from ..widgets import ListView, button


class Bluetooth(App):
    name = 'bluetooth'
    title = 'Bluetooth'
    icon = '[B]'
    size = (80, 24)
    min_size = (48, 12)
    single = True

    def __init__(self, wm, win, **kw):
        super().__init__(wm, win)
        self.list = ListView()
        self.list.on_activate = self.primary
        self.s = None
        self.err = ''
        self.busy = ''
        self.note = ''
        self.note_bad = False
        self._seq = 0
        self._expect = None
        self._last_res = 0
        self._deadline = 0.0
        self._rev = -1
        self.installed = bool(which('bluetoothctl'))
        if self.installed:
            try:
                self.s = Session()
                self.s.start()
                self.s.scan(True)
            except Exception as e:
                self.err = str(e)
                self.s = None
        self.every(0.5, self.poll)
        self.every(4, self.tick)

    # ------------------------------------------------------------ helpers
    @property
    def sel(self):
        return self.list.current()

    def devices(self):
        if not self.s:
            return []
        return sorted(self.s.devices.values(), key=sort_key)

    def sync(self):
        items = self.devices()
        addr = (self.sel or {}).get('address', '')
        self.list.set_items(items, keep=True)
        if addr:
            for i, dev in enumerate(items):
                if dev.get('address') == addr:
                    self.list.sel = i
                    break
        self.list.clamp()

    def act(self, call, msg, wait=20.0, kind=None):
        if self.busy or not self.s:
            return
        self.busy = msg
        self.err = ''
        self.note = ''
        self.note_bad = False
        self._seq = self.s.result_seq
        self._expect = kind
        self._deadline = time.monotonic() + wait if wait else 0.0
        call()
        if not wait:
            self.busy = ''
            self._expect = None
        self.invalidate()

    # ------------------------------------------------------------ actions
    def primary(self, dev=None):
        dev = dev or self.sel
        action = primary_action(dev)
        if not action:
            return
        addr = dev['address']
        if action == 'pair':
            self.act(lambda: self.s.pair(addr),
                     'pairing with %s...' % (display_name(dev) or addr), 30, 'pair')
        elif action == 'connect':
            self.act(lambda: self.s.connect(addr), 'connecting...', 25, 'connect')
        else:
            self.act(lambda: self.s.disconnect(addr), 'disconnecting...', 15, 'disconnect')

    def connect(self):
        dev = self.sel
        if dev:
            self.act(lambda: self.s.connect(dev['address']), 'connecting...', 25, 'connect')

    def disconnect(self):
        dev = self.sel
        if dev:
            self.act(lambda: self.s.disconnect(dev['address']), 'disconnecting...', 15,
                     'disconnect')

    def toggle_trust(self):
        dev = self.sel
        if dev:
            self.s.trust(dev['address'], not dev.get('trusted'))
            self.note = 'trusted' if dev.get('trusted') else 'trust removed'
            self.note_bad = False
            self.invalidate()

    def forget(self):
        dev = self.sel
        if not dev:
            return
        name = display_name(dev) or dev['address']
        self.wm.confirm('Forget device',
                        'Remove "%s" from the list and delete its pairing?' % name,
                        lambda: self.act(lambda: self.s.remove(dev['address']),
                                         'forgetting...', 12, 'remove'),
                        yes='Forget', parent=self.win)

    def toggle_scan(self):
        if not self.s:
            return
        on = not self.s.scanning
        self.act(lambda: self.s.scan(on), 'scanning...' if on else 'stopping scan...', 0)

    def toggle_power(self):
        if not self.s:
            return
        on = not self.s.adapter.get('powered', True)
        self.act(lambda: self.s.power(on),
                 'switching radio %s...' % ('on' if on else 'off'), 12, 'power')

    def toggle_discoverable(self):
        if not self.s:
            return
        if self.busy:
            return
        on = not self.s.adapter.get('discoverable')
        self.act(lambda: self.s.discoverable(on), '', 0)
        self.note = 'discoverable for 3 minutes' if on else 'no longer discoverable'
        self.note_bad = False
        self.invalidate()

    def info(self):
        dev = self.sel
        if not dev:
            return
        name = display_name(dev) or dev['address']
        tag = device_tag(dev)
        lines = ['%s %s' % (name, '(%s)' % tag if tag else ''),
                 'Address: ' + dev['address']]
        if dev.get('icon'):
            lines.append('Kind:    ' + dev['icon'])
        if dev.get('class'):
            lines.append('Class:   ' + dev['class'])
        lines.append('  '.join('%s: %s' % (key, 'yes' if dev.get(key) else 'no')
                               for key in ('paired', 'trusted', 'connected')))
        extra = []
        if dev.get('rssi') is not None:
            extra.append('signal %d dBm' % dev['rssi'])
        if dev.get('battery') is not None:
            extra.append('battery %d %%' % dev['battery'])
        if extra:
            lines.append('  ·  '.join(extra))
        if dev.get('adapter'):
            lines.append('Adapter: ' + dev['adapter'])
        if dev.get('uuids'):
            lines.append('Profiles: %d' % len(dev['uuids']))
        lines += ['', 'enter pair/connect · t trust · x forget']
        self.wm.message(name, '\n'.join(lines), parent=self.win)

    # ------------------------------------------------------------ polling
    def poll(self):
        if not self.s:
            return
        if self.s.stopped:
            if not self.busy and not self.note_bad:
                self.note = 'bluetoothd connection lost - press r to restart'
                self.note_bad = True
                self.invalidate()
            return
        if self.s.result_seq > self._last_res:
            self._last_res = self.s.result_seq
            if (self.s.result in ('scanning', 'scan stopped')
                    or self.s.result.startswith('Changing ')):
                self.note = ''
            else:
                self.note = self.s.result
                self.note_bad = self.s.msg_bad
            self.invalidate()
        if self._deadline:
            done = (self.s.result_seq > self._seq
                    and (self._expect is None or self.s.result_kind == self._expect))
            if done or time.monotonic() > self._deadline:
                self.busy = ''
                self._expect = None
                self._deadline = 0.0
                self.invalidate()
        if (self.s.scanning and self.s.scan_since
                and time.monotonic() - self.s.scan_since > 90):
            self.s.scan(False)
        if self.s.rev != self._rev:
            self._rev = self.s.rev
            self.sync()
            self.invalidate()

    def tick(self):
        if self.s:
            self.s.refresh(full=self.s.scanning)

    # ------------------------------------------------------------ draw
    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        cv.put(1, 0, 'Bluetooth', th.c('accent', 'win', 1))
        if not self.installed:
            cv.put(1, 2, 'BlueZ (bluetoothctl) is not installed.', th.c('bright', 'win'))
            cv.put(1, 4, 'On Debian: sudo apt install bluez', th.c('dim', 'win'))
            return
        ad = self.s.adapter if self.s else {}
        powered = bool(ad.get('powered'))
        cv.put(11, 0, ' %s ' % ('on' if powered else 'off'),
               th.c('on_accent' if powered else 'shadow', 'accent' if powered else 'dim'))
        version = VERSIONS.get(ad.get('version'), '')
        right = ('BT ' + version if version else 'BT') + '  ' + (ad.get('address') or 'no adapter')
        cv.put(max(17, w - len(right) - 1), 0, right, th.c('dim', 'win'))

        name = ad.get('name') or ad.get('alias') or 'adapter'
        if not ad:
            line = 'no adapter found'
        else:
            bits = []
            if ad.get('pairable'):
                bits.append('pairable')
            if ad.get('discoverable'):
                bits.append('discoverable')
            line = name + (' · ' + ' · '.join(bits) if bits else '')
        cv.put(1, 1, fit(line, w - 2), th.c('text' if powered else 'dim', 'win'))

        scanning = bool(self.s and self.s.scanning)
        x = 1
        x += button(self, cv, x, 3, 'Stop' if scanning else 'Scan', 'scan', active=scanning) + 1
        x += button(self, cv, x, 3, 'Power off' if powered else 'Power on', 'power',
                    enabled=bool(ad) and not self.busy) + 1
        dev = self.sel
        action = primary_action(dev) if dev else None
        if action:
            label = {'pair': 'Pair', 'connect': 'Connect', 'disconnect': 'Disconnect'}[action]
            x += button(self, cv, x, 3, label, 'primary', primary=True,
                        enabled=not self.busy) + 1
        if dev:
            x += button(self, cv, x, 3,
                        'Untrust' if dev.get('trusted') else 'Trust', 'trust',
                        enabled=not self.busy) + 1
            x += button(self, cv, x, 3, 'Forget', 'forget', enabled=not self.busy) + 1
            x += button(self, cv, x, 3, 'Info', 'info') + 1

        n = len(self.list.items)
        count = '%d device%s' % (n, '' if n == 1 else 's')
        if scanning:
            count += ' · scanning'
        cv.put(1, 4, count, th.c('accent' if scanning else 'dim', 'win'))

        def row(cv2, rx, ry, rw, item, idx, selected):
            a = th.c('on_accent', 'accent') if selected and focused else \
                (th.c('bright', 'sel') if selected else th.c('text', 'win'))
            if selected:
                cv2.fill(rx, ry, rw, 1, a)
            if item.get('connected'):
                mark, mc = '*', th.c('green', 'win')
            elif item.get('paired'):
                mark, mc = '+', th.c('accent', 'win')
            else:
                mark, mc = ' ', th.c('dim', 'win')
            cv2.put(rx, ry, mark, a if selected else mc)
            cv2.put(rx + 2, ry, '%-3s' % device_tag(item), a if selected else th.c('dim', 'win'))
            rssi = item.get('rssi')
            bat = item.get('battery')
            name = display_name(item)
            aw = 18 if name else 0
            rw_right = aw + (5 if rssi is not None else 0) + (5 if bat is not None else 0)
            namew = max(4, rw - 6 - rw_right)
            if name:
                cv2.put(rx + 6, ry, fit(name, namew), a)
            else:
                cv2.put(rx + 6, ry, fit(item['address'], namew),
                        a if selected else th.c('dim', 'win'))
            ax = rx + rw - rw_right
            if name:
                cv2.put(ax, ry, item['address'], a if selected else th.c('dim', 'win'))
                ax += 18
            if rssi is not None:
                col = 'green' if rssi >= -60 else 'yellow' if rssi >= -80 else 'red'
                cv2.put(ax, ry, '%4d' % rssi, a if selected else th.c(col, 'win'))
                ax += 5
            if bat is not None:
                col = 'green' if bat >= 40 else 'yellow' if bat >= 20 else 'red'
                cv2.put(ax, ry, '%3d%%' % bat, a if selected else th.c(col, 'win'))
        self.list.draw(cv, 0, 5, w, max(1, h - 6), row, th)
        self.zone(0, 5, w, max(1, h - 6), 'list')

        msg = self.busy or self.err or self.note
        hint = 'enter pair/connect  s scan  w power  t trust  x forget  i info  d discoverable'
        if msg:
            bad = bool(self.err) or (self.note_bad and not self.busy)
            cv.put(1, h - 1, fit(msg, w - 2), th.c('red' if bad else 'dim', 'win'))
        else:
            cv.put(1, h - 1, fit(hint, w - 2), th.c('dim', 'win'))

    # ------------------------------------------------------------ input
    def on_click(self, key, ev):
        if key == 'scan':
            self.toggle_scan()
        elif key == 'power':
            self.toggle_power()
        elif key == 'primary':
            self.primary()
        elif key == 'trust':
            self.toggle_trust()
        elif key == 'forget':
            self.forget()
        elif key == 'info':
            self.info()
        elif key == 'list':
            self.pressed = 'list'

    def on_mouse(self, ev):
        k = self.zone_at(ev.x, ev.y)
        if k == 'list' or (ev.kind in ('wheelup', 'wheeldown') and k is not None):
            if self.hover is not None:
                self.hover = None
                self.invalidate()
            if self.list.mouse(ev, ev.y - 5):
                self.invalidate()
                return True
            return True
        return super().on_mouse(ev)

    def on_key(self, ev):
        k = ev.key
        if k == 's':
            self.toggle_scan()
        elif k == 'w':
            self.toggle_power()
        elif k == 't':
            self.toggle_trust()
        elif k == 'x':
            self.forget()
        elif k == 'i':
            self.info()
        elif k == 'd':
            self.toggle_discoverable()
        elif k == 'c':
            self.connect()
        elif k == 'u':
            self.disconnect()
        elif k == 'r':
            if self.s.stopped or self.s is None:
                self.restart()
            else:
                self.act(lambda: self.s.refresh(full=True), 'refreshing...', 0)
        elif k == 'enter' and not self.list.items:
            return False
        elif self.list.key(ev):
            pass
        else:
            return False
        self.invalidate()
        return True

    def on_close(self):
        if self.s:
            self.s.scan(False)
            self.s.stop()
            self.s = None
        return True


APP = Bluetooth
