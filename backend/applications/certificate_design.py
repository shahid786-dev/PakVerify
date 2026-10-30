"""
PakVerify certificate design (vector, generated entirely in code).

An original A4 portrait certificate in the style of security paper:
guilloche border and watermark rosette, an original PakVerify seal,
the citizen's details, a details panel, digital approval blocks and a
QR / blockchain verification panel. No third-party logos, seals or
handwritten signatures are used.

render_certificate(values, verification_url) -> PDF bytes
"""

import io
import math
import os

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = A4

NAVY = HexColor('#14306B')
NAVY_DARK = HexColor('#0A1A3D')
RED = HexColor('#B3202E')
GOLD = HexColor('#B8892F')
GOLD_LIGHT = HexColor('#D9B76A')
INK = HexColor('#1E2A33')
MUTED = HexColor('#5B6670')
PAPER = HexColor('#FBF8F0')

_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'certificate_assets', 'fonts')
_fonts_ready = False


def _register_fonts():
    """Use bundled Poppins for labels; fall back to Helvetica if the files are missing."""
    global _fonts_ready, SANS, SANS_MED, SANS_BOLD, SANS_LIGHT
    if _fonts_ready:
        return
    SANS, SANS_MED, SANS_BOLD, SANS_LIGHT = 'Helvetica', 'Helvetica', 'Helvetica-Bold', 'Helvetica'
    try:
        for name, file in (('PV-Sans', 'Poppins-Regular.ttf'), ('PV-Sans-Medium', 'Poppins-Medium.ttf'),
                           ('PV-Sans-Bold', 'Poppins-Bold.ttf'), ('PV-Sans-Light', 'Poppins-Light.ttf')):
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, os.path.join(_FONT_DIR, file)))
        SANS, SANS_MED, SANS_BOLD, SANS_LIGHT = 'PV-Sans', 'PV-Sans-Medium', 'PV-Sans-Bold', 'PV-Sans-Light'
    except Exception:
        pass
    _fonts_ready = True


SANS = SANS_MED = SANS_BOLD = SANS_LIGHT = 'Helvetica'
SERIF_BOLD = 'Times-Bold'
SERIF_ITALIC = 'Times-Italic'
SERIF_BOLD_ITALIC = 'Times-BoldItalic'
SERIF = 'Times-Roman'


def _alpha(color, a):
    return Color(color.red, color.green, color.blue, alpha=a)


def _fit(c, text, font, size, max_width, min_size=7):
    while size > min_size and c.stringWidth(text, font, size) > max_width:
        size -= 0.5
    return size


def _centered(c, text, y, font, size, color=INK, max_width=None, cx=PAGE_W / 2):
    if max_width:
        size = _fit(c, text, font, size, max_width)
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawCentredString(cx, y, text)
    return size


def _spaced(c, text, x, y, font, size, spacing, color, anchor='middle'):
    """Letter-spaced text (small caps style headings)."""
    width = sum(c.stringWidth(ch, font, size) for ch in text) + spacing * (len(text) - 1)
    start = x - width / 2 if anchor == 'middle' else x
    c.setFont(font, size)
    c.setFillColor(color)
    pos = start
    for ch in text:
        c.drawString(pos, y, ch)
        pos += c.stringWidth(ch, font, size) + spacing
    return width


# ── Security-paper ornaments ─────────────────────────────────────────────────

