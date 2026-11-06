import unittest

from monocell.bluetooth import (Session, display_name, device_tag, is_address_name,
                                numeric, primary_action, sort_key, strip_ansi, VERSIONS)

BOSE = 'AC:BF:71:CE:14:1B'


def adapter_block(s):
    s.feed('Controller C0:18:85:DC:8B:20 (public)')
    s.feed('\tAddress: C0:18:85:DC:8B:20')
    s.feed('\tManufacturer: 0x000f (15)')
    s.feed('\tVersion: 0x07 (7)')
    s.feed('\tName: X61s')
    s.feed('\tPowered: yes')
    s.feed('\tDiscoverable: no')
    s.feed('\tPairable: no')
    s.feed('\tDiscovering: no')


def device_block(s, address=BOSE, name='Bose QuietComfort Ultra'):
    s.feed('Device %s (public)' % address)
    s.feed('\tName: %s' % name)
    s.feed('\tAlias: %s' % name)
    s.feed('\tClass: 0x00240418 (2360344)')
    s.feed('\tIcon: audio-card')
    s.feed('\tPaired: yes')
    s.feed('\tTrusted: yes')
    s.feed('\tConnected: no')
    s.feed('\tRSSI: -42')
    s.feed('\tBattery Percentage: 0x64 (100)')
    s.feed('\tUUID: Audio Sink                (0000110b-0000-1000-8000-00805f9b34fb)')


