"""
Local demo data for the integrity-check recording (runs on a throwaway SQLite DB
inside GitHub Actions). Creates a test citizen, a test staff officer, a paid
application, and issues a real certificate through CertificateService.
Usage: python manage.py shell < ../demo/seed_and_issue.py
"""
import json
import os
import shutil

from django.contrib.auth import get_user_model
from applications.models import Application
from applications.certificate_service import CertificateService

User = get_user_model()
OUT = os.path.abspath(os.path.join(os.getcwd(), '..', 'demo_out'))
os.makedirs(OUT, exist_ok=True)

citizen, _ = User.objects.get_or_create(
    cnic='42101-0000001-1',
    defaults=dict(email='demo.citizen@example.test', full_name='Shahid Ali',
                  role='CITIZEN', province='Sindh', district='Khairpur'),
)
citizen.set_password('DemoCitizen#2026')
citizen.save()

staff, _ = User.objects.get_or_create(
    cnic='00000-0000009-9',
    defaults=dict(email='demo.staff@example.test', full_name='Inspector Demo',
                  role='POLICE_STAFF', province='Sindh', district='Khairpur'),
)
staff.set_password('DemoStaff#2026')
staff.save()

app = Application.objects.create(
    applicant=citizen,
    application_type='Character Certificate',
    purpose='Job',
    current_address='Khairpur, Sindh',
    nearest_station='City Police Station Khairpur',
    status='PAYMENT_VERIFIED',
    applicant_province='Sindh',
)
cert = CertificateService.generate_certificate(app)
cert.refresh_from_db()

src = cert.pdf_file.path
shutil.copy(src, os.path.join(OUT, 'original.pdf'))
info = {
    'certificate_number': cert.certificate_number,
    'certificate_hash': cert.certificate_hash,
    'staff_cnic': staff.cnic,
}
with open(os.path.join(OUT, 'cert.json'), 'w') as fh:
    json.dump(info, fh, indent=2)
print('ISSUED', json.dumps(info))