def _guilloche_band(c, x0, y0, x1, y1, band):
    """Interlaced sine waves running round the frame between two rectangles."""
    mid = band / 2
    c.saveState()
    c.setLineWidth(0.35)
    for k, (amp, freq, phase, col) in enumerate([
        (mid * 0.85, 0.11, 0.0, _alpha(NAVY, 0.55)),
        (mid * 0.85, 0.11, math.pi, _alpha(NAVY, 0.55)),
        (mid * 0.55, 0.22, math.pi / 2, _alpha(GOLD, 0.65)),
        (mid * 0.55, 0.22, 3 * math.pi / 2, _alpha(GOLD, 0.65)),
    ]):
        c.setStrokeColor(col)
        # top & bottom edges
        for yc in (y1 - mid, y0 + mid):
            p = c.beginPath()
            steps = int((x1 - x0) / 1.5)
            for i in range(steps + 1):
                x = x0 + band + (x1 - x0 - 2 * band) * i / steps
                y = yc + amp * math.sin(freq * (x - x0) + phase)
                p.moveTo(x, y) if i == 0 else p.lineTo(x, y)
            c.drawPath(p, stroke=1, fill=0)
        # left & right edges
        for xc in (x0 + mid, x1 - mid):
            p = c.beginPath()
            steps = int((y1 - y0) / 1.5)
            for i in range(steps + 1):
                y = y0 + band + (y1 - y0 - 2 * band) * i / steps
                x = xc + amp * math.sin(freq * (y - y0) + phase)
                p.moveTo(x, y) if i == 0 else p.lineTo(x, y)
            c.drawPath(p, stroke=1, fill=0)
    c.restoreState()


def _rosette(c, cx, cy, radius, petals=12, color=GOLD, width=0.5, alpha=1.0, loops=3):
    """Spirograph rosette (epitrochoid) — used for corners, seal and watermark."""
    c.saveState()
    c.setStrokeColor(_alpha(color, alpha))
    c.setLineWidth(width)
    for j in range(loops):
        R = radius * (1 - 0.18 * j)
        r = R / petals
        d = r * 2.2
        p = c.beginPath()
        steps = 720
        for i in range(steps + 1):
            t = 2 * math.pi * i / steps
            x = cx + (R - r) * math.cos(t) * 0.82 + d * math.cos((R - r) / r * t) * 0.82
            y = cy + (R - r) * math.sin(t) * 0.82 - d * math.sin((R - r) / r * t) * 0.82
            p.moveTo(x, y) if i == 0 else p.lineTo(x, y)
        c.drawPath(p, stroke=1, fill=0)
    c.restoreState()


def _microtext(c, text, x0, x1, y, size=3.2, color=NAVY):
    c.saveState()
    c.setFont(SANS, size)
    c.setFillColor(_alpha(color, 0.55))
    unit = text + '  ·  '
    w = c.stringWidth(unit, SANS, size)
    x = x0
    while x + w < x1:
        c.drawString(x, y, unit)
        x += w
    c.restoreState()


def _text_on_circle(c, text, cx, cy, radius, font, size, color, start_deg=90, top=True):
    """Draw text around a circle (clockwise across the top)."""
    c.saveState()
    c.setFont(font, size)
    c.setFillColor(color)
    widths = [c.stringWidth(ch, font, size) for ch in text]
    total_angle = sum(widths) / radius
    angle = math.radians(start_deg) + (total_angle / 2 if top else -total_angle / 2)
    for ch, w in zip(text, widths):
        half = (w / 2) / radius
        angle_c = angle - half if top else angle + half
        x = cx + radius * math.cos(angle_c)
        y = cy + radius * math.sin(angle_c)
        c.saveState()
        c.translate(x, y)
        c.rotate(math.degrees(angle_c) - 90 if top else math.degrees(angle_c) + 90)
        c.drawCentredString(0, 0, ch)
        c.restoreState()
        angle = angle - 2 * half if top else angle + 2 * half
    c.restoreState()


def _shield(c, cx, cy, w, h, fill, stroke, line=1.0):
    p = c.beginPath()
    p.moveTo(cx - w / 2, cy + h / 2)
    p.lineTo(cx + w / 2, cy + h / 2)
    p.lineTo(cx + w / 2, cy - h * 0.05)
    p.curveTo(cx + w / 2, cy - h * 0.32, cx + w * 0.2, cy - h * 0.45, cx, cy - h / 2)
    p.curveTo(cx - w * 0.2, cy - h * 0.45, cx - w / 2, cy - h * 0.32, cx - w / 2, cy - h * 0.05)
    p.close()
    c.setFillColor(fill)
    c.setStrokeColor(stroke)
    c.setLineWidth(line)
    c.drawPath(p, stroke=1, fill=1)