class ParseTest(unittest.TestCase):
    def test_adapter(self):
        s = Session()
        adapter_block(s)
        self.assertEqual(s.adapter['address'], 'C0:18:85:DC:8B:20')
        self.assertEqual(s.adapter['name'], 'X61s')
        self.assertEqual(s.adapter['version'], 7)
        self.assertEqual(VERSIONS[s.adapter['version']], '4.1')
        self.assertTrue(s.adapter['powered'])
        self.assertFalse(s.adapter['discoverable'])
        self.assertFalse(s.scanning)

    def test_device_info(self):
        s = Session()
        adapter_block(s)
        device_block(s)
        dev = s.devices[BOSE]
        self.assertEqual(dev['name'], 'Bose QuietComfort Ultra')
        self.assertEqual(dev['alias'], 'Bose QuietComfort Ultra')
        self.assertEqual(dev['icon'], 'audio-card')
        self.assertEqual(dev['rssi'], -42)
        self.assertEqual(dev['battery'], 100)
        self.assertTrue(dev['paired'])
        self.assertTrue(dev['trusted'])
        self.assertFalse(dev['connected'])
        self.assertEqual(device_tag(dev), 'AUD')
        self.assertEqual(display_name(dev), 'Bose QuietComfort Ultra')

    def test_device_events(self):
        s = Session()
        s.feed('[NEW] Device %s Bose QC Ultra' % BOSE)
        self.assertEqual(s.devices[BOSE]['name'], 'Bose QC Ultra')
        s.feed('[CHG] Device %s RSSI: -55' % BOSE)
        self.assertEqual(s.devices[BOSE]['rssi'], -55)
        s.feed('[CHG] Device %s Connected: yes' % BOSE)
        self.assertTrue(s.devices[BOSE]['connected'])
        s.feed('[CHG] Device %s Connected: no' % BOSE)
        self.assertFalse(s.devices[BOSE]['connected'])
        s.feed('[CHG] Device %s RSSI is nil' % BOSE)
        self.assertIsNone(s.devices[BOSE]['rssi'])
        s.feed('[DEL] Device %s Bose QC Ultra' % BOSE)
        self.assertNotIn(BOSE, s.devices)

    def test_ansi_and_prompt(self):
        s = Session()
        s.feed('\x1b[0;94m[bluetoothctl]> \x1b[0m   \x1b[0;92m[NEW]\x1b[0m Device '
               '%s ScanWatch 2' % BOSE)
        self.assertEqual(s.devices[BOSE]['name'], 'ScanWatch 2')

    def test_device_prompt(self):
        s = Session()
        pad = ' ' * 79
        s.feed('\x1b[0;94m[UltraHörer]> \x1b[0m\r' + pad + '\rConnection successful')
        self.assertEqual(s.result, 'connected')
        self.assertEqual(s.result_kind, 'connect')
        s.feed('\x1b[0;94m[UltraHörer]> \x1b[0m\r' + pad + '\rDisconnection successful')
        self.assertEqual(s.result_kind, 'disconnect')

    def test_prompt_changes_mid_line(self):
        s = Session()
        pad = ' ' * 40
        s.feed('\x1b[0;94m[UltraHörer]> \x1b[0m\r\x1b[0;94m[bluetoothctl]> \x1b[0m\r'
               + pad + '\r[\x1b[0;92mNEW\x1b[0m] Device %s Bose' % BOSE)
        self.assertEqual(s.devices[BOSE]['name'], 'Bose')

    def test_prompt_prefixed_info_block(self):
        s = Session()
        s.feed('\x1b[0;94m[UltraHörer]> \x1b[0m\r   \rDevice %s (public)' % BOSE)
        s.feed('\tName: Bose')
        s.feed('\tPaired: yes')
        s.feed('\tConnected: yes')
        dev = s.devices[BOSE]
        self.assertEqual(dev['name'], 'Bose')
        self.assertTrue(dev['paired'])
        self.assertTrue(dev['connected'])

    def test_backspace_redraw(self):
        s = Session()
        s.feed('ConnectionX\x08 successful')
        self.assertEqual(s.result_kind, 'connect')

    def test_echo_is_ignored(self):
        s = Session()
        s._sent.append('scan on')
        s.feed('scan on')
        self.assertEqual(s.msg, '')
        self.assertEqual(s.adapter, {})

    def test_show_is_not_a_message(self):
        s = Session()
        adapter_block(s)
        self.assertEqual(s.msg, '')

    def test_alias_wins(self):
        s = Session()
        s.feed('[NEW] Device %s Bose' % BOSE)
        s.feed('Device %s (public)' % BOSE)
        s.feed('\tName: Bose QC')
        s.feed('\tAlias: Kopfhoerer Alex')
        self.assertEqual(display_name(s.devices[BOSE]), 'Kopfhoerer Alex')

    def test_unnamed_device(self):
        s = Session()
        s.feed('[NEW] Device 4E:87:C0:18:8F:54 4E-87-C0-18-8F-54')
        dev = s.devices['4E:87:C0:18:8F:54']
        self.assertTrue(is_address_name(dev.get('name', ''), dev['address']))
        self.assertEqual(display_name(dev), '')

    def test_junk_name_is_replaced(self):
        s = Session()
        s.feed('[NEW] Device 4E:87:C0:18:8F:54 4E-87-C0-18-8F-54')
        s.feed('[CHG] Device 4E:87:C0:18:8F:54 Alias: Speaker')
        self.assertEqual(display_name(s.devices['4E:87:C0:18:8F:54']), 'Speaker')

    def test_results(self):
        s = Session()
        s.feed('Pairing successful')
        self.assertEqual(s.result, 'paired')
        self.assertFalse(s.msg_bad)
        s.feed('Connection successful')
        self.assertEqual(s.result, 'connected')
        s.feed('Successful disconnected')
        self.assertEqual(s.result, 'disconnected')
        s.feed('Discovery started')
        self.assertEqual(s.result, 'scanning')
        self.assertTrue(s.scanning)
        s.feed('Discovery stopped')
        self.assertFalse(s.scanning)
        s.feed('Changing power on succeeded')
        self.assertFalse(s.msg_bad)
        s.feed('Device %s removed' % BOSE)
        self.assertEqual(s.result, 'forgotten')

    def test_failure(self):
        s = Session()
        s.feed('Failed to pair: org.bluez.Error.AuthenticationFailed')
        self.assertTrue(s.msg_bad)
        self.assertIn('AuthenticationFailed', s.result)
        s.feed('No default controller available')
        self.assertTrue(s.msg_bad)
        self.assertEqual(s.result, 'no Bluetooth adapter')
        self.assertEqual(s.adapter, {})

    def test_device_left(self):
        s = Session()
        s.feed('[NEW] Device %s Bose' % BOSE)
        s.feed('Device %s not available' % BOSE)
        self.assertNotIn(BOSE, s.devices)
        self.assertTrue(s.msg_bad)

    def test_failed_line_is_not_a_property(self):
        s = Session()
        adapter_block(s)
        s.feed('Failed to pair: org.bluez.Error.AuthenticationFailed')
        self.assertTrue(s.msg_bad)
        self.assertNotIn('failed to pair', s.adapter)

    def test_discovering_event(self):
        s = Session()
        s.feed('[CHG] Controller C0:18:85:DC:8B:20 Discovering: yes')
        self.assertTrue(s.scanning)
        self.assertGreater(s.scan_since, 0)
        s.feed('[CHG] Controller C0:18:85:DC:8B:20 Discovering: no')
        self.assertFalse(s.scanning)
        self.assertEqual(s.scan_since, 0)

    def test_power_off_stops_scanning(self):
        s = Session()
        s.scan(True)
        s.feed('[CHG] Controller C0:18:85:DC:8B:20 Powered: no')
        self.assertFalse(s.scanning)

    def test_result_kinds(self):
        s = Session()
        s.feed('Pairing successful')
        self.assertEqual(s.result_kind, 'pair')
        s.feed('Connection successful')
        self.assertEqual(s.result_kind, 'connect')
        s.feed('Successful disconnected')
        self.assertEqual(s.result_kind, 'disconnect')
        s.feed('Discovery started')
        self.assertEqual(s.result_kind, 'scan')
        s.feed('Changing power off succeeded')
        self.assertEqual(s.result_kind, 'power')
        s.feed('Changing discoverable on succeeded')
        self.assertEqual(s.result_kind, 'adapter')
        s.feed('Device %s removed' % BOSE)
        self.assertEqual(s.result_kind, 'remove')
        s.feed('Failed to connect: org.bluez.Error.Failed')
        self.assertEqual(s.result_kind, 'connect')
        self.assertTrue(s.msg_bad)

    def test_info_requested_for_listed_devices_only(self):
        s = Session()
        sent = []
        s.send = lambda command: sent.append(command)
        s.feed('Device %s Bose' % BOSE)
        self.assertEqual(sent, ['info ' + BOSE])
        sent[:] = []
        s.feed('Device %s (public)' % BOSE)
        self.assertEqual(sent, [])
        self.assertEqual(s.devices[BOSE].get('name'), 'Bose')

    def test_uuid_list(self):
        s = Session()
        device_block(s)
        device_block(s)
        uuids = s.devices[BOSE]['uuids']
        self.assertTrue(any('Audio Sink' in u for u in uuids))
        self.assertEqual(len(uuids), 1)


