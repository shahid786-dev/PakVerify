"""
HD (1920x1080) recordings on the LOCAL stack with the test accounts from seed_rich.py:
  chat_citizen / chat_staff / chat_admin : role-scoped assistant replies
  admin                                   : admin dashboard tabs
  chain                                   : blockchain explorer + record history
Writes demo_out/<scene>.webm and demo_out/hd_marks.json (times + chatbot panel box).
"""
import json
import shutil
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1])
info = json.loads((OUT / 'cert.json').read_text())
BASE = 'http://localhost:5173'
VIEW = {'width': 1920, 'height': 1080}
apps = info['applications']
marks = {}

LOGINS = {
    'citizen': ('/login', info['citizen_cnic'], 'DemoCitizen#2026'),
    'staff': ('/login/staff', info['staff_cnic'], 'DemoStaff#2026'),
    'admin': ('/login/admin', info['admin_cnic'], 'DemoAdmin#2026'),
}


def login(browser, who):
    path, cnic, pw = LOGINS[who]
    ctx = browser.new_context(viewport=VIEW, color_scheme='dark')
    page = ctx.new_page()
    page.goto(BASE + path)
    page.get_by_placeholder('CNIC (35202-XXXXXXX-X) or Email').fill(cnic)
    page.get_by_placeholder('Enter your password').fill(pw)
    page.locator('button[type=submit]').first.click()
    page.wait_for_selector('#otp-0', timeout=20000)
    for i, d in enumerate('123456'):
        page.fill(f'#otp-{i}', d)
    page.locator('button[type=submit]').first.click()
    page.wait_for_url(lambda u: '/login' not in u, timeout=20000)
    state = OUT / f'state_{who}.json'
    ctx.storage_state(path=str(state))
    session = page.evaluate('() => JSON.stringify({...sessionStorage})')
    ctx.close()
    return state, session


def scene(browser, name, who, fn):
    state, session = login(browser, who)
    ctx = browser.new_context(viewport=VIEW, color_scheme='dark', storage_state=str(state),
                              record_video_dir=str(OUT / 'vid'), record_video_size=VIEW)
    page = ctx.new_page()
    t0 = time.monotonic()
    m = marks.setdefault(name, {})
    mark = lambda k: m.__setitem__(k, round(time.monotonic() - t0, 2))
    page.goto(BASE + '/')
    page.evaluate('(s) => { for (const [k,v] of Object.entries(JSON.parse(s))) sessionStorage.setItem(k, v); }', session)
    fn(page, mark, m)
    video = page.video.path()
    ctx.close()
    shutil.move(video, OUT / f'{name}.webm')


def ask(page, mark, key, text, wait=3200):
    box = page.get_by_placeholder('Type your question...')
    box.click()
    box.type(text, delay=35)
    page.keyboard.press('Enter')
    mark(f'{key}_sent')
    page.wait_for_timeout(wait)
    mark(f'{key}_answered')


def open_chat(page, mark, m):
    page.get_by_label('Open AI Chatbot').click()
    page.wait_for_timeout(1400)
    panel = page.get_by_placeholder('Type your question...').locator(
        'xpath=ancestor::div[contains(@class,"fixed")][1]')
    try:
        m['panel'] = panel.bounding_box()
    except Exception:
        m['panel'] = None
    mark('chat_open')


def chat_citizen(page, mark, m):
    page.goto(BASE + '/citizen/dashboard')
    page.wait_for_timeout(2600)
    mark('dashboard')
    open_chat(page, mark, m)
    ask(page, mark, 'track', f"Where is my application {apps[1 - 1]['tracking_id']}?")
    ask(page, mark, 'denied', 'Check criminal record for CNIC 42301-9876543-2')
    page.wait_for_timeout(800)


def chat_staff(page, mark, m):
    page.goto(BASE + '/staff/dashboard')
    page.wait_for_timeout(2600)
    mark('dashboard')
    open_chat(page, mark, m)
    ask(page, mark, 'lookup', 'Check criminal record for CNIC 42301-9876543-2')
    ask(page, mark, 'app', f"Show application {apps[2]['tracking_id']}")
    page.wait_for_timeout(800)


def chat_admin(page, mark, m):
    page.goto(BASE + '/admin/dashboard')
    page.wait_for_timeout(2600)
    mark('dashboard')
    open_chat(page, mark, m)
    ask(page, mark, 'cert', f"Certificate {info['certificate_number']} status")
    page.wait_for_timeout(800)


def admin(page, mark, m):
    page.goto(BASE + '/admin/dashboard')
    page.wait_for_timeout(3500)
    mark('overview')
    for tab in ('Applications', 'Staff Management', 'Analytics', 'Audit Logs'):
        page.get_by_role('button', name=tab, exact=True).first.click()
        page.wait_for_timeout(2800)
        mark(tab)
    page.wait_for_timeout(600)


def chain(page, mark, m):
    page.goto(BASE + '/blockchain')
    page.wait_for_timeout(3500)
    mark('stats')
    page.mouse.wheel(0, 650)
    page.wait_for_timeout(2600)
    mark('ledger')
    page.mouse.wheel(0, -650)
    page.wait_for_timeout(900)
    box = page.get_by_placeholder('Application ID (number)…')
    box.click()
    box.type(str(apps[0]['id']), delay=120)
    page.get_by_role('button', name='Search').click()
    page.wait_for_timeout(1800)
    mark('history')
    page.mouse.wheel(0, 380)
    page.wait_for_timeout(2800)
    mark('history_scrolled')


with sync_playwright() as p:
    browser = p.chromium.launch()
    for name, who, fn in (('chat_citizen', 'citizen', chat_citizen), ('chat_staff', 'staff', chat_staff),
                          ('chat_admin', 'admin', chat_admin), ('admin', 'admin', admin),
                          ('chain', 'admin', chain)):
        try:
            scene(browser, name, who, fn)
        except Exception as exc:
            marks.setdefault(name, {})['error'] = repr(exc)
    browser.close()

(OUT / 'hd_marks.json').write_text(json.dumps(marks, indent=2))
print(json.dumps(marks, indent=2))