def _check(c, cx, cy, s, color, width=2.2):
    c.saveState()
    c.setStrokeColor(color)
    c.setLineWidth(width)
    c.setLineCap(1)
    c.setLineJoin(1)
    p = c.beginPath()
    p.moveTo(cx - s * 0.5, cy)
    p.lineTo(cx - s * 0.12, cy - s * 0.38)
    p.lineTo(cx + s * 0.55, cy + s * 0.42)
    c.drawPath(p, stroke=1, fill=0)
    c.restoreState()


def _star_path(c, cx, cy, outer, inner, points, rotation=90):
    p = c.beginPath()
    for i in range(points * 2 + 1):
        ang = math.radians(rotation) + math.pi * i / points
        rad = outer if i % 2 == 0 else inner
        x, y = cx + rad * math.cos(ang), cy + rad * math.sin(ang)
        p.moveTo(x, y) if i == 0 else p.lineTo(x, y)
    p.close()
    return p


def _laurel(c, cx, cy, r, color, side):
    """A simple laurel branch on one side of a badge (side = -1 left, +1 right)."""
    c.saveState()
    c.setFillColor(color)
    c.setStrokeColor(color)
    c.setLineWidth(r * 0.03)
    p = c.beginPath()
    for i in range(41):
        t = math.radians(-120 + 105 * i / 40)
        x = cx + side * r * math.cos(t) * -1
        y = cy + r * math.sin(t)
        p.moveTo(x, y) if i == 0 else p.lineTo(x, y)
    c.drawPath(p, stroke=1, fill=0)
    for k in range(7):
        t = math.radians(-112 + 14 * k)
        x = cx - side * r * math.cos(t)
        y = cy + r * math.sin(t)
        c.saveState()
        c.translate(x, y)
        c.rotate(math.degrees(t) + (90 if side < 0 else -90) + side * 35)
        c.ellipse(-r * 0.05, 0, r * 0.05, r * 0.2, stroke=0, fill=1)
        c.restoreState()
    c.restoreState()