class HelpersTest(unittest.TestCase):
    def test_numeric(self):
        self.assertEqual(numeric('-42'), -42)
        self.assertEqual(numeric('0x64 (100)'), 100)
        self.assertEqual(numeric('0xffffffd6 (-42)'), -42)
        self.assertEqual(numeric('0xffffffd6'), -42)
        self.assertIsNone(numeric('nil'))
        self.assertIsNone(numeric(''))
        self.assertEqual(numeric('0x07 (7)'), 7)

    def test_strip_ansi(self):
        self.assertEqual(strip_ansi('\x1b[0;93m[CHG]\x1b[0m x\r\n'), '[CHG] x')

    def test_display_name(self):
        self.assertEqual(display_name({'address': BOSE}), '')
        self.assertEqual(display_name({'address': BOSE, 'name': 'Bose'}), 'Bose')

    def test_tag_from_uuid(self):
        dev = {'address': BOSE,
               'uuids': ['Serial Port               (00001101-0000-1000-8000-00805f9b34fb)',
                         'Audio Sink                (0000110b-0000-1000-8000-00805f9b34fb)']}
        self.assertEqual(device_tag(dev), 'AUD')
        dev['icon'] = 'input-keyboard'
        self.assertEqual(device_tag(dev), 'KBD')
        self.assertEqual(device_tag({'address': BOSE}), '')

    def test_connect_result_from_property(self):
        s = Session()
        device_block(s)
        dev = s.devices[BOSE]
        dev['connected'] = False
        s.connect(BOSE)
        seq = s.result_seq
        s.feed('[CHG] Device %s Connected: yes' % BOSE)
        self.assertGreater(s.result_seq, seq)
        self.assertEqual(s.result_kind, 'connect')

    def test_disconnect_result_from_property(self):
        s = Session()
        device_block(s)
        dev = s.devices[BOSE]
        dev['connected'] = True
        s.disconnect(BOSE)
        s.feed('[CHG] Device %s Connected: no' % BOSE)
        self.assertEqual(s.result_kind, 'disconnect')

    def test_connected_event_without_action_is_quiet(self):
        s = Session()
        device_block(s)
        seq = s.result_seq
        s.feed('[CHG] Device %s Connected: yes' % BOSE)
        self.assertEqual(s.result_seq, seq)

    def test_pair_result_from_property(self):
        s = Session()
        s.pair(BOSE)
        s.feed('[CHG] Device %s Paired: yes' % BOSE)
        self.assertEqual(s.result_kind, 'pair')

    def test_primary_action(self):
        self.assertIsNone(primary_action(None))
        self.assertEqual(primary_action({'address': BOSE}), 'pair')
        self.assertEqual(primary_action({'address': BOSE, 'paired': True}), 'connect')
        self.assertEqual(primary_action({'address': BOSE, 'paired': True,
                                         'connected': True}), 'disconnect')

    def test_sort_order(self):
        devs = [
            {'address': 'AA:AA:AA:AA:AA:AA', 'name': 'seen', 'rssi': -90},
            {'address': 'BB:BB:BB:BB:BB:BB', 'name': 'paired', 'paired': True},
            {'address': 'CC:CC:CC:CC:CC:CC', 'name': 'connected',
             'paired': True, 'connected': True},
        ]
        order = [d['name'] for d in sorted(devs, key=sort_key)]
        self.assertEqual(order, ['connected', 'paired', 'seen'])

    def test_sort_signal(self):
        devs = [
            {'address': 'AA:AA:AA:AA:AA:AA', 'name': 'weak', 'rssi': -90},
            {'address': 'BB:BB:BB:BB:BB:BB', 'name': 'strong', 'rssi': -40},
            {'address': 'CC:CC:CC:CC:CC:CC', 'name': 'unknown'},
        ]
        order = [d['name'] for d in sorted(devs, key=sort_key)]
        self.assertEqual(order, ['strong', 'weak', 'unknown'])


if __name__ == '__main__':
    unittest.main()
