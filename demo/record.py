"""
Records the Certificate Integrity Check page on the LOCAL dev stack
(localhost only, test officer created by seed_and_issue.py):
  1) original PDF  -> integrity verified
  2) edited PDF    -> hash mismatch (tampered)
Writes demo_out/integrity.webm, screenshots and marks.json (seconds from start).
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
VIEW = {'width': 1600, 'height': 900}

with sync_playwright() as p:
    browser = p.chromium.launch()

    # sign in once (not recorded): test officer + local OTP bypass
    ctx = browser.new_context(viewport=VIEW)
    page = ctx.new_page()
    page.goto(f'{BASE}/login/staff')
    page.get_by_placeholder('CNIC (35202-XXXXXXX-X) or Email').fill(info['staff_cnic'])
    page.get_by_placeholder('Enter your password').fill('DemoStaff#2026')
    page.locator('button[type=submit]').first.click()
    page.wait_for_selector('#otp-0', timeout=20000)
    for i, d in enumerate('123456'):
        page.fill(f'#otp-{i}', d)
    page.locator('button[type=submit]').first.click()
    page.wait_for_url('**/staff/**', timeout=20000)
    ctx.storage_state(path=str(OUT / 'state.json'))
    session = page.evaluate('() => JSON.stringify({ls: {...localStorage}, ss: {...sessionStorage}})')
    ctx.close()

    rec = browser.new_context(viewport=VIEW, storage_state=str(OUT / 'state.json'),
                              record_video_dir=str(OUT / 'vid'), record_video_size=VIEW)
    page = rec.new_page()
    t0 = time.monotonic()
    marks = {}
    mark = lambda k: marks.__setitem__(k, round(time.monotonic() - t0, 2))

    # sessionStorage is per tab: restore the active-role marker
    page.goto(f'{BASE}/staff/dashboard')
    page.evaluate('(s) => { const o = JSON.parse(s); for (const [k,v] of Object.entries(o.ss)) sessionStorage.setItem(k, v); }', session)
    page.goto(f'{BASE}/staff/certificate-integrity')
    page.wait_for_selector('text=Certificate Integrity Check', timeout=30000)
    page.wait_for_timeout(1500)
    mark('page_ready')

    def run_check(pdf_name, label):
        box = page.get_by_placeholder('CERT-2026-001234 or QR hash')
        box.fill('')
        box.type(info['certificate_number'], delay=60)
        page.locator('input[type=file]').set_input_files(str(OUT / pdf_name))
        page.wait_for_timeout(900)
        mark(f'{label}_submit')
        page.get_by_role('button', name='Verify Certificate Integrity').click()
        page.wait_for_selector('text=Awaiting check', state='detached', timeout=30000)
        page.wait_for_timeout(800)
        mark(f'{label}_result')
        page.screenshot(path=str(OUT / f'{label}.jpg'), type='jpeg', quality=92)
        page.mouse.wheel(0, 380)
        page.wait_for_timeout(2600)
        page.screenshot(path=str(OUT / f'{label}_scrolled.jpg'), type='jpeg', quality=92)
        page.mouse.wheel(0, -380)
        page.wait_for_timeout(800)
        mark(f'{label}_end')

    run_check('original.pdf', 'verified')
    page.reload()
    page.evaluate('(s) => { const o = JSON.parse(s); for (const [k,v] of Object.entries(o.ss)) sessionStorage.setItem(k, v); }', session)
    page.wait_for_selector('text=Certificate Integrity Check', timeout=30000)
    page.wait_for_timeout(1000)
    mark('second_ready')
    run_check('tampered.pdf', 'tampered')
    page.wait_for_timeout(1500)

    video = page.video.path()
    rec.close()
    shutil.move(video, OUT / 'integrity.webm')
    (OUT / 'marks.json').write_text(json.dumps(marks, indent=2))
    browser.close()
    print(json.dumps(marks, indent=2))