def _emblem(c, cx, cy, r):
    """Original police-style badge: 8-point gold star, navy ring text, blue/red shield, star and laurels."""
    c.saveState()
    # 8-point badge star
    c.setFillColor(GOLD)
    c.setStrokeColor(GOLD)
    c.setLineWidth(0.6)
    c.drawPath(_star_path(c, cx, cy, r * 1.16, r * 0.9, 16, 90), stroke=1, fill=1)
    c.setFillColor(GOLD_LIGHT)
    c.drawPath(_star_path(c, cx, cy, r * 1.04, r * 0.9, 16, 90), stroke=0, fill=1)
    for i in range(16):
        ang = math.radians(90 + 360 / 16 * i)
        c.setFillColor(GOLD)
        c.circle(cx + r * 1.17 * math.cos(ang), cy + r * 1.17 * math.sin(ang), r * 0.04, stroke=0, fill=1)
    # navy ring
    c.setFillColor(NAVY_DARK)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.2)
    c.circle(cx, cy, r * 0.8, stroke=1, fill=1)
    c.setStrokeColor(GOLD_LIGHT)
    c.setLineWidth(0.5)
    c.circle(cx, cy, r * 0.54, stroke=1, fill=0)
    _text_on_circle(c, 'POLICE VERIFICATION', cx, cy, r * 0.62, SANS_BOLD, r * 0.115, GOLD_LIGHT, 90, True)
    _text_on_circle(c, 'PAKVERIFY', cx, cy, r * 0.65, SANS_BOLD, r * 0.1, GOLD_LIGHT, 270, False)
    for ang in (200, 340):
        c.setFillColor(GOLD_LIGHT)
        c.circle(cx + r * 0.67 * math.cos(math.radians(ang)), cy + r * 0.67 * math.sin(math.radians(ang)), r * 0.03, stroke=0, fill=1)
    # blue / red shield
    sw, sh = r * 0.62, r * 0.72
    c.saveState()
    p = c.beginPath()
    p.moveTo(cx - sw / 2, cy + sh / 2)
    p.lineTo(cx + sw / 2, cy + sh / 2)
    p.lineTo(cx + sw / 2, cy - sh * 0.05)
    p.curveTo(cx + sw / 2, cy - sh * 0.32, cx + sw * 0.2, cy - sh * 0.45, cx, cy - sh / 2)
    p.curveTo(cx - sw * 0.2, cy - sh * 0.45, cx - sw / 2, cy - sh * 0.32, cx - sw / 2, cy - sh * 0.05)
    p.close()
    c.clipPath(p, stroke=0, fill=0)
    c.setFillColor(NAVY)
    c.rect(cx - sw / 2, cy, sw, sh / 2, stroke=0, fill=1)
    c.setFillColor(RED)
    c.rect(cx - sw / 2, cy - sh / 2, sw, sh / 2, stroke=0, fill=1)
    c.restoreState()
    _shield(c, cx, cy, sw, sh, Color(0, 0, 0, alpha=0), GOLD_LIGHT, 1.0)
    c.setFillColor(GOLD_LIGHT)
    c.drawPath(_star_path(c, cx, cy + sh * 0.18, r * 0.13, r * 0.055, 5, 90), stroke=0, fill=1)
    c.setStrokeColor(GOLD_LIGHT)
    c.setLineWidth(r * 0.03)
    c.line(cx - sw * 0.28, cy - sh * 0.02, cx + sw * 0.28, cy - sh * 0.02)
    _check(c, cx, cy - sh * 0.2, r * 0.2, GOLD_LIGHT, width=r * 0.05)
    c.restoreState()


def _seal(c, cx, cy, r, year):
    """Round 'police verified' stamp in navy and red."""
    c.saveState()
    teeth = 48
    p = c.beginPath()
    for i in range(teeth * 2 + 1):
        ang = math.pi * i / teeth
        rad = r * (0.9 if i % 2 == 0 else 0.84)
        x, y = cx + rad * math.cos(ang), cy + rad * math.sin(ang)
        p.moveTo(x, y) if i == 0 else p.lineTo(x, y)
    p.close()
    c.setFillColor(PAPER)
    c.setStrokeColor(NAVY)
    c.setLineWidth(0.8)
    c.drawPath(p, stroke=1, fill=1)
    c.setStrokeColor(RED)
    c.setFillColor(_alpha(RED, 0.05))
    c.setLineWidth(1.4)
    c.circle(cx, cy, r * 0.78, stroke=1, fill=1)
    c.setLineWidth(0.6)
    c.setStrokeColor(NAVY)
    c.circle(cx, cy, r * 0.5, stroke=1, fill=0)
    _text_on_circle(c, 'POLICE VERIFIED', cx, cy, r * 0.6, SANS_BOLD, r * 0.13, RED, 90, True)
    _text_on_circle(c, f'PAKVERIFY · {year}', cx, cy, r * 0.66, SANS_MED, r * 0.1, NAVY, 270, False)
    c.setFillColor(NAVY)
    c.drawPath(_star_path(c, cx, cy + r * 0.04, r * 0.36, r * 0.15, 5, 90), stroke=0, fill=1)
    _check(c, cx, cy + r * 0.0, r * 0.2, PAPER, width=r * 0.05)
    c.restoreState()


