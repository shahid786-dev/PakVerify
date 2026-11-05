"""
Second set of LOCAL recordings (localhost, throwaway SQLite, test accounts):
  chat_citizen.webm  - role-based assistant as a citizen
  chat_staff.webm    - same assistant as police staff (criminal-record lookup)
  admin.webm         - admin control center tabs
  explorer.webm      - blockchain explorer, chain verified
  explorer_bad.webm  - same page after one ledger row was edited in the DB
marks2.json holds per-clip timestamps (seconds from clip start).
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1])
info = json.loads((OUT / 'cert.json').read_text())
BASE = 'http://localhost:5173'
VIEW = {'width': 1600, 'height': 900}
marks = {}

ACCOUNTS = {
    'citizen': ('/login', info['citizen_cnic'], 'DemoCitizen#2026', '/citizen/dashboard'),
    'staff': ('/login/staff', info['staff_cnic'], 'DemoStaff#2026', '/staff/dashboard'),
    'admin': ('/login/admin', info['admin_cnic'], 'DemoAdmin#2026', '/admin/dashboard'),
}


def login(browser, who):
    path, cnic, pw, _ = ACCOUNTS[who]
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
    page.wait_for_url(f"**{ACCOUNTS[who][3]}**", timeout=20000)
    state = OUT / f'state_{who}.json'
    ctx.storage_state(path=str(state))
    session = page.evaluate('() => JSON.stringify({...sessionStorage})')
    ctx.close()
    return state, session


def recorded(browser, who, name, fn):
    state, session = login(browser, who)
    ctx = browser.new_context(viewport=VIEW, color_scheme='dark', storage_state=str(state),
                              record_video_dir=str(OUT / 'vid'), record_video_size=VIEW)
    page = ctx.new_page()
    t0 = time.monotonic()
    m = {}
    mark = lambda k: m.__setitem__(k, round(time.monotonic() - t0, 2))
    page.goto(BASE + '/')
    page.evaluate('(s) => { for (const [k,v] of Object.entries(JSON.parse(s))) sessionStorage.setItem(k, v); }', session)
    fn(page, mark)
    video = page.video.path()
    ctx.close()
    shutil.move(video, OUT / f'{name}.webm')
    marks[name] = m


def ask(page, mark, text, label):
    box = page.get_by_placeholder('Type your question...')
    box.click()
    box.type(text, delay=35)
    page.wait_for_timeout(300)
    mark(f'{label}_sent')
    page.locator('input[placeholder="Type your question..."] + button').click()
    page.wait_for_timeout(2600)
    mark(f'{label}_answered')


def chat(role_path, questions):
    def run(page, mark):
        page.goto(BASE + role_path)
        page.wait_for_timeout(2500)
        mark('dashboard')
        page.get_by_label('Open AI Chatbot').click()
        page.wait_for_timeout(1800)
        mark('open')
        for label, q in questions:
            ask(page, mark, q, label)
        page.wait_for_timeout(1200)
        mark('end')
    return run


def admin(page, mark):
    page.goto(BASE + '/admin/dashboard')
    page.wait_for_timeout(3500)
    mark('overview')
    for tab in ('Applications', 'Staff Management', 'Analytics', 'Audit Logs'):
        page.get_by_role('button', name=tab, exact=True).click()
        page.wait_for_timeout(2800)
        mark(tab.lower().replace(' ', '_'))
    page.wait_for_timeout(800)


def explorer(page, mark):
    page.goto(BASE + '/blockchain')
    page.wait_for_selector('text=Blockchain Integrity Verified', timeout=30000)
    page.wait_for_timeout(2500)
    mark('verified')
    page.mouse.wheel(0, 650)
    page.wait_for_timeout(2500)
    mark('ledger')
    page.get_by_placeholder('Application ID (number)…').fill(str(info['application_id']))
    page.get_by_role('button', name='Search').click()
    page.wait_for_timeout(2500)
    mark('history')
    page.mouse.wheel(0, -2000)
    page.wait_for_timeout(600)


def explorer_bad(page, mark):
    page.goto(BASE + '/blockchain')
    page.wait_for_selector('text=Chain Integrity Compromised', timeout=30000)
    page.wait_for_timeout(3500)
    mark('compromised')
    page.mouse.wheel(0, 300)
    page.wait_for_timeout(2000)
    mark('end')


TAMPER_LEDGER = """
from blockchain.models import BlockchainBlock
b = BlockchainBlock.objects.filter(action_type='CERTIFICATE_ISSUE').order_by('block_index').first()
BlockchainBlock.objects.filter(pk=b.pk).update(payload_json=b.payload_json.replace('CERT-', 'CERT-X'))
print('LEDGER_EDITED block', b.block_index)
"""

with sync_playwright() as p:
    browser = p.chromium.launch()
    recorded(browser, 'citizen', 'chat_citizen', chat('/citizen/dashboard', [
        ('track', f"Track my application {info['tracking_id']}"),
        ('lookup', f"Check criminal record for CNIC {info['criminal_cnic']}"),
    ]))
    recorded(browser, 'staff', 'chat_staff', chat('/staff/dashboard', [
        ('lookup', f"Check criminal record for CNIC {info['criminal_cnic']}"),
        ('track', f"Show application {info['tracking_id']}"),
    ]))
    recorded(browser, 'admin', 'admin', admin)
    recorded(browser, 'admin', 'explorer', explorer)
    out = subprocess.run([sys.executable, 'manage.py', 'shell', '-c', TAMPER_LEDGER],
                         cwd='backend', capture_output=True, text=True)
    (OUT / 'ledger_edit.log').write_text(out.stdout + out.stderr)
    recorded(browser, 'admin', 'explorer_bad', explorer_bad)
    browser.close()

(OUT / 'marks2.json').write_text(json.dumps(marks, indent=2))
print(json.dumps(marks, indent=2))
