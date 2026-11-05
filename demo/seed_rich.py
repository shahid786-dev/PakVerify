"""
Local demo data for the HD recordings (throwaway SQLite DB in GitHub Actions).

Drives the REAL API endpoints with test accounts so every ledger block in the
Blockchain Explorer comes from an actual workflow action:
  citizen submit + document upload -> criminal check -> staff review/forward
  -> authority decision -> challan -> payment -> payment verification -> certificate.

The live-camera face step cannot run in CI, so after upload the script calls the
same criminal-check service the face endpoint calls on success and moves the
application to CRIMINAL_CHECKED (exactly what the endpoint does after a verified face).
Usage: python manage.py shell < ../demo/seed_rich.py
"""
import io
import json
import os
import shutil

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from PIL import Image, ImageDraw
from rest_framework.test import APIClient

from applications.models import Application, Certificate
from criminals.service import perform_criminal_check

User = get_user_model()
OUT = os.path.abspath(os.path.join(os.getcwd(), '..', 'demo_out'))
os.makedirs(OUT, exist_ok=True)

call_command('seed_criminal_records')


def user(cnic, name, role, email, password):
    u, _ = User.objects.get_or_create(cnic=cnic, defaults=dict(
        email=email, full_name=name, role=role, province='Sindh', district='Khairpur'))
    u.full_name, u.role, u.province, u.district = name, role, 'Sindh', 'Khairpur'
    u.set_password(password)
    u.save()
    return u


citizens = [
    user('42101-0000001-1', 'Shahid Ali', 'CITIZEN', 'c1@example.test', 'DemoCitizen#2026'),
    user('42101-0000002-2', 'Tahir Raza', 'CITIZEN', 'c2@example.test', 'DemoCitizen#2026'),
    user('42101-0000003-3', 'Mohsin Raza', 'CITIZEN', 'c3@example.test', 'DemoCitizen#2026'),
    user('42101-0000004-4', 'Ayesha Khan', 'CITIZEN', 'c4@example.test', 'DemoCitizen#2026'),
]
staff = user('00000-0000009-9', 'Inspector Demo', 'POLICE_STAFF', 'staff@example.test', 'DemoStaff#2026')
authority = user('00000-0000008-8', 'SP Demo', 'POLICE_AUTHORITY', 'sp@example.test', 'DemoAuthority#2026')
admin = user('00000-0000007-7', 'Admin Demo', 'SUPER_ADMIN', 'admin@example.test', 'DemoAdmin#2026')


def as_user(u):
    c = APIClient(HTTP_HOST='localhost')
    c.force_authenticate(u)
    return c


def ok(res, step):
    if res.status_code >= 400:
        raise SystemExit(f'{step} failed {res.status_code}: {str(getattr(res, "data", None) or res.content[:400])}')
    return res


def png(label):
    im = Image.new('RGB', (640, 400), (232, 236, 244))
    ImageDraw.Draw(im).text((24, 24), label, fill=(20, 30, 60))
    buf = io.BytesIO()
    im.save(buf, 'JPEG')
    return SimpleUploadedFile(f'{label}.jpg', buf.getvalue(), content_type='image/jpeg')


TYPES = ['Character Certificate', 'Tenant Verification', 'Employee Verification', 'Character Certificate']
STAGES = ['COMPLETED', 'PAYMENT_PENDING', 'FORWARDED_TO_ADMIN', 'PENDING']
apps = []
for cz, kind, stage in zip(citizens, TYPES, STAGES):
    c = as_user(cz)
    res = ok(c.post('/api/citizen/applications/', {
        'application_type': kind, 'purpose': 'Job', 'current_address': 'Khairpur, Sindh',
        'nearest_station': 'City Police Station Khairpur'}, format='json'), 'create')
    app = Application.objects.filter(applicant=cz).latest('id')
    for doc in ('CNIC_FRONT', 'CNIC_BACK', 'PASSPORT_PHOTO'):
        ok(c.post(f'/api/citizen/applications/{app.id}/upload/',
                  {'document_type': doc, 'file': png(f'{cz.full_name} {doc}')}, format='multipart'), 'upload')
    apps.append((app, cz, stage))
    if stage == 'PENDING':
        continue

    # same service the face-verify endpoint runs after a verified live face
    perform_criminal_check(app)
    app.status = 'CRIMINAL_CHECKED'
    app.face_confidence, app.liveness_score = 86.6, 0.9
    app.save(update_fields=['status', 'face_confidence', 'liveness_score', 'updated_at'])

    s, a = as_user(staff), as_user(authority)
    ok(s.post(f'/api/staff/applications/{app.id}/remark/',
              {'remarks': 'Documents verified.', 'recommendation': 'APPROVE'}, format='json'), 'remark')
    ok(s.post(f'/api/staff/applications/{app.id}/forward/', {'remarks': 'Forwarded.'}, format='json'), 'forward')
    if stage == 'FORWARDED_TO_ADMIN':
        continue
    ok(a.post(f'/api/authority/applications/{app.id}/decide/',
              {'decision': 'APPROVE', 'reason': 'Approved.'}, format='json'), 'decide')
    ok(s.post(f'/api/staff/applications/{app.id}/confirm/', {}, format='json'), 'confirm')
    if stage == 'PAYMENT_PENDING':
        continue
    ok(c.post(f'/api/citizen/applications/{app.id}/pay/',
              {'payment_method': 'EASYPAISA', 'mobile_number': '03001234567'}, format='json'), 'pay')
    ok(s.post(f'/api/staff/applications/{app.id}/verify-payment/', {}, format='json'), 'verify-payment')
    app.refresh_from_db()
    if app.status != 'COMPLETED':
        ok(a.post(f'/api/authority/applications/{app.id}/issue-cert/', {}, format='json'), 'issue-cert')

first = apps[0][0]
cert = Certificate.objects.get(application=first)
shutil.copy(cert.pdf_file.path, os.path.join(OUT, 'original.pdf'))
info = {
    'certificate_number': cert.certificate_number,
    'certificate_hash': cert.certificate_hash,
    'staff_cnic': staff.cnic,
    'authority_cnic': authority.cnic,
    'admin_cnic': admin.cnic,
    'citizen_cnic': citizens[0].cnic,
    'applications': [{'id': a.id, 'tracking_id': Application.objects.get(id=a.id).tracking_id,
                      'status': Application.objects.get(id=a.id).status, 'citizen': cz.full_name}
                     for a, cz, _ in apps],
    'criminal_cnic': '42301-9876543-2',
}
with open(os.path.join(OUT, 'cert.json'), 'w') as fh:
    json.dump(info, fh, indent=2)
print('SEEDED', json.dumps(info))
