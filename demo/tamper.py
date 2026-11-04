"""
Make a forged copy of the issued certificate: the holder's name is painted over
and replaced, the way someone might edit a PDF to reuse another person's
certificate. Writes demo_out/tampered.pdf plus PNG previews of both files.
"""
import hashlib
import json
import sys

import fitz  # PyMuPDF

out = sys.argv[1]
orig = f'{out}/original.pdf'
doc = fitz.open(orig)
page = doc[0]

target, replacement = 'Shahid Ali', 'Ali Raza'
hits = page.search_for(target)
if not hits:
    raise SystemExit('name not found in certificate text')

pix = page.get_pixmap(dpi=72)
for rect in hits:
    # paint over with the paper colour sampled just left of the word
    x = max(0, int(rect.x0) - 6)
    y = int((rect.y0 + rect.y1) / 2)
    r, g, b = pix.pixel(x, y)[:3]
    pad = fitz.Rect(rect.x0 - 2, rect.y0 + 1, rect.x1 + 2, rect.y1 - rect.height * 0.12)
    page.draw_rect(pad, color=None, fill=(r / 255, g / 255, b / 255), overlay=True)
    size = rect.height * 0.78
    width = fitz.get_text_length(replacement, fontname='tibi', fontsize=size)
    x0 = (rect.x0 + rect.x1) / 2 - width / 2
    page.insert_text((x0, rect.y1 - rect.height * 0.22), replacement,
                     fontname='tibi', fontsize=size, color=(0.10, 0.12, 0.20))

doc.save(f'{out}/tampered.pdf')

for name in ('original', 'tampered'):
    d = fitz.open(f'{out}/{name}.pdf')
    d[0].get_pixmap(dpi=150).save(f'{out}/{name}.png')

summary = {name: hashlib.sha256(open(f'{out}/{name}.pdf', 'rb').read()).hexdigest()
           for name in ('original', 'tampered')}
json.dump(summary, open(f'{out}/hashes.json', 'w'), indent=2)
print(json.dumps(summary, indent=2))
