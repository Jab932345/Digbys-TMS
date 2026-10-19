"""Dashboard test in headless Chromium with a mocked Supabase.

Checks sign-in, rendering of 6 units, and that every control sends the right
RPC with the right arguments. Needs the two CDN libraries locally:
    CDN_DIR containing supabase.js and chart.umd.js
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CDN = os.environ.get('CDN_DIR', '/tmp/cdn')
SUPA = 'https://jaajrllkozknilvmdezt.supabase.co'
fails = 0
calls = []


def check(cond, what):
    global fails
    print(('PASS ' if cond else 'FAIL ') + what, flush=True)
    if not cond:
        fails += 1


def iso(dt):
    return dt.isoformat().replace('+00:00', 'Z')


now = datetime.now(timezone.utc)
UNITS = [
    {'name': 'Main Freezer', 'location': 'Unit', 'warn_temp': -14, 'critical_temp': -10, 'active': True, 'device_id': 'tms-01', 'alarm_delay_s': 180, 'fw_target': None},
    {'name': 'Main Fridge', 'location': 'Unit', 'warn_temp': 5, 'critical_temp': 8, 'active': True, 'device_id': 'tms-02', 'alarm_delay_s': 180, 'fw_target': '2.0.1'},
    {'name': 'Walk-in Fridge', 'location': 'Unit', 'warn_temp': 5, 'critical_temp': 8, 'active': True, 'device_id': 'tms-03', 'alarm_delay_s': 180, 'fw_target': None},
    {'name': 'Meat Fridge', 'location': 'Butchery', 'warn_temp': 4, 'critical_temp': 6, 'active': True, 'device_id': 'tms-04', 'alarm_delay_s': 180, 'fw_target': None},
    {'name': 'Blast Chiller', 'location': 'Unit', 'warn_temp': 5, 'critical_temp': 8, 'active': True, 'device_id': 'tms-05', 'alarm_delay_s': 180, 'fw_target': None},
    {'name': 'Dairy <Fridge>', 'location': 'Unit', 'warn_temp': 5, 'critical_temp': 8, 'active': True, 'device_id': 'tms-06', 'alarm_delay_s': 180, 'fw_target': None},
]
LIVE = [
    {'unit_name': 'Main Freezer', 'temp': -19.2, 'alert': 'ok', 'snoozed': False, 'snooze_until': None, 'defrost_mode': False, 'defrost_until': None, 'siren': False, 'fw_version': '2.0.0', 'fw_status': None, 'rssi': -61, 'last_seen': iso(now), 'updated_at': iso(now)},
    {'unit_name': 'Main Fridge', 'temp': 9.1, 'alert': 'critical', 'snoozed': False, 'snooze_until': None, 'defrost_mode': False, 'defrost_until': None, 'siren': True, 'fw_version': '2.0.0', 'fw_status': 'installed 2.0.0', 'rssi': -70, 'last_seen': iso(now), 'updated_at': iso(now)},
    {'unit_name': 'Walk-in Fridge', 'temp': 3.0, 'alert': 'ok', 'snoozed': True, 'snooze_until': iso(now + timedelta(minutes=20)), 'snooze_source': 'unit', 'defrost_mode': False, 'defrost_until': None, 'siren': False, 'fw_version': '2.0.0', 'last_seen': iso(now), 'updated_at': iso(now)},
    {'unit_name': 'Meat Fridge', 'temp': 2.5, 'alert': 'ok', 'snoozed': False, 'snooze_until': None, 'defrost_mode': True, 'defrost_until': iso(now + timedelta(minutes=90)), 'siren': False, 'fw_version': '2.0.0', 'last_seen': iso(now), 'updated_at': iso(now)},
    {'unit_name': 'Blast Chiller', 'temp': None, 'alert': 'critical', 'snoozed': False, 'snooze_until': None, 'defrost_mode': False, 'siren': False, 'fw_version': '2.0.0', 'last_seen': iso(now - timedelta(minutes=10)), 'updated_at': iso(now - timedelta(minutes=10))},
    {'unit_name': 'Dairy <Fridge>', 'temp': 6.0, 'alert': 'warning', 'snoozed': False, 'snooze_until': None, 'defrost_mode': False, 'siren': False, 'fw_version': '2.0.0', 'last_seen': iso(now), 'updated_at': iso(now)},
]
LOGS = [{'unit_name': 'Main Fridge', 'temp': 4 + i * 0.1, 'alert': 'ok', 'created_at': iso(now - timedelta(minutes=60 - i))} for i in range(60)]
RELEASES = [{'version': '2.0.1', 'notes': 'faster sync', 'created_at': iso(now)}]


def handle(route):
    req = route.request
    url = req.url
    if url.startswith('https://cdn.jsdelivr.net/') and 'supabase' in url:
        return route.fulfill(path=os.path.join(CDN, 'supabase-supabase-js-2.45.4/dist/umd/supabase.js'), content_type='application/javascript')
    if url.startswith('https://cdnjs.cloudflare.com/') and 'chart' in url.lower():
        return route.fulfill(path=os.path.join(CDN, 'chart.js-4.4.1/dist/chart.umd.js'), content_type='application/javascript')
    if url.startswith('https://digbys-tms.test/'):
        return route.fulfill(path=os.path.join(ROOT, 'index.html'), content_type='text/html')
    if 'fonts.g' in url:
        return route.fulfill(status=200, body='', content_type='text/css')
    if not url.startswith(SUPA):
        return route.abort()
    path = url[len(SUPA):]
    if path.startswith('/auth/v1/token'):
        body = json.loads(req.post_data or '{}')
        if body.get('password') != 'right':
            return route.fulfill(status=400, content_type='application/json',
                                 body=json.dumps({'error': 'invalid_grant', 'error_description': 'Invalid login credentials'}))
        exp = int(time.time()) + 3600
        return route.fulfill(status=200, content_type='application/json', body=json.dumps({
            'access_token': 'header.' + 'eyJleHAiOiAlZH0' + '.sig', 'token_type': 'bearer', 'expires_in': 3600, 'expires_at': exp,
            'refresh_token': 'r', 'user': {'id': 'u1', 'email': body.get('email'), 'aud': 'authenticated', 'role': 'authenticated'}}))
    if path.startswith('/auth/v1/user'):
        return route.fulfill(status=200, content_type='application/json', body=json.dumps({'id': 'u1', 'aud': 'authenticated'}))
    if path.startswith('/rest/v1/rpc/'):
        fn = path[len('/rest/v1/rpc/'):].split('?')[0]
        args = json.loads(req.post_data or '{}')
        calls.append((fn, args))
        if req.headers.get('authorization', '').startswith('Bearer header.'):
            pass
        else:
            calls.append(('NOAUTH', fn))
        if fn == 'set_snooze':
            v = None if args['p_minutes'] == 0 else iso(datetime.now(timezone.utc) + timedelta(minutes=args['p_minutes']))
            return route.fulfill(status=200, content_type='application/json', body=json.dumps(v))
        if fn == 'set_defrost':
            v = iso(datetime.now(timezone.utc) + timedelta(minutes=120)) if args['p_on'] else None
            return route.fulfill(status=200, content_type='application/json', body=json.dumps(v))
        if fn == 'set_fw_target':
            return route.fulfill(status=200, content_type='application/json', body='1')
        if fn == 'update_unit_settings':
            for u in UNITS:
                if u['name'] == args['p_unit']:
                    u.update(name=args['p_name'], location=args['p_location'], warn_temp=args['p_warn'],
                             critical_temp=args['p_crit'], alarm_delay_s=args['p_delay_s'])
            return route.fulfill(status=200, content_type='application/json', body='null')
        return route.fulfill(status=404, content_type='application/json', body='{}')
    if path.startswith('/rest/v1/units'):
        return route.fulfill(status=200, content_type='application/json', body=json.dumps(UNITS))
    if path.startswith('/rest/v1/temperature_live'):
        return route.fulfill(status=200, content_type='application/json', body=json.dumps(LIVE))
    if path.startswith('/rest/v1/temperature_logs'):
        return route.fulfill(status=200, content_type='application/json', body=json.dumps(LOGS))
    if path.startswith('/rest/v1/firmware_releases'):
        return route.fulfill(status=200, content_type='application/json', body=json.dumps(RELEASES))
    route.fulfill(status=404, body='{}', content_type='application/json')


errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    import copy
    UNITS0 = copy.deepcopy(UNITS)
    for width in (1280, 390):
        UNITS[:] = copy.deepcopy(UNITS0)
        calls.clear()
        errors.clear()
        page = b.new_page(viewport={'width': width, 'height': 900})
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('console', lambda m: errors.append(m.text) if m.type == 'error' and 'WebSocket' not in m.text and '400 (Bad Request)' not in m.text and 'realtime' not in m.text else None)
        page.on('dialog', lambda d: d.accept())
        page.route('**/*', handle)
        page.goto('https://digbys-tms.test/')
        page.wait_for_selector('#login:not(.hide)')
        check(True, '[%d] sign-in shown first' % width)
        page.fill('#li-email', 'james@example.com')
        page.fill('#li-pass', 'wrong')
        page.click('.login button')
        page.wait_for_function("document.getElementById('li-msg').textContent.length>0")
        check('Invalid' in page.inner_text('#li-msg'), '[%d] wrong password rejected' % width)
        page.fill('#li-pass', 'right')
        page.click('.login button')
        page.wait_for_selector('#login.hide', state='attached')
        page.wait_for_function("document.querySelectorAll('#unit-grid .ucard').length===6")
        check(True, '[%d] 6 unit cards' % width)
        page.screenshot(path='/tmp/dash-overview-%d.png' % width, full_page=False)
        cards = {c.get_attribute('data-n'): c for c in page.query_selector_all('#unit-grid .ucard')}
        cls = {n: c.get_attribute('class') for n, c in cards.items()}
        check('critical' in cls['Main Fridge'], '[%d] critical card' % width)
        check('SIREN' in cards['Main Fridge'].inner_text(), '[%d] siren shown on card' % width)
        check('snoozed' in cls['Walk-in Fridge'] and '19:' in cards['Walk-in Fridge'].inner_text(), '[%d] unit snooze countdown shown' % width)
        check('defrost' in cls['Meat Fridge'], '[%d] defrost card' % width)
        check('offline' in cls['Blast Chiller'], '[%d] stale unit offline' % width)
        check('warning' in cls['Dairy <Fridge>'] and '<Fridge>' in cards['Dairy <Fridge>'].inner_text(), '[%d] names escaped, not HTML' % width)
        check(page.is_visible('#alert-banner') and 'Main Fridge' in page.inner_text('#alert-banner-text'), '[%d] critical banner' % width)
        stats = page.inner_text('#stat-row')
        check('5/6' in stats, '[%d] online 5/6' % width)

        # alarm sound: Main Fridge siren is due -> website alarms
        page.wait_for_timeout(700)
        check(page.evaluate('ALARMING') is True, '[%d] website alarm sounding for Main Fridge' % width)
        check(page.evaluate("AC!==null && AC.state==='running'"), '[%d] audio unlocked by sign-in tap' % width)
        page.click('#snd-btn')
        check(page.evaluate('SOUND') is False and 'OFF' in page.inner_text('#snd-btn'), '[%d] sound can be muted' % width)
        page.click('#snd-btn')
        check(page.evaluate('SOUND') is True, '[%d] sound back on' % width)

        # quick buttons on cards
        cards['Main Fridge'].query_selector('.qbtn.snz').click()
        page.wait_for_timeout(300)
        check(('set_snooze', {'p_unit': 'Main Fridge', 'p_minutes': 30, 'p_mode': 'add'}) in calls, '[%d] card snooze -> set_snooze add 30' % width)
        check('snoozed' in page.query_selector('#unit-grid .ucard[data-n="Main Fridge"]').get_attribute('class'), '[%d] card shows snoozed' % width)
        page.wait_for_timeout(700)
        check(page.evaluate('ALARMING') is False, '[%d] snooze silences website alarm' % width)
        page.query_selector('#unit-grid .ucard[data-n="Main Freezer"] .qbtn.def').click()
        page.wait_for_timeout(300)
        check(('set_defrost', {'p_unit': 'Main Freezer', 'p_on': True, 'p_minutes': 120}) in calls, '[%d] card defrost -> set_defrost on' % width)
        page.query_selector('#unit-grid .ucard[data-n="Meat Fridge"] .qbtn.def').click()
        page.wait_for_timeout(300)
        check(('set_defrost', {'p_unit': 'Meat Fridge', 'p_on': False, 'p_minutes': 120}) in calls, '[%d] card defrost toggles off' % width)

        # detail page
        page.query_selector('#unit-grid .ucard[data-n="Main Fridge"]').click()
        page.wait_for_selector('#tab-detail', state='visible')
        check(page.inner_text('#d-name') == 'Main Fridge', '[%d] detail opens' % width)
        dbg = page.evaluate("[snoozeLeft(UNITS['Main Fridge']), document.getElementById('cd-card').style.display, UNITS['Main Fridge'].snoozeUntil, document.getElementById('s-delay').value]")
        check(page.is_visible('#cd-card') and 'snooze' in page.inner_text('#cd-title').lower(), '[%d] snooze ring shown %s' % (width, dbg))
        page.click('.sp-btn[data-m="15"]')
        page.wait_for_timeout(300)
        check(('set_snooze', {'p_unit': 'Main Fridge', 'p_minutes': 15, 'p_mode': 'add'}) in calls, '[%d] 15m preset' % width)
        page.fill('#custom-mins', '45')
        page.click('.custom-btn')
        page.wait_for_timeout(300)
        check(('set_snooze', {'p_unit': 'Main Fridge', 'p_minutes': 45, 'p_mode': 'add'}) in calls, '[%d] custom snooze' % width)
        page.fill('#custom-mins', '999')
        n0 = len(calls)
        page.click('.custom-btn')
        page.wait_for_timeout(300)
        check(len(calls) == n0, '[%d] custom 999 rejected locally' % width)
        page.click('.clear-snooze')
        page.wait_for_timeout(300)
        check(('set_snooze', {'p_unit': 'Main Fridge', 'p_minutes': 0, 'p_mode': 'set'}) in calls, '[%d] clear snooze' % width)
        check(not page.is_visible('#cd-card'), '[%d] ring hidden after clear' % width)
        page.click('.toggle-wrap')
        page.wait_for_timeout(300)
        check(('set_defrost', {'p_unit': 'Main Fridge', 'p_on': True, 'p_minutes': 120}) in calls, '[%d] defrost toggle' % width)
        check(page.inner_text('#fw-ver') == '2.0.0' and 'pending' in page.inner_text('#fw-target'), '[%d] firmware panel' % width)
        check(page.inner_text('#s-delay') == '' and page.input_value('#s-delay') == '3', '[%d] delay shown in minutes' % width)

        # settings validation + save
        page.fill('#s-warn', '9')
        page.fill('#s-crit', '8')
        n0 = len(calls)
        page.click('.save-btn >> nth=0')
        page.wait_for_timeout(300)
        check(len(calls) == n0 and 'below' in page.inner_text('#save-msg'), '[%d] warn>=crit blocked' % width)
        page.fill('#s-warn', '5')
        page.fill('#s-crit', '8')
        page.fill('#s-delay', '2')
        page.click('.save-btn >> nth=0')
        page.wait_for_timeout(400)
        check(('update_unit_settings', {'p_unit': 'Main Fridge', 'p_name': 'Main Fridge', 'p_location': 'Unit', 'p_warn': 5, 'p_crit': 8, 'p_delay_s': 120}) in calls, '[%d] settings saved via RPC' % width)

        # firmware
        page.select_option('#fw-select', '2.0.1')
        page.click('text=Update this unit')
        page.wait_for_timeout(300)
        check(('set_fw_target', {'p_version': '2.0.1', 'p_units': ['Main Fridge']}) in calls, '[%d] update one unit' % width)
        page.click('text=Update all units')
        page.wait_for_timeout(300)
        check(('set_fw_target', {'p_version': '2.0.1', 'p_units': None}) in calls, '[%d] update all (after confirm)' % width)

        # banner snooze
        page.evaluate("UNITS['Main Fridge'].snoozeUntil=0;UNITS['Main Fridge'].alert='critical';UNITS['Main Fridge'].defrost=false;renderAll()")
        page.click('#alert-banner button >> nth=0')
        page.wait_for_timeout(300)
        check(('set_snooze', {'p_unit': 'Main Fridge', 'p_minutes': 30, 'p_mode': 'set'}) in calls, '[%d] banner snooze' % width)

        # HACCP tab
        page.click('.tab >> nth=2')
        page.click('text=Load records')
        page.wait_for_timeout(400)
        check('60 readings' in page.inner_text('#h-sum'), '[%d] HACCP log loads' % width)

        check(not any(c[0] == 'NOAUTH' for c in calls), '[%d] every RPC carries the signed-in token' % width)
        sw = page.evaluate('document.documentElement.scrollWidth')
        check(width != 390 or sw <= 390 + 1, '[%d] no sideways scroll (%d)' % (width, sw))
        page.screenshot(path='/tmp/dash-%d.png' % width, full_page=True)
        check(not errors, '[%d] no page errors %s' % (width, errors[:3]))
        page.close()
    b.close()

print('\n%d failure(s)' % fails)
sys.exit(1 if fails else 0)
