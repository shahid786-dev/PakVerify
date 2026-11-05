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

# ── Extra local demo data for the chatbot / admin / explorer recording ──────
from django.core.management import call_command
call_command('seed_criminal_records')

admin, _ = User.objects.get_or_create(
    cnic='00000-0000001-1',
    defaults=dict(email='demo.admin@example.test', full_name='System Admin', role='SUPER_ADMIN'),
)
admin.set_password('DemoAdmin#2026')
admin.save()

others = [
    ('42101-0000003-3', 'Tahir Raza', 'Employee Verification', 'CRIMINAL_CHECKED'),
    ('42101-0000004-4', 'Mohsin Raza', 'Tenant Verification', 'PENDING'),
    ('42101-0000005-5', 'Ayesha Khan', 'Character Certificate', 'AUTHORITY_APPROVED'),
    ('42101-0000006-6', 'Bilal Ahmed', 'General Police Verification', 'PAYMENT_PENDING'),
]
for cnic, name, kind, status in others:
    u, _ = User.objects.get_or_create(cnic=cnic, defaults=dict(
        email=f'{cnic[-3:]}@example.test', full_name=name, role='CITIZEN',
        province='Sindh', district='Khairpur'))
    Application.objects.create(applicant=u, application_type=kind, purpose='Job',
                               current_address='Khairpur', nearest_station='City PS Khairpur',
                               status=status, applicant_province='Sindh')

info.update({
    'tracking_id': app.tracking_id,
    'application_id': app.id,
    'admin_cnic': admin.cnic,
    'citizen_cnic': citizen.cnic,
    'criminal_cnic': '42301-9876543-2',
})
with open(os.path.join(OUT, 'cert.json'), 'w') as fh:
    json.dump(info, fh, indent=2)
print('DEMO', json.dumps(info))