def _divider(c, y, half_width=120, color=GOLD):
    cx = PAGE_W / 2
    c.saveState()
    c.setStrokeColor(color)
    c.setLineWidth(0.7)
    c.line(cx - half_width, y, cx - 10, y)
    c.line(cx + 10, y, cx + half_width, y)
    c.setFillColor(color)
    p = c.beginPath()
    p.moveTo(cx, y + 4)
    p.lineTo(cx + 4, y)
    p.lineTo(cx, y - 4)
    p.lineTo(cx - 4, y)
    p.close()
    c.drawPath(p, stroke=0, fill=1)
    c.restoreState()


def _draw_qr(c, url, x, y, size):
    """Vector QR code (reportlab encoder) placed as a PDF form XObject."""
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderPDF

    widget = QrCodeWidget(url, barLevel='M', barBorder=1, barFillColor=NAVY)
    bx0, by0, bx1, by1 = widget.getBounds()
    w, h = bx1 - bx0, by1 - by0
    drawing = Drawing(size, size, transform=[size / w, 0, 0, size / h, -bx0 * size / w, -by0 * size / h])
    drawing.add(widget)
    name = 'PakVerifyQR'
    c.beginForm(name)
    renderPDF.draw(drawing, c, 0, 0)
    c.endForm()
    c.saveState()
    c.translate(x, y)
    c.doForm(name)
    c.restoreState()


def _fmt_date(value):
    if hasattr(value, 'strftime'):
        return value.strftime('%d %B %Y')
    return str(value or '')


# ── Page ─────────────────────────────────────────────────────────────────────

def render_certificate(values, verification_url):
    _register_fonts()
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=A4)
    c.setTitle(f"PakVerify Certificate {values.get('certificate_no', '')}")
    c.setAuthor('PakVerify')
    c.setSubject('Police verification certificate')

    W, H = PAGE_W, PAGE_H
    name = str(values.get('name') or '')
    father = str(values.get('father_name') or '')
    cnic = str(values.get('cnic') or '')
    district = str(values.get('district') or '')
    province = str(values.get('province') or '')
    cert_no = str(values.get('certificate_no') or '')
    issue = _fmt_date(values.get('issue_date'))
    valid = _fmt_date(values.get('valid_until'))
    status = str(values.get('status') or 'VERIFIED').upper()
    app_type = str(values.get('application_type') or 'Police Verification')
    tracking = str(values.get('tracking_id') or '')
    year = str(values.get('issue_date'))[:4] if values.get('issue_date') else ''
    if hasattr(values.get('issue_date'), 'year'):
        year = str(values['issue_date'].year)

    # paper
    c.setFillColor(PAPER)
    c.rect(0, 0, W, H, stroke=0, fill=1)

    # frame: outer green line, guilloche band, inner gold double rule
    m, band = 18, 22
    c.setStrokeColor(NAVY)
    c.setLineWidth(1.6)
    c.rect(m, m, W - 2 * m, H - 2 * m, stroke=1, fill=0)
    c.setFillColor(_alpha(NAVY, 0.05))
    c.rect(m, m, W - 2 * m, H - 2 * m, stroke=0, fill=1)
    _guilloche_band(c, m, m, W - m, H - m, band)
    inner = m + band
    c.setFillColor(PAPER)
    c.setStrokeColor(NAVY)
    c.setLineWidth(0.9)
    c.rect(inner, inner, W - 2 * inner, H - 2 * inner, stroke=1, fill=1)
    c.setStrokeColor(RED)
    c.setLineWidth(0.6)
    c.rect(inner + 4, inner + 4, W - 2 * inner - 8, H - 2 * inner - 8, stroke=1, fill=0)
    c.setStrokeColor(GOLD)
    c.setLineWidth(0.4)
    c.rect(inner + 7, inner + 7, W - 2 * inner - 14, H - 2 * inner - 14, stroke=1, fill=0)
    for (x, y) in ((m + band / 2, m + band / 2), (W - m - band / 2, m + band / 2),
                   (m + band / 2, H - m - band / 2), (W - m - band / 2, H - m - band / 2)):
        c.setFillColor(PAPER)
        c.circle(x, y, band * 0.62, stroke=0, fill=1)
        _rosette(c, x, y, band * 0.6, petals=8, color=GOLD, width=0.4, loops=2)
        c.setFillColor(GOLD)
        c.circle(x, y, 1.6, stroke=0, fill=1)

    # watermark rosette
    _rosette(c, W / 2, H * 0.47, 190, petals=24, color=NAVY, width=0.3, alpha=0.07, loops=4)

    # microtext along the inner frame
    _microtext(c, 'PAKVERIFY SECURE DIGITAL CERTIFICATE', inner + 12, W - inner - 12, inner + 9)
    _microtext(c, 'PAKVERIFY SECURE DIGITAL CERTIFICATE', inner + 12, W - inner - 12, H - inner - 13)

    # header
    top = H - inner
    _emblem(c, W / 2, top - 64, 38)
    _spaced(c, 'PAKVERIFY', W / 2, top - 125, SANS_BOLD, 11, 4.2, NAVY)
    _centered(c, 'Online Police Verification & Character Clearance Portal', top - 139, SANS, 8.5, MUTED)
    _centered(c, 'Police Verification Certificate', top - 176, SERIF_BOLD, 31, NAVY_DARK, max_width=W - 2 * inner - 60)
    _divider(c, top - 192, 150)
    _spaced(c, 'CHARACTER  &  BACKGROUND  CLEARANCE', W / 2, top - 208, SANS_MED, 8, 1.6, RED)

    # body
    y = top - 250
    _centered(c, 'This is to certify that', y, SERIF_ITALIC, 14, MUTED)
    y -= 38
    size = _centered(c, name, y, SERIF_BOLD_ITALIC, 30, INK, max_width=W - 2 * inner - 90)
    nw = min(c.stringWidth(name, SERIF_BOLD_ITALIC, size) + 40, W - 2 * inner - 60)
    c.setStrokeColor(GOLD)
    c.setLineWidth(0.8)
    c.line(W / 2 - nw / 2, y - 10, W / 2 + nw / 2, y - 10)

    y -= 34
    line1 = f'Son / Daughter of {father}'
    _centered(c, line1, y, SERIF, 13.5, INK, max_width=W - 2 * inner - 80)
    y -= 21
    parts = [('holder of CNIC No. ', SERIF), (cnic, SERIF_BOLD), (', resident of ', SERIF),
             (f'District {district}, {province}', SERIF_BOLD)]
    total = sum(c.stringWidth(t, f, 13.5) for t, f in parts)
    fs = 13.5
    while total > W - 2 * inner - 80 and fs > 9:
        fs -= 0.5
        total = sum(c.stringWidth(t, f, fs) for t, f in parts)
    x = W / 2 - total / 2
    for t, f in parts:
        c.setFont(f, fs)
        c.setFillColor(INK)
        c.drawString(x, y, t)
        x += c.stringWidth(t, f, fs)

    y -= 30
    statement = [
        'has successfully completed identity and background verification through the PakVerify',
        'portal. Identity was confirmed by AI face verification against NADRA records, the criminal',
        'record check was cleared by police staff, and the application was approved by the authority.',
    ]
    for line in statement:
        _centered(c, line, y, SERIF, 11.2, MUTED, max_width=W - 2 * inner - 70)
        y -= 16

    # details panel
    py = y - 18
    ph = 92
    px0, px1 = inner + 34, W - inner - 34
    c.setFillColor(_alpha(NAVY, 0.045))
    c.setStrokeColor(_alpha(NAVY, 0.35))
    c.setLineWidth(0.6)
    c.roundRect(px0, py - ph, px1 - px0, ph, 8, stroke=1, fill=1)
    cols = 3
    cw = (px1 - px0) / cols
    cells = [
        ('CERTIFICATE NO.', cert_no), ('DATE OF ISSUE', issue), ('VALID UNTIL', valid),
        ('VERIFICATION TYPE', app_type), ('APPLICATION REF.', tracking or '—'), ('STATUS', status),
    ]
    c.setStrokeColor(_alpha(NAVY, 0.18))
    c.line(px0 + 14, py - ph / 2, px1 - 14, py - ph / 2)
    for i in (1, 2):
        c.line(px0 + cw * i, py - 10, px0 + cw * i, py - ph + 10)
    for idx, (label, value) in enumerate(cells):
        col, row = idx % cols, idx // cols
        cx = px0 + cw * col + cw / 2
        cy = py - 20 - row * (ph / 2)
        _spaced(c, label, cx, cy, SANS_MED, 6.4, 1.1, GOLD)
        if label == 'STATUS':
            bw = c.stringWidth(value, SANS_BOLD, 9) + 26
            c.setFillColor(NAVY)
            c.roundRect(cx - bw / 2, cy - 23, bw, 15, 7.5, stroke=0, fill=1)
            _check(c, cx - bw / 2 + 10, cy - 15.5, 6, PAPER, width=1.3)
            c.setFont(SANS_BOLD, 9)
            c.setFillColor(PAPER)
            c.drawString(cx - bw / 2 + 17, cy - 18.8, value)
        else:
            _centered(c, value, cy - 18, SANS_MED, 10.5, INK, max_width=cw - 16, cx=cx)

    # approvals and seal
    sy = py - ph - 62
    for cx, role, sub in ((inner + 125, 'Verification Officer', 'Police staff review'),
                          (W - inner - 125, 'Approving Authority', 'Authority decision')):
        _centered(c, 'Digitally approved', sy + 8, SERIF_ITALIC, 12, NAVY, cx=cx)
        c.setStrokeColor(INK)
        c.setLineWidth(0.6)
        c.line(cx - 78, sy, cx + 78, sy)
        _centered(c, role, sy - 13, SANS_MED, 9, INK, cx=cx)
        _centered(c, sub, sy - 24, SANS, 7.2, MUTED, cx=cx)
    _seal(c, W / 2, sy + 2, 42, year)

    # verification panel
    vy = inner + 26
    vh = 104
    vx0, vx1 = inner + 22, W - inner - 22
    c.setFillColor(PAPER)
    c.setStrokeColor(GOLD)
    c.setLineWidth(0.7)
    c.roundRect(vx0, vy, vx1 - vx0, vh, 8, stroke=1, fill=1)
    qs = vh - 20
    c.setFillColor(HexColor('#FFFFFF'))
    c.setStrokeColor(_alpha(NAVY, 0.4))
    c.roundRect(vx0 + 10, vy + 10, qs, qs, 4, stroke=1, fill=1)
    _draw_qr(c, verification_url, vx0 + 14, vy + 14, qs - 8)
    tx = vx0 + qs + 26
    c.setFont(SANS_BOLD, 10)
    c.setFillColor(NAVY_DARK)
    c.drawString(tx, vy + vh - 24, 'Verify this certificate')
    lines = [
        'Scan the QR code or open the link below. The portal shows the live status',
        'and compares this document with its record on the PakVerify blockchain',
        'ledger; its SHA-256 fingerprint is also anchored on Ethereum (Sepolia).',
    ]
    c.setFont(SANS, 7.6)
    c.setFillColor(MUTED)
    for i, line in enumerate(lines):
        c.drawString(tx, vy + vh - 40 - i * 11, line)
    url = verification_url.replace('https://', '').replace('http://', '')
    fs = _fit(c, url, SANS_MED, 8, vx1 - tx - 12)
    c.setFont(SANS_MED, fs)
    c.setFillColor(NAVY)
    c.drawString(tx, vy + 16, url)

    # footer note
    c.setFont(SANS_LIGHT, 6.2)
    c.setFillColor(MUTED)
    c.drawCentredString(W / 2, inner + 15.5,
                        'Electronically generated certificate — valid without a physical signature or stamp. '
                        'PakVerify Final Year Project prototype.')

    c.showPage()
    c.save()
    return out.getvalue()
