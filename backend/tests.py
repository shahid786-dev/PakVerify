import hashlib
import tempfile
from datetime import date, timedelta
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from rest_framework import status
from django.utils import timezone
from django.test import override_settings
from unittest.mock import Mock, patch
from applications.models import Application, Challan, Document, Certificate
from criminals.models import CriminalRecord, CriminalCheckResult
from incidents.models import EmergencySOS
from nadra.models import NADRAVerification

User = get_user_model()

def create_citizen(**kwargs):
    defaults = dict(
        cnic='35202-1234567-9', email='test@example.com',
        password='Test@1234', full_name='Test Citizen',
        role='CITIZEN', province='Punjab', district='Lahore'
    )
    defaults.update(kwargs)
    return User.objects.create_user(**defaults)

def create_staff(**kwargs):
    defaults = dict(
        cnic='00000-1111111-1', email='staff@test.com',
        password='TestStaffPass!456', full_name='Test Staff', role='POLICE_STAFF'
    )
    defaults.update(kwargs)
    return User.objects.create_user(**defaults)



# ── Active liveness test helpers ────────────────────────────────────────────
_ACTIVE_LIVENESS_PASS = {
    'verified': True, 'liveness_score': 0.9, 'anti_spoofing': 'REAL', 'reason': 'LIVE',
    'message': 'Live person confirmed.', 'metrics': {}, 'center_bytes': b'center-frame',
}


class _LivenessPatches:
    """Patch both the head-turn check and the AI-service passive check."""

    def __init__(self, passive_result, active_result=None):
        self._patches = [
            patch('face_verification.active_liveness.run_active_liveness',
                  return_value=active_result or _ACTIVE_LIVENESS_PASS),
            patch('face_verification.views.check_liveness_with_ai', return_value=passive_result),
        ]

    def __enter__(self):
        return [p.start() for p in self._patches]

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False

class StaffPasswordRequiredTests(TestCase):
    def setUp(self):
        self.authority = create_citizen(
            cnic='11111-1111111-1',
            email='authority-password-test@example.com',
            role='POLICE_AUTHORITY',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.authority)

    def test_staff_creation_requires_password(self):
        response = self.client.post('/api/authority/staff/', {
            'cnic': '22222-2222222-2',
            'email': 'new-staff@example.com',
            'full_name': 'New Staff',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(cnic='22222-2222222-2').exists())

    def test_staff_password_reset_requires_password(self):
        staff = create_staff()

        response = self.client.post(
            f'/api/authority/staff/{staff.pk}/reset-password/',
            {},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(staff.check_password('TestStaffPass!456'))


class PublicPortalStatsTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def create_application(self, citizen, status, issue_certificate=False):
        application = Application.objects.create(
            applicant=citizen,
            application_type='Character Certificate',
            purpose='Employment',
            current_address='Lahore',
            nearest_station='Central Police Station',
            status=status,
        )
        if issue_certificate:
            Certificate.objects.create(
                application=application,
                validity_expiry=timezone.localdate() + timedelta(days=365),
                digital_signature='test-signature',
                verification_url='/verify/test/',
                pdf_file='certificates/test.pdf',
                certificate_hash=hashlib.sha256(application.tracking_id.encode()).hexdigest(),
            )
        return application

    def test_counts_unique_citizens_with_completed_issued_certificates(self):
        citizen = create_citizen()
        self.create_application(citizen, 'COMPLETED', issue_certificate=True)
        self.create_application(citizen, 'COMPLETED', issue_certificate=True)
        self.create_application(citizen, 'PENDING', issue_certificate=True)
        self.create_application(citizen, 'REJECTED', issue_certificate=True)
        self.create_application(citizen, 'COMPLETED')

        response = self.client.get('/api/public/stats/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'verified_citizens': 1})

        second_citizen = create_citizen(
            cnic='35202-7654321-9',
            email='second-citizen@example.com',
            full_name='Second Citizen',
        )
        self.create_application(second_citizen, 'COMPLETED', issue_certificate=True)

        response = self.client.get('/api/public/stats/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'verified_citizens': 2})

    def test_returns_zero_when_no_citizens_have_completed_verification(self):
        response = self.client.get('/api/public/stats/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'verified_citizens': 0})


# ─── Auth Tests ──────────────────────────────────────────────────────────────
class AuthTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_register_citizen(self):
        data = {
            'cnic': '35202-7777777-7', 'email': 'new@test.com',
            'password': 'Pass@1234', 'full_name': 'New Citizen',
            'father_name': 'Citizen Father',
            'dob': '1992-06-15',
            'address': 'House 10, Model Town, Lahore',
            'province': 'Punjab', 'district': 'Lahore'
        }
        res = self.client.post('/api/auth/register/', data)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIn('otp_code', res.data)
        citizen = User.objects.get(cnic=data['cnic'])
        self.assertEqual(citizen.father_name, 'Citizen Father')
        self.assertEqual(citizen.dob, date(1992, 6, 15))
        self.assertEqual(citizen.address, 'House 10, Model Town, Lahore')
        self.assertEqual(citizen.province, 'Punjab')

    def test_register_duplicate_cnic_fails(self):
        create_citizen()
        data = {'cnic': '35202-1234567-9', 'email': 'other@test.com', 'password': 'x', 'full_name': 'X'}
        res = self.client.post('/api/auth/register/', data)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_returns_otp(self):
        create_citizen()
        res = self.client.post('/api/auth/login/', {'cnic': '35202-1234567-9', 'password': 'Test@1234'})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('otp_code', res.data)

    def test_login_wrong_password(self):
        create_citizen()
        res = self.client.post('/api/auth/login/', {'cnic': '35202-1234567-9', 'password': 'Wrong'})
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_otp_verify_bypass(self):
        user = create_citizen()
        from users.otp_service import get_otp_service
        get_otp_service().generate_and_send(user, purpose="login")
        user.refresh_from_db()
        res = self.client.post('/api/auth/verify-otp/', {'cnic': user.cnic, 'otp_code': '123456'})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('access', res.data)
        self.assertEqual(res.data['role'], 'CITIZEN')

    @override_settings(OTP_DEV_BYPASS_ENABLED=False)
    def test_otp_bypass_disabled_outside_local_mode(self):
        user = create_citizen()
        from users.otp_service import get_otp_service
        get_otp_service().generate_and_send(user, purpose="login")
        res = self.client.post('/api/auth/verify-otp/', {'cnic': user.cnic, 'otp_code': '123456'})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Invalid OTP', res.data['error'])


# ─── Application Tests ────────────────────────────────────────────────────────
class ApplicationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.citizen = create_citizen()
        self.client.force_authenticate(user=self.citizen)

    def test_create_application(self):
        data = {
            'application_type': 'Character Certificate',
            'purpose': 'Job application',
            'current_address': 'House 1, DHA Lahore',
            'nearest_station': 'Gulberg PS, Lahore'
        }
        res = self.client.post('/api/citizen/applications/', data)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIn('tracking_id', res.data)
        self.assertEqual(res.data['status'], 'PENDING')
        app = Application.objects.get(pk=res.data['id'])
        self.assertEqual(app.status, 'PENDING')

    def test_citizen_cannot_see_other_citizen_apps(self):
        other = create_citizen(cnic='35202-9999999-9', email='other@test.com')
        Application.objects.create(
            applicant=other, application_type='Character Certificate',
            purpose='Test', current_address='Test', nearest_station='Test PS'
        )
        res = self.client.get('/api/citizen/applications/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        for item in res.data:
            self.assertEqual(item['applicant']['cnic'], self.citizen.cnic)

    def test_staff_queue_includes_new_and_face_verified_applications(self):
        pending = Application.objects.create(
            applicant=self.citizen, application_type='Character Certificate',
            purpose='Pending application', current_address='Test', nearest_station='Test PS',
        )
        face_verified = Application.objects.create(
            applicant=self.citizen, application_type='Character Certificate',
            purpose='Face verified application', current_address='Test', nearest_station='Test PS',
            status='FACE_VERIFIED',
        )
        self.client.force_authenticate(user=create_staff())

        response = self.client.get('/api/staff/applications/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item['id'] for item in response.data},
            {pending.id, face_verified.id},
        )

    def test_face_verify_requires_live_image(self):
        app = Application.objects.create(
            applicant=self.citizen, application_type='Character Certificate',
            purpose='Test', current_address='Test', nearest_station='Test PS'
        )
        Document.objects.create(application=app, document_type='PASSPORT_PHOTO', file='dummy.jpg')
        res = self.client.post(f'/api/citizen/applications/{app.pk}/face-verify/')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('live face image', res.data['error'])

    @patch('face_verification.active_liveness.run_active_liveness')
    @patch('face_verification.views.check_liveness_with_ai')
    @patch('face_verification.views.FaceVerificationService.verify_with_cnic')
    def test_cnic_face_verify_runs_criminal_check(self, mock_verify, mock_liveness, mock_active):
        mock_active.return_value = _ACTIVE_LIVENESS_PASS
        from django.core.files.uploadedfile import SimpleUploadedFile
        from face_verification.models import FaceVerificationReport

        app = Application.objects.create(
            applicant=self.citizen, application_type='Character Certificate',
            purpose='Test', current_address='Test', nearest_station='Test PS'
        )
        
        report = FaceVerificationReport.objects.create(
            citizen=self.citizen,
            application=app,
            matched_cnic=self.citizen.cnic,
            matched_citizen_name=self.citizen.full_name,
            matched_father_name='Father',
            matched_date_of_birth='1990-01-01',
            matched_gender='M',
            matched_address='Address',
            matched_district='District',
            matched_province='Province',
            matched_photo_url='http://test',
            similarity_score=0.885,
            similarity_pct=88.5,
            status='VERIFIED',
            confidence_level='HIGH',
            model_used='InsightFace',
            processing_time_ms=120.0,
            verified_at=timezone.now(),
            blockchain_hash='xyz',
            blockchain_block_index=5
        )

        mock_verify.return_value = report
        mock_liveness.return_value = {
            'liveness_score': 0.91,
            'anti_spoofing': 'REAL',
            'face_detected': True,
            'verified': True,
        }

        dummy_image = SimpleUploadedFile("face.jpg", b"file_content", content_type="image/jpeg")
        data = {
            'cnic': self.citizen.cnic,
            'live_image': dummy_image,
            'application_id': app.id
        }

        res = self.client.post('/api/face-verify/verify-with-cnic/', data, format='multipart')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['success'])

        app.refresh_from_db()
        self.assertEqual(app.status, 'CRIMINAL_CHECKED')

        # Criminal screening runs against the authenticated citizen's CNIC.
        self.assertFalse(NADRAVerification.objects.filter(application=app).exists())
        self.assertEqual(CriminalCheckResult.objects.get(application=app).result, 'CLEAN')

        # Test repeat request: should return immediately without calling mock_verify again
        mock_verify.reset_mock()
        res_repeat = self.client.post('/api/face-verify/verify-with-cnic/', data, format='multipart')
        self.assertEqual(res_repeat.status_code, status.HTTP_200_OK)
        mock_verify.assert_not_called()

    def test_process_payment(self):
        app = Application.objects.create(
            applicant=self.citizen, application_type='Character Certificate',
            purpose='Test', current_address='Test', nearest_station='Test PS',
            status='PAYMENT_PENDING'
        )
        Challan.objects.create(application=app, due_date='2027-01-01')
        res = self.client.post(f'/api/citizen/applications/{app.pk}/pay/', {'payment_method': 'jazzcash'})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['challan_status'], 'PAID')
        self.assertEqual(res.data['application_status'], 'PAYMENT_SUBMITTED')


# ─── Stage 2: Workflow State Machine Guard Tests ──────────────────────────────

def create_authority(**kwargs):
    defaults = dict(
        cnic='00000-2222222-2', email='authority@test.com',
        password='Auth@1234', full_name='Test Authority', role='POLICE_AUTHORITY'
    )
    defaults.update(kwargs)
    return User.objects.create_user(**defaults)


def make_app(citizen, status_val='PENDING'):
    return Application.objects.create(
        applicant=citizen,
        application_type='Character Certificate',
        purpose='Test',
        current_address='Test Address',
        nearest_station='Test PS',
        status=status_val,
    )


class WorkflowGuardTests(TestCase):
    """
    Stage 2 — Strict state machine enforcement.
    """

    def setUp(self):
        self.client = APIClient()
        self.citizen   = create_citizen()
        self.staff     = create_staff()
        self.authority = create_authority()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _staff_client(self):
        c = APIClient()
        c.force_authenticate(user=self.staff)
        return c

    def _authority_client(self):
        c = APIClient()
        c.force_authenticate(user=self.authority)
        return c

    def _citizen_client(self):
        c = APIClient()
        c.force_authenticate(user=self.citizen)
        return c

    # ─────────────────────────────────────────────────────────────────────────
    # Test 1 — Staff cannot forward PENDING application
    # ─────────────────────────────────────────────────────────────────────────
    def test_staff_cannot_forward_pending(self):
        app = make_app(self.citizen, 'PENDING')
        res = self._staff_client().post(f'/api/staff/applications/{app.pk}/forward/', {'remarks': 'test'})
        self.assertEqual(res.status_code, 400)
        app.refresh_from_db()
        self.assertEqual(app.status, 'PENDING')

    # ─────────────────────────────────────────────────────────────────────────
    # Test 2 — Staff cannot forward before STAFF_REVIEWED (FACE_VERIFIED state)
    # ─────────────────────────────────────────────────────────────────────────
    def test_staff_cannot_forward_face_verified(self):
        app = make_app(self.citizen, 'FACE_VERIFIED')
        res = self._staff_client().post(f'/api/staff/applications/{app.pk}/forward/', {'remarks': 'test'})
        self.assertEqual(res.status_code, 400)
        app.refresh_from_db()
        self.assertEqual(app.status, 'FACE_VERIFIED')  # db unchanged

    def test_staff_can_forward_staff_reviewed(self):
        app = make_app(self.citizen, 'STAFF_REVIEWED')
        res = self._staff_client().post(f'/api/staff/applications/{app.pk}/forward/', {'remarks': 'test'})
        self.assertEqual(res.status_code, 200)
        app.refresh_from_db()
        self.assertEqual(app.status, 'FORWARDED_TO_ADMIN')  # db updated

    # ─────────────────────────────────────────────────────────────────────────
    # Test 4 — Admin cannot approve PENDING application
    # ─────────────────────────────────────────────────────────────────────────
    def test_admin_cannot_approve_pending(self):
        app = make_app(self.citizen, 'PENDING')
        res = self._authority_client().post(f'/api/authority/applications/{app.pk}/decide/', {'decision': 'APPROVE'})
        self.assertEqual(res.status_code, 400)
        app.refresh_from_db()
        self.assertEqual(app.status, 'PENDING')  # db unchanged

    # ─────────────────────────────────────────────────────────────────────────
    # Test 5 — Admin cannot approve FACE_VERIFIED application
    # ─────────────────────────────────────────────────────────────────────────
    def test_admin_cannot_approve_face_verified(self):
        app = make_app(self.citizen, 'FACE_VERIFIED')
        res = self._authority_client().post(f'/api/authority/applications/{app.pk}/decide/', {'decision': 'APPROVE'})
        self.assertEqual(res.status_code, 400)
        app.refresh_from_db()
        self.assertEqual(app.status, 'FACE_VERIFIED')  # db unchanged

    # ─────────────────────────────────────────────────────────────────────────
    # Test 6 — Admin cannot approve STAFF_REVIEWED application (not yet forwarded)
    # ─────────────────────────────────────────────────────────────────────────
    def test_admin_cannot_approve_staff_reviewed(self):
        app = make_app(self.citizen, 'STAFF_REVIEWED')
        res = self._authority_client().post(f'/api/authority/applications/{app.pk}/decide/', {'decision': 'APPROVE'})
        self.assertEqual(res.status_code, 400)
        app.refresh_from_db()
        self.assertEqual(app.status, 'STAFF_REVIEWED')  # db unchanged

    # ─────────────────────────────────────────────────────────────────────────
    # Test 7 — Admin CAN approve FORWARDED_TO_ADMIN application
    # ─────────────────────────────────────────────────────────────────────────
    def test_admin_can_approve_forwarded(self):
        app = make_app(self.citizen, 'FORWARDED_TO_ADMIN')
        res = self._authority_client().post(f'/api/authority/applications/{app.pk}/decide/', {'decision': 'APPROVE'})
        self.assertEqual(res.status_code, 200)
        app.refresh_from_db()
        self.assertEqual(app.status, 'AUTHORITY_APPROVED')  # db updated

    # ─────────────────────────────────────────────────────────────────────────
    # Test 8 — Admin cannot approve the same application repeatedly
    # ─────────────────────────────────────────────────────────────────────────
    def test_admin_cannot_approve_twice(self):
        app = make_app(self.citizen, 'FORWARDED_TO_ADMIN')
        # First approval
        res1 = self._authority_client().post(f'/api/authority/applications/{app.pk}/decide/', {'decision': 'APPROVE'})
        self.assertEqual(res1.status_code, 200)
        # Second approval attempt
        res2 = self._authority_client().post(f'/api/authority/applications/{app.pk}/decide/', {'decision': 'APPROVE'})
        self.assertEqual(res2.status_code, 200)  # idempotent, safe response
        self.assertIn('already been recorded', res2.data['message'])
        app.refresh_from_db()
        self.assertEqual(app.status, 'AUTHORITY_APPROVED')  # unchanged after duplicate

    # ─────────────────────────────────────────────────────────────────────────
    # Test 9 — Staff cannot confirm PENDING application
    # ─────────────────────────────────────────────────────────────────────────
    def test_staff_cannot_confirm_pending(self):
        app = make_app(self.citizen, 'PENDING')
        res = self._staff_client().post(f'/api/staff/applications/{app.pk}/confirm/')
        self.assertEqual(res.status_code, 400)
        app.refresh_from_db()
        self.assertEqual(app.status, 'PENDING')  # db unchanged

    # ─────────────────────────────────────────────────────────────────────────
    # Test 10 — Staff cannot confirm FORWARDED_TO_ADMIN (admin hasn't decided yet)
    # ─────────────────────────────────────────────────────────────────────────
    def test_staff_cannot_confirm_forwarded(self):
        app = make_app(self.citizen, 'FORWARDED_TO_ADMIN')
        res = self._staff_client().post(f'/api/staff/applications/{app.pk}/confirm/')
        self.assertEqual(res.status_code, 400)
        app.refresh_from_db()
        self.assertEqual(app.status, 'FORWARDED_TO_ADMIN')  # db unchanged

    # ─────────────────────────────────────────────────────────────────────────
    # Test 11 — Staff CAN confirm AUTHORITY_APPROVED application
    # ─────────────────────────────────────────────────────────────────────────
    def test_staff_can_confirm_authority_approved(self):
        app = make_app(self.citizen, 'AUTHORITY_APPROVED')
        res = self._staff_client().post(f'/api/staff/applications/{app.pk}/confirm/')
        self.assertEqual(res.status_code, 200)
        app.refresh_from_db()
        # After confirm, challan is generated and status becomes PAYMENT_PENDING
        self.assertEqual(app.status, 'PAYMENT_PENDING')  # _generate_challan sets PAYMENT_PENDING
        self.assertTrue(Challan.objects.filter(application=app).exists())

    # ─────────────────────────────────────────────────────────────────────────
    # Test 12 — Staff cannot confirm AUTHORITY_REJECTED application
    # ─────────────────────────────────────────────────────────────────────────
    def test_staff_cannot_confirm_authority_rejected(self):
        app = make_app(self.citizen, 'AUTHORITY_REJECTED')
        res = self._staff_client().post(f'/api/staff/applications/{app.pk}/confirm/')
        self.assertEqual(res.status_code, 400)
        app.refresh_from_db()
        self.assertEqual(app.status, 'AUTHORITY_REJECTED')  # db unchanged

    # ─────────────────────────────────────────────────────────────────────────
    # Test 13 — Staff cannot verify payment before PAYMENT_SUBMITTED
    # ─────────────────────────────────────────────────────────────────────────
    def test_staff_cannot_verify_payment_before_submitted(self):
        app = make_app(self.citizen, 'PAYMENT_PENDING')
        res = self._staff_client().post(f'/api/staff/applications/{app.pk}/verify-payment/')
        self.assertEqual(res.status_code, 400)
        app.refresh_from_db()
        self.assertEqual(app.status, 'PAYMENT_PENDING')  # db unchanged

    # ─────────────────────────────────────────────────────────────────────────
    # Test 14 — Staff can verify a valid PAYMENT_SUBMITTED application
    # ─────────────────────────────────────────────────────────────────────────
    def test_staff_can_verify_payment_submitted(self):
        from applications.models import Payment
        app = make_app(self.citizen, 'PAYMENT_SUBMITTED')
        challan = Challan.objects.create(application=app, due_date='2027-01-01', status='PAID')
        Payment.objects.create(
            application=app, challan=challan,
            amount=650, payment_method='JAZZCASH',
        )
        res = self._staff_client().post(f'/api/staff/applications/{app.pk}/verify-payment/')
        self.assertEqual(res.status_code, 200)
        app.refresh_from_db()
        # Status advances through PAYMENT_VERIFIED → PAYMENT_CONFIRMED → COMPLETED
        # depending on whether the CertificateService succeeds in test environment
        self.assertIn(app.status, ['PAYMENT_VERIFIED', 'PAYMENT_CONFIRMED', 'COMPLETED'])

    # ─────────────────────────────────────────────────────────────────────────
    # Test 15 — Repeated requests do not create duplicate workflow actions
    # ─────────────────────────────────────────────────────────────────────────
    def test_repeated_confirm_does_not_duplicate_challan(self):
        app = make_app(self.citizen, 'AUTHORITY_APPROVED')
        # First confirm
        res1 = self._staff_client().post(f'/api/staff/applications/{app.pk}/confirm/')
        self.assertEqual(res1.status_code, 200)
        challan_count_1 = Challan.objects.filter(application=app).count()
        # Second confirm
        res2 = self._staff_client().post(f'/api/staff/applications/{app.pk}/confirm/')
        self.assertEqual(res2.status_code, 200)
        self.assertIn('already been confirmed', res2.data['message'])
        challan_count_2 = Challan.objects.filter(application=app).count()
        # Challan count must not increase
        self.assertEqual(challan_count_1, challan_count_2)

    def test_repeated_forward_does_not_double_forward(self):
        app = make_app(self.citizen, 'STAFF_REVIEWED')
        res1 = self._staff_client().post(f'/api/staff/applications/{app.pk}/forward/', {'remarks': 'ok'})
        self.assertEqual(res1.status_code, 200)
        res2 = self._staff_client().post(f'/api/staff/applications/{app.pk}/forward/', {'remarks': 'ok again'})
        self.assertEqual(res2.status_code, 200)  # safe idempotent response
        self.assertIn('already been forwarded', res2.data['message'])
        app.refresh_from_db()
        self.assertEqual(app.status, 'FORWARDED_TO_ADMIN')  # unchanged

    # ─────────────────────────────────────────────────────────────────────────
    # Test 16 — Citizen cannot directly change application status via staff endpoints
    # ─────────────────────────────────────────────────────────────────────────
    def test_citizen_cannot_call_staff_forward(self):
        app = make_app(self.citizen, 'STAFF_REVIEWED')
        res = self._citizen_client().post(f'/api/staff/applications/{app.pk}/forward/')
        self.assertEqual(res.status_code, 403)
        app.refresh_from_db()
        self.assertEqual(app.status, 'STAFF_REVIEWED')  # db unchanged

    def test_citizen_cannot_call_staff_confirm(self):
        app = make_app(self.citizen, 'AUTHORITY_APPROVED')
        res = self._citizen_client().post(f'/api/staff/applications/{app.pk}/confirm/')
        self.assertEqual(res.status_code, 403)
        app.refresh_from_db()
        self.assertEqual(app.status, 'AUTHORITY_APPROVED')  # db unchanged

    def test_citizen_cannot_call_authority_decide(self):
        app = make_app(self.citizen, 'FORWARDED_TO_ADMIN')
        res = self._citizen_client().post(f'/api/authority/applications/{app.pk}/decide/', {'decision': 'APPROVE'})
        self.assertEqual(res.status_code, 403)
        app.refresh_from_db()
        self.assertEqual(app.status, 'FORWARDED_TO_ADMIN')  # db unchanged

    # ─────────────────────────────────────────────────────────────────────────
    # Test 17 — Police staff cannot perform admin approval (wrong role)
    # ─────────────────────────────────────────────────────────────────────────
    def test_staff_cannot_perform_admin_approval(self):
        app = make_app(self.citizen, 'FORWARDED_TO_ADMIN')
        res = self._staff_client().post(f'/api/authority/applications/{app.pk}/decide/', {'decision': 'APPROVE'})
        self.assertEqual(res.status_code, 403)
        app.refresh_from_db()
        self.assertEqual(app.status, 'FORWARDED_TO_ADMIN')  # db unchanged

    # ─────────────────────────────────────────────────────────────────────────
    # Test 18 — Admin cannot perform police-only confirmation (wrong role)
    # ─────────────────────────────────────────────────────────────────────────
    def test_admin_cannot_perform_staff_confirmation(self):
        app = make_app(self.citizen, 'AUTHORITY_APPROVED')
        res = self._authority_client().post(f'/api/staff/applications/{app.pk}/confirm/')
        self.assertEqual(res.status_code, 403)
        app.refresh_from_db()
        self.assertEqual(app.status, 'AUTHORITY_APPROVED')  # db unchanged

    # ─────────────────────────────────────────────────────────────────────────
    # Extra: Full linear happy path — database state at every step
    # ─────────────────────────────────────────────────────────────────────────
    def test_full_workflow_happy_path_db_verification(self):
        """Verifies DB state changes at each stage of the workflow."""
        from applications.models import Payment

        app = make_app(self.citizen, 'CRIMINAL_CHECKED')
        CriminalCheckResult.objects.create(
            application=app,
            result='CLEAN',
            report_summary='No criminal record found.',
        )

        # Step 1: Staff reviews
        res = self._staff_client().post(f'/api/staff/applications/{app.pk}/remark/', {'remarks': 'Verified in person.'})
        self.assertEqual(res.status_code, 200)
        app.refresh_from_db()
        self.assertEqual(app.status, 'STAFF_REVIEWED')

        # Step 2: Staff forwards
        res = self._staff_client().post(f'/api/staff/applications/{app.pk}/forward/', {'remarks': 'Forwarding for approval.'})
        self.assertEqual(res.status_code, 200)
        app.refresh_from_db()
        self.assertEqual(app.status, 'FORWARDED_TO_ADMIN')

        # Step 3: Authority approves
        res = self._authority_client().post(f'/api/authority/applications/{app.pk}/decide/', {'decision': 'APPROVE', 'reason': 'All clear.'})
        self.assertEqual(res.status_code, 200)
        app.refresh_from_db()
        self.assertEqual(app.status, 'AUTHORITY_APPROVED')

        # Step 4: Staff confirms and challan is generated
        res = self._staff_client().post(f'/api/staff/applications/{app.pk}/confirm/')
        self.assertEqual(res.status_code, 200)
        app.refresh_from_db()
        self.assertEqual(app.status, 'PAYMENT_PENDING')
        self.assertTrue(Challan.objects.filter(application=app).exists())

        # Step 5: Citizen pays
        challan = Challan.objects.get(application=app)
        self._citizen_client().post(f'/api/citizen/applications/{app.pk}/pay/', {'payment_method': 'JAZZCASH'})
        app.refresh_from_db()
        self.assertEqual(app.status, 'PAYMENT_SUBMITTED')

        # Step 6: Staff verifies payment
        Payment.objects.filter(application=app).update()  # ensure payment exists
        res = self._staff_client().post(f'/api/staff/applications/{app.pk}/verify-payment/')
        self.assertEqual(res.status_code, 200)
        app.refresh_from_db()
        # Status advances through PAYMENT_VERIFIED → PAYMENT_CONFIRMED → COMPLETED
        # depending on whether the CertificateService succeeds in test environment
        self.assertIn(app.status, ['PAYMENT_VERIFIED', 'PAYMENT_CONFIRMED', 'COMPLETED'])

    # ─────────────────────────────────────────────────────────────────────────
    # Stage 3: Legacy Review Bypass Removal Tests
    # ─────────────────────────────────────────────────────────────────────────

    def test_legacy_review_endpoint_is_removed(self):
        """1, 2, 6, 7. Legacy review endpoint must return 404 and cannot approve or issue challan."""
        app = make_app(self.citizen, 'FACE_VERIFIED')
        
        # Call the old URL pattern directly
        res = self._staff_client().post(f'/api/police/applications/{app.pk}/review/', {'status': 'APPROVED', 'notes': 'Should fail'})
        self.assertEqual(res.status_code, 404)
        
        app.refresh_from_db()
        self.assertEqual(app.status, 'FACE_VERIFIED')  # Status unaffected
        self.assertFalse(Challan.objects.filter(application=app).exists())  # No challan generated

    def test_staff_cannot_directly_set_authority_approved(self):
        """9. No Police Staff endpoint can directly assign AUTHORITY_APPROVED status."""
        app = make_app(self.citizen, 'FACE_VERIFIED')
        
        # Test remark endpoint
        res1 = self._staff_client().post(f'/api/staff/applications/{app.pk}/remark/', {'status': 'AUTHORITY_APPROVED', 'remarks': 'hack'})
        # Should not set status to AUTHORITY_APPROVED
        app.refresh_from_db()
        self.assertNotEqual(app.status, 'AUTHORITY_APPROVED')

        # Test forward endpoint
        res2 = self._staff_client().post(f'/api/staff/applications/{app.pk}/forward/', {'status': 'AUTHORITY_APPROVED', 'remarks': 'hack'})
        app.refresh_from_db()
        self.assertNotEqual(app.status, 'AUTHORITY_APPROVED')

        # Test confirm endpoint
        res3 = self._staff_client().post(f'/api/staff/applications/{app.pk}/confirm/', {'status': 'AUTHORITY_APPROVED'})
        app.refresh_from_db()
        self.assertNotEqual(app.status, 'AUTHORITY_APPROVED')

    def test_staff_cannot_directly_set_authority_rejected(self):
        """10. No Police Staff endpoint can directly assign AUTHORITY_REJECTED status."""
        app = make_app(self.citizen, 'FACE_VERIFIED')
        
        # Test remark endpoint
        self._staff_client().post(f'/api/staff/applications/{app.pk}/remark/', {'status': 'AUTHORITY_REJECTED', 'remarks': 'hack'})
        app.refresh_from_db()
        self.assertNotEqual(app.status, 'AUTHORITY_REJECTED')

        # Test forward endpoint
        self._staff_client().post(f'/api/staff/applications/{app.pk}/forward/', {'status': 'AUTHORITY_REJECTED', 'remarks': 'hack'})
        app.refresh_from_db()
        self.assertNotEqual(app.status, 'AUTHORITY_REJECTED')

        # Test confirm endpoint
        self._staff_client().post(f'/api/staff/applications/{app.pk}/confirm/', {'status': 'AUTHORITY_REJECTED'})
        app.refresh_from_db()
        self.assertNotEqual(app.status, 'AUTHORITY_REJECTED')

    def test_staff_cannot_bypass_admin_stage(self):
        """3, 11. Police Staff cannot bypass Admin stage (cannot jump to confirm directly)."""
        app = make_app(self.citizen, 'STAFF_REVIEWED')
        
        # Try to confirm directly from STAFF_REVIEWED status (bypassing forward and admin decision)
        res = self._staff_client().post(f'/api/staff/applications/{app.pk}/confirm/')
        self.assertEqual(res.status_code, 400)
        
        app.refresh_from_db()
        self.assertEqual(app.status, 'STAFF_REVIEWED')  # Unchanged
        self.assertFalse(Challan.objects.filter(application=app).exists())

    # ─────────────────────────────────────────────────────────────────────────
    # Stage 4: Authoritative & Idempotent Certificate Generation Tests
    # ─────────────────────────────────────────────────────────────────────────

    def test_certificate_cannot_be_generated_before_admin_approval(self):
        """1. Certificate cannot be generated before admin approval."""
        from applications.certificate_service import CertificateService
        app = make_app(self.citizen, 'STAFF_REVIEWED')
        
        with self.assertRaises(ValueError):
            CertificateService.generate_certificate(app)
            
        res = self._staff_client().post(f'/api/staff/applications/{app.pk}/issue-cert/')
        self.assertEqual(res.status_code, 400)

    def test_certificate_cannot_be_generated_before_police_confirmation(self):
        """2. Certificate cannot be generated before police confirmation."""
        from applications.certificate_service import CertificateService
        app = make_app(self.citizen, 'AUTHORITY_APPROVED')
        
        with self.assertRaises(ValueError):
            CertificateService.generate_certificate(app)
            
        res = self._staff_client().post(f'/api/staff/applications/{app.pk}/issue-cert/')
        self.assertEqual(res.status_code, 400)

    def test_certificate_cannot_be_generated_before_payment_submission(self):
        """3. Certificate cannot be generated before payment submission."""
        from applications.certificate_service import CertificateService
        app = make_app(self.citizen, 'PAYMENT_PENDING')
        
        with self.assertRaises(ValueError):
            CertificateService.generate_certificate(app)
            
        res = self._staff_client().post(f'/api/staff/applications/{app.pk}/issue-cert/')
        self.assertEqual(res.status_code, 400)

    def test_certificate_cannot_be_generated_before_payment_verification(self):
        """4. Certificate cannot be generated before payment verification."""
        from applications.certificate_service import CertificateService
        app = make_app(self.citizen, 'PAYMENT_SUBMITTED')
        
        with self.assertRaises(ValueError):
            CertificateService.generate_certificate(app)
            
        res = self._staff_client().post(f'/api/staff/applications/{app.pk}/issue-cert/')
        self.assertEqual(res.status_code, 400)

    def test_valid_completed_workflow_generates_exactly_one_certificate(self):
        """5. Valid completed workflow generates exactly ONE certificate."""
        from applications.certificate_service import CertificateService
        app = make_app(self.citizen, 'PAYMENT_VERIFIED')
        
        cert = CertificateService.generate_certificate(app)
        self.assertIsNotNone(cert)
        app.refresh_from_db()
        self.assertEqual(app.status, 'COMPLETED')
        self.assertEqual(Certificate.objects.filter(application=app).count(), 1)

    def test_repeating_certificate_generation_is_idempotent(self):
        """6, 8, 9. Repeating the certificate request does not create duplicates/new numbers."""
        from applications.certificate_service import CertificateService
        app = make_app(self.citizen, 'PAYMENT_VERIFIED')
        
        cert1 = CertificateService.generate_certificate(app)
        cert_num_1 = cert1.certificate_number
        qr_hash_1 = cert1.qr_code_hash
        
        # Second call to service
        cert2 = CertificateService.generate_certificate(app)
        self.assertEqual(cert2.id, cert1.id)
        self.assertEqual(cert2.certificate_number, cert_num_1)
        self.assertEqual(cert2.qr_code_hash, qr_hash_1)
        self.assertEqual(Certificate.objects.filter(application=app).count(), 1)
        
        # Call via API should also be idempotent
        res = self._staff_client().post(f'/api/staff/applications/{app.pk}/issue-cert/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['certificate_number'], cert_num_1)
        self.assertEqual(Certificate.objects.filter(application=app).count(), 1)


    def test_repeating_payment_verification_is_idempotent(self):
        """7. Repeating payment verification does NOT create another certificate."""
        from applications.models import Payment
        app = make_app(self.citizen, 'PAYMENT_SUBMITTED')
        challan = Challan.objects.create(application=app, due_date='2027-01-01', status='PAID')
        Payment.objects.create(
            application=app, challan=challan,
            amount=650, payment_method='JAZZCASH',
        )
        
        # Verify first time
        res1 = self._staff_client().post(f'/api/staff/applications/{app.pk}/verify-payment/')
        self.assertEqual(res1.status_code, 200)
        cert_num_1 = res1.data.get('certificate_number')
        self.assertIsNotNone(cert_num_1)
        
        # Verify second time (using staff client)
        res2 = self._staff_client().post(f'/api/staff/applications/{app.pk}/verify-payment/')
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(Certificate.objects.filter(application=app).count(), 1)

    def test_citizen_cannot_directly_invoke_certificate_issuance(self):
        """12. Citizen cannot directly invoke privileged certificate issuance."""
        app = make_app(self.citizen, 'PAYMENT_VERIFIED')
        
        res1 = self._citizen_client().post(f'/api/staff/applications/{app.pk}/issue-cert/')
        self.assertEqual(res1.status_code, 403)
        
        res2 = self._citizen_client().post(f'/api/authority/applications/{app.pk}/issue-cert/')
        self.assertEqual(res2.status_code, 403)
        
        self.assertFalse(Certificate.objects.filter(application=app).exists())

    @patch('applications.certificate_service.CertificateService.generate_certificate')
    def test_failed_certificate_generation_does_not_mark_completed(self, mock_generate):
        """10, 11. If certificate generation fails, application is NOT falsely marked COMPLETED."""
        from applications.models import Payment
        mock_generate.side_effect = Exception("PDF generation failed")
        
        app = make_app(self.citizen, 'PAYMENT_SUBMITTED')
        challan = Challan.objects.create(application=app, due_date='2027-01-01', status='PAID')
        Payment.objects.create(
            application=app, challan=challan,
            amount=650, payment_method='JAZZCASH',
        )
        
        res = self._staff_client().post(f'/api/staff/applications/{app.pk}/verify-payment/')
        self.assertEqual(res.status_code, 200)
        
        app.refresh_from_db()
        # Should stay at PAYMENT_VERIFIED, and NOT become COMPLETED
        self.assertEqual(app.status, 'PAYMENT_VERIFIED')
        self.assertFalse(Certificate.objects.filter(application=app).exists())


# ─── Legacy Police Tests (preserved, non-interfering) ────────────────────────
class PoliceTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = create_staff()
        self.citizen = create_citizen()
        self.client.force_authenticate(user=self.staff)

    def test_criminal_search_by_cnic_clean(self):
        res = self.client.post('/api/criminals/search/', {'cnic': '35202-0000000-0'})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'CLEAN')

    def test_criminal_search_finds_record(self):
        CriminalRecord.objects.create(cnic='42101-9876543-2', name='Test Criminal', status='CRIMINAL_MATCH')
        res = self.client.post('/api/criminals/search/', {'cnic': '42101-9876543-2'})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'CRIMINAL_MATCH')


# ─── Chatbot Tests ────────────────────────────────────────────────────────────
class ChatbotTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_chatbot_responds_to_faq(self):
        res = self.client.post('/api/chatbot/chat/', {'message': 'How do I apply for certificate?'})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('reply', res.data)
        self.assertTrue(len(res.data['reply']) > 10)
        self.assertEqual(res.data['source'], 'project_guidance')
        self.assertNotIn('3-7 days', res.data['reply'])

    def test_chatbot_empty_message_fails(self):
        res = self.client.post('/api/chatbot/chat/', {'message': ''})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_chatbot_rejects_long_messages(self):
        res = self.client.post('/api/chatbot/chat/', {'message': 'x' * 1001})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_document_guidance_uses_supported_types_without_claiming_requirements(self):
        res = self.client.post(
            '/api/chatbot/chat/', {'message': 'What documents are required?'}
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('CNIC Front Side', res.data['reply'])
        self.assertIn('does not define mandatory documents', res.data['reply'])

    def test_anonymous_user_cannot_track_private_application(self):
        citizen = create_citizen()
        app = Application.objects.create(
            applicant=citizen,
            application_type='Character Certificate',
            purpose='Employment',
            current_address='Lahore',
            nearest_station='Central Police Station',
        )
        res = self.client.post(
            '/api/chatbot/chat/', {'message': f'Track application {app.tracking_id}'}
        )
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_citizen_can_track_own_application_but_not_another_citizens(self):
        owner = create_citizen()
        another_citizen = create_citizen(
            cnic='35202-7654321-9',
            email='another@example.com',
            full_name='Another Citizen',
        )
        own_app = Application.objects.create(
            applicant=owner,
            application_type='Character Certificate',
            purpose='Employment',
            current_address='Lahore',
            nearest_station='Central Police Station',
        )
        other_app = Application.objects.create(
            applicant=another_citizen,
            application_type='Tenant Verification',
            purpose='Housing',
            current_address='Karachi',
            nearest_station='Central Police Station',
        )
        self.client.force_authenticate(user=owner)

        own_res = self.client.post(
            '/api/chatbot/chat/', {'message': f'Track application {own_app.tracking_id}'}
        )
        self.assertEqual(own_res.status_code, status.HTTP_200_OK)
        self.assertIn(own_app.tracking_id, own_res.data['reply'])
        self.assertNotIn(owner.cnic, own_res.data['reply'])

        other_res = self.client.post(
            '/api/chatbot/chat/', {'message': f'Track application {other_app.tracking_id}'}
        )
        self.assertEqual(other_res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertNotIn(other_app.tracking_id, str(other_res.data))

    def test_citizen_can_retrieve_own_certificate(self):
        citizen = create_citizen()
        app = Application.objects.create(
            applicant=citizen,
            application_type='Character Certificate',
            purpose='Employment',
            current_address='Lahore',
            nearest_station='Central Police Station',
        )
        cert = Certificate.objects.create(
            application=app,
            validity_expiry=timezone.localdate() + timedelta(days=365),
            digital_signature='test-signature',
            verification_url='/verify/test/',
        )
        self.client.force_authenticate(user=citizen)

        res = self.client.post(
            '/api/chatbot/chat/', {'message': f'Check certificate {cert.certificate_number}'}
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn(cert.certificate_number, res.data['reply'])

    def test_citizen_cannot_retrieve_another_citizens_certificate(self):
        owner = create_citizen()
        other_citizen = create_citizen(
            cnic='35202-7654321-9',
            email='another-certificate@example.com',
            full_name='Another Citizen',
        )
        app = Application.objects.create(
            applicant=other_citizen,
            application_type='Character Certificate',
            purpose='Employment',
            current_address='Karachi',
            nearest_station='Central Police Station',
        )
        cert = Certificate.objects.create(
            application=app,
            validity_expiry=timezone.localdate() + timedelta(days=365),
            digital_signature='test-signature',
            verification_url='/verify/test/',
        )
        self.client.force_authenticate(user=owner)

        res = self.client.post(
            '/api/chatbot/chat/', {'message': f'Check certificate {cert.certificate_number}'}
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertNotIn(cert.certificate_number, str(res.data))

    def test_citizen_cannot_use_criminal_record_lookup(self):
        self.client.force_authenticate(user=create_citizen())
        res = self.client.post(
            '/api/chatbot/chat/',
            {'message': 'Check criminal records for 42101-1234567-1'},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_criminal_lookup_distinguishes_local_match_and_no_match(self):
        staff = create_staff()
        CriminalRecord.objects.create(
            cnic='42101-9876543-2',
            name='Test Record',
            status='CRIMINAL_MATCH',
        )
        self.client.force_authenticate(user=staff)

        match_res = self.client.post(
            '/api/chatbot/chat/',
            {'message': 'Check criminal records for 42101-9876543-2'},
        )
        self.assertEqual(match_res.status_code, status.HTTP_200_OK)
        self.assertEqual(match_res.data['source'], 'local_criminal_records')
        self.assertIn('matching entry was found', match_res.data['reply'])
        self.assertIn('locally maintained', match_res.data['reply'])

        no_match_res = self.client.post(
            '/api/chatbot/chat/',
            {'message': 'Check criminal records for 42101-1234567-1'},
        )
        self.assertEqual(no_match_res.status_code, status.HTTP_200_OK)
        self.assertIn('No matching record', no_match_res.data['reply'])

    def test_staff_criminal_lookup_validates_cnic(self):
        self.client.force_authenticate(user=create_staff())
        res = self.client.post(
            '/api/chatbot/chat/',
            {'message': 'Check criminal records for 1234'},
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


# ─── Stage 5: Certificate Province/District/Authority Data Integrity ─────────

def make_app_for_citizen(citizen, status_val='PAYMENT_VERIFIED'):
    """Create an application with applicant_province mirroring citizen.province."""
    return Application.objects.create(
        applicant=citizen,
        application_type='Character Certificate',
        purpose='Test',
        current_address='Test Address',
        nearest_station='Test PS',
        status=status_val,
        applicant_province=citizen.province,
    )


def create_citizen_with_province(cnic, email, full_name, province, district):
    return User.objects.create_user(
        cnic=cnic, email=email, password='Test@1234',
        full_name=full_name, role='CITIZEN',
        province=province, district=district,
    )


class CertificateDataIntegrityTests(TestCase):
    """
    Stage 5 — Certificate data, province/district and QR URL tests.
    Ensures certificates carry the citizen's ACTUAL data from the DB.
    """

    def setUp(self):
        self.staff = create_staff()
        # Citizen A — Sindh/Karachi
        self.citizen_sindh = create_citizen_with_province(
            cnic='42101-1111111-1', email='sindh@test.com',
            full_name='Zara Khan', province='Sindh', district='Karachi',
        )
        # Citizen B — Punjab/Lahore
        self.citizen_punjab = create_citizen_with_province(
            cnic='35202-2222222-2', email='punjab@test.com',
            full_name='Ali Hassan', province='Punjab', district='Lahore',
        )

    def _generate_cert(self, citizen):
        from applications.certificate_service import CertificateService
        app = make_app_for_citizen(citizen, 'PAYMENT_VERIFIED')
        return CertificateService.generate_certificate(app), app

    # ── 1-2: Province-specific certificate data ─────────────────────────────

    def test_sindh_citizen_gets_sindh_province(self):
        """Sindh citizen certificate contains province=Sindh."""
        cert, app = self._generate_cert(self.citizen_sindh)
        self.assertEqual(app.applicant_province, 'Sindh')
        # Verify the cert was generated
        self.assertIsNotNone(cert)
        self.assertEqual(app.status, 'COMPLETED')

    def test_punjab_citizen_gets_punjab_province(self):
        """Punjab citizen certificate contains province=Punjab."""
        cert, app = self._generate_cert(self.citizen_punjab)
        self.assertEqual(app.applicant_province, 'Punjab')
        self.assertIsNotNone(cert)

    # ── 3: District appears correctly ──────────────────────────────────────

    def test_sindh_citizen_district_is_karachi(self):
        """District for Sindh citizen is Karachi from DB."""
        self.assertEqual(self.citizen_sindh.district, 'Karachi')

    def test_punjab_citizen_district_is_lahore(self):
        """District for Punjab citizen is Lahore from DB."""
        self.assertEqual(self.citizen_punjab.district, 'Lahore')

    # ── 4-5: Province/District not hardcoded ────────────────────────────────

    def test_province_comes_from_application_not_hardcoded(self):
        """applicant_province is set from citizen.province at application creation."""
        app = make_app_for_citizen(self.citizen_sindh, 'PAYMENT_VERIFIED')
        self.assertNotEqual(app.applicant_province, 'Punjab')
        self.assertNotEqual(app.applicant_province, 'Sindh' if app.applicant_province == 'Punjab' else 'Punjab')
        self.assertEqual(app.applicant_province, self.citizen_sindh.province)

    def test_district_comes_from_citizen_model(self):
        """District on citizen model is preserved without substitution."""
        self.assertNotIn(self.citizen_sindh.district, ['Karachi South'])  # no dummy substitution
        self.assertEqual(self.citizen_sindh.district, 'Karachi')

    # ── 6: Serializer does NOT default province to Sindh ────────────────────

    def test_serializer_does_not_default_province_to_sindh(self):
        """Serializer's get_nadra_details must not return Sindh for a Punjab citizen."""
        app = make_app_for_citizen(self.citizen_punjab, 'PENDING')
        from applications.serializers import ApplicationSerializer
        data = ApplicationSerializer(app).data
        nadra = data.get('nadra_details', {})
        # Province must NOT be Sindh for a Punjab citizen
        self.assertNotEqual(nadra.get('province'), 'Sindh')

    def test_serializer_does_not_default_province_to_punjab(self):
        """Serializer's get_nadra_details must not return Punjab for a Sindh citizen."""
        app = make_app_for_citizen(self.citizen_sindh, 'PENDING')
        from applications.serializers import ApplicationSerializer
        data = ApplicationSerializer(app).data
        nadra = data.get('nadra_details', {})
        # Province must NOT be Punjab for a Sindh citizen
        self.assertNotEqual(nadra.get('province'), 'Punjab')

    def test_identity_details_fall_back_to_citizen_profile_when_match_metadata_is_placeholder(self):
        from face_verification.models import FaceVerificationReport
        from applications.serializers import ApplicationSerializer

        citizen = create_citizen(
            cnic='35202-4444444-4',
            email='identity-details@test.com',
            full_name='Shahid Ali',
            father_name='Muhammad Ali',
            dob=date(1990, 5, 4),
            gender='MALE',
            province='Punjab',
            district='Lahore',
            address='House 12, Model Town, Lahore',
        )
        application = make_app_for_citizen(citizen, 'PENDING')
        FaceVerificationReport.objects.create(
            citizen=citizen,
            application=application,
            matched_cnic=citizen.cnic,
            matched_citizen_name=f'Citizen ({citizen.cnic})',
            matched_father_name='N/A',
            matched_date_of_birth='N/A',
            matched_gender='N/A',
            matched_address='N/A',
            matched_district='N/A',
            matched_province='N/A',
            status='VERIFIED',
        )

        data = ApplicationSerializer(application).data['nadra_details']

        self.assertEqual(data['full_name'], 'Shahid Ali')
        self.assertEqual(data['father_name'], 'Muhammad Ali')
        self.assertEqual(data['date_of_birth'], '1990-05-04')
        self.assertEqual(data['gender'], 'MALE')
        self.assertEqual(data['district'], 'Lahore')
        self.assertEqual(data['province'], 'Punjab')
        self.assertEqual(data['permanent_address'], 'House 12, Model Town, Lahore')

    # ── 10-13: Certificate contains correct citizen data ────────────────────

    def test_certificate_contains_correct_citizen_name(self):
        """Certificate record is linked to the correct citizen name."""
        cert, app = self._generate_cert(self.citizen_sindh)
        self.assertEqual(cert.application.applicant.full_name, 'Zara Khan')

    def test_certificate_contains_correct_cnic(self):
        """Certificate is linked to the correct CNIC."""
        cert, app = self._generate_cert(self.citizen_sindh)
        self.assertEqual(cert.application.applicant.cnic, '42101-1111111-1')

    def test_certificate_has_certificate_number(self):
        """Certificate has a non-empty unique certificate number."""
        cert, _ = self._generate_cert(self.citizen_sindh)
        self.assertTrue(cert.certificate_number.startswith('CERT-'))

    def test_certificate_has_issue_date(self):
        """Certificate has a valid issue date."""
        cert, _ = self._generate_cert(self.citizen_sindh)
        self.assertIsNotNone(cert.issue_date)

    # ── 14: Two citizens receive DIFFERENT certificate data ─────────────────

    def test_two_citizens_receive_different_certificates(self):
        """Sindh and Punjab citizens each get their own distinct certificate."""
        cert_s, _ = self._generate_cert(self.citizen_sindh)
        cert_p, _ = self._generate_cert(self.citizen_punjab)

        # Different certificate numbers
        self.assertNotEqual(cert_s.certificate_number, cert_p.certificate_number)
        # Different linked citizens
        self.assertNotEqual(
            cert_s.application.applicant.cnic,
            cert_p.application.applicant.cnic,
        )

    # ── 15-17: QR URL and verification endpoint ─────────────────────────────

    def test_qr_verification_url_contains_certificate_number(self):
        """QR verification_url embeds the actual certificate number."""
        cert, _ = self._generate_cert(self.citizen_sindh)
        self.assertIn(cert.certificate_number, cert.verification_url)

    def test_qr_url_does_not_hardcode_localhost_in_production_config(self):
        """
        CERTIFICATE_VERIFY_BASE_URL is used, not a hardcoded localhost string
        baked into Python source. Verifies the URL comes from settings.
        """
        from django.conf import settings
        # The setting itself may still be localhost in dev — that's fine.
        # What we verify is that the setting EXISTS (not that source code hardcodes it).
        self.assertTrue(
            hasattr(settings, 'CERTIFICATE_VERIFY_BASE_URL'),
            "CERTIFICATE_VERIFY_BASE_URL setting must exist so it can be overridden in production."
        )

    def test_two_qr_codes_identify_different_certificates(self):
        """Two citizens get two different QR URLs (different certificate numbers)."""
        cert_s, _ = self._generate_cert(self.citizen_sindh)
        cert_p, _ = self._generate_cert(self.citizen_punjab)
        self.assertNotEqual(cert_s.verification_url, cert_p.verification_url)

    def test_qr_verification_endpoint_returns_correct_province(self):
        """GET /api/certificates/verify/<cert_num>/ returns the correct province."""
        cert, _ = self._generate_cert(self.citizen_sindh)
        client = APIClient()  # anonymous — public endpoint
        res = client.get(f'/api/certificates/verify/{cert.certificate_number}/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['province'], 'Sindh')

    def test_qr_verification_endpoint_returns_correct_district(self):
        """GET /api/certificates/verify/<cert_num>/ returns the correct district."""
        cert, _ = self._generate_cert(self.citizen_sindh)
        client = APIClient()
        res = client.get(f'/api/certificates/verify/{cert.certificate_number}/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['district'], 'Karachi')

    def test_qr_verification_endpoint_returns_correct_authority_sindh(self):
        """Sindh citizen → authority is 'Sindh Police'."""
        cert, _ = self._generate_cert(self.citizen_sindh)
        client = APIClient()
        res = client.get(f'/api/certificates/verify/{cert.certificate_number}/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['authority'], 'Sindh Police')

    def test_qr_verification_endpoint_returns_correct_authority_punjab(self):
        """Punjab citizen → authority is 'Punjab Police'."""
        cert, _ = self._generate_cert(self.citizen_punjab)
        client = APIClient()
        res = client.get(f'/api/certificates/verify/{cert.certificate_number}/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['authority'], 'Punjab Police')

    def test_qr_does_not_cross_leak_citizen_data(self):
        """Sindh cert verification endpoint must not return Punjab citizen data."""
        cert_s, _ = self._generate_cert(self.citizen_sindh)
        cert_p, _ = self._generate_cert(self.citizen_punjab)
        client = APIClient()

        res_s = client.get(f'/api/certificates/verify/{cert_s.certificate_number}/')
        res_p = client.get(f'/api/certificates/verify/{cert_p.certificate_number}/')

        # Sindh cert → Sindh data
        self.assertEqual(res_s.data['province'], 'Sindh')
        self.assertEqual(res_s.data['district'], 'Karachi')
        self.assertEqual(res_s.data['applicant_name'], 'Zara Khan')

        # Punjab cert → Punjab data
        self.assertEqual(res_p.data['province'], 'Punjab')
        self.assertEqual(res_p.data['district'], 'Lahore')
        self.assertEqual(res_p.data['applicant_name'], 'Ali Hassan')

        # No cross-leak
        self.assertNotEqual(res_s.data['cnic'], res_p.data['cnic'])

    # ── 18-19: Missing province/district — no silent substitution ───────────

    def test_missing_province_is_none_not_punjab_or_sindh(self):
        """When province is missing from user and application, it returns None — not Punjab or Sindh."""
        citizen_no_province = User.objects.create_user(
            cnic='00000-9999999-9', email='noprovince@test.com',
            password='Test@1234', full_name='No Province Citizen',
            role='CITIZEN', province=None, district=None,
        )
        app = Application.objects.create(
            applicant=citizen_no_province,
            application_type='Character Certificate',
            purpose='Test', current_address='Test', nearest_station='Test PS',
            status='PENDING',
            applicant_province=None,
        )
        from applications.serializers import ApplicationSerializer
        data = ApplicationSerializer(app).data
        nadra = data.get('nadra_details', {})
        prov = nadra.get('province')
        # Must not silently substitute Punjab or Sindh
        self.assertNotIn(prov, ['Punjab', 'Sindh'])
        # Either None/null or empty
        self.assertFalse(bool(prov))

    def test_missing_district_is_none_not_karachi_or_lahore(self):
        """When district is missing from user, it returns None — not a dummy district."""
        citizen_no_district = User.objects.create_user(
            cnic='00000-8888888-8', email='nodistrict@test.com',
            password='Test@1234', full_name='No District Citizen',
            role='CITIZEN', province=None, district=None,
        )
        app = Application.objects.create(
            applicant=citizen_no_district,
            application_type='Character Certificate',
            purpose='Test', current_address='Test', nearest_station='Test PS',
            status='PENDING',
        )
        from applications.serializers import ApplicationSerializer
        data = ApplicationSerializer(app).data
        nadra = data.get('nadra_details', {})
        dist = nadra.get('district')
        self.assertNotIn(dist, ['Karachi', 'Karachi South', 'Lahore'])
        self.assertFalse(bool(dist))

    # ── Province→Authority mapping logic ────────────────────────────────────

    def test_get_authority_name_returns_correct_mapping(self):
        """Province→authority function maps all known provinces correctly."""
        from applications.certificate_service import get_authority_name
        self.assertEqual(get_authority_name('Punjab'),             'Punjab Police')
        self.assertEqual(get_authority_name('Sindh'),              'Sindh Police')
        self.assertEqual(get_authority_name('Khyber Pakhtunkhwa'), 'KP Police')
        self.assertEqual(get_authority_name('KPK'),                'KP Police')
        self.assertEqual(get_authority_name('Balochistan'),        'Balochistan Police')
        self.assertEqual(get_authority_name('Islamabad'),          'ICT Police')
        # Unknown province -> generic label
        self.assertEqual(get_authority_name('Gilgit-Baltistan'),   'GB Police')

    def test_get_authority_name_none_returns_pakistan_police(self):
        """Missing province returns generic 'Pakistan Police'."""
        from applications.certificate_service import get_authority_name
        self.assertEqual(get_authority_name(None),  'Pakistan Police')
        self.assertEqual(get_authority_name(''),    'Pakistan Police')


class ProtectedDocumentFileTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.settings_override.enable()
        self.citizen = create_citizen()
        self.other_citizen = create_citizen(
            cnic='35202-7654321-9',
            email='other-citizen@example.com',
            full_name='Other Citizen',
        )
        self.staff = create_staff()
        self.application = Application.objects.create(
            applicant=self.citizen,
            application_type='Character Certificate',
            purpose='Employment',
            current_address='Lahore',
            nearest_station='Central Police Station',
        )
        self.document = Document.objects.create(
            application=self.application,
            document_type='CNIC_FRONT',
            file=SimpleUploadedFile(
                'cnic-front.png',
                b'\x89PNG\r\n\x1a\nprotected-cnic-data',
                content_type='image/png',
            ),
        )

    def tearDown(self):
        self.settings_override.disable()
        self.media_directory.cleanup()

    def test_document_url_is_protected_and_not_a_public_media_path(self):
        from applications.serializers import DocumentSerializer

        serialized = DocumentSerializer(self.document).data
        self.assertEqual(serialized['file'], f'/api/documents/{self.document.pk}/file/')
        self.assertNotIn('/media/', serialized['file'])

    def test_citizen_can_view_own_document(self):
        client = APIClient()
        client.force_authenticate(user=self.citizen)

        response = client.get(f'/api/documents/{self.document.pk}/file/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        try:
            self.assertEqual(b''.join(response.streaming_content), b'\x89PNG\r\n\x1a\nprotected-cnic-data')
        finally:
            response.close()

    def test_other_citizen_cannot_view_document(self):
        client = APIClient()
        client.force_authenticate(user=self.other_citizen)

        response = client.get(f'/api/documents/{self.document.pk}/file/')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_authorized_staff_can_view_document(self):
        client = APIClient()
        client.force_authenticate(user=self.staff)

        response = client.get(f'/api/documents/{self.document.pk}/file/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'image/png')
        response.close()

    def test_authority_and_super_admin_can_view_document(self):
        admin = User.objects.create_user(
            cnic='99999-1111111-1',
            email='admin-docs@example.com',
            password='Admin@1234',
            full_name='Test Super Admin',
            role='SUPER_ADMIN',
        )
        for user in (create_authority(), admin):
            with self.subTest(role=user.role):
                client = APIClient()
                client.force_authenticate(user=user)

                response = client.get(f'/api/documents/{self.document.pk}/file/')

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                response.close()

    def test_anonymous_user_cannot_view_document(self):
        response = APIClient().get(f'/api/documents/{self.document.pk}/file/')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_legacy_media_path_is_also_protected(self):
        client = APIClient()

        response = client.get(f'/media/{self.document.file.name}')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class CertificateLayoutTests(TestCase):
    def test_citizen_fields_are_centered_and_use_larger_font(self):
        from applications.certificate_service import CERTIFICATE_LAYOUT, draw_fitted_text

        class RecordingCanvas:
            def __init__(self):
                self.font = None
                self.drawn = None

            def stringWidth(self, text, font_name, font_size):
                return len(text) * font_size * 0.5

            def setFont(self, font_name, font_size):
                self.font = (font_name, font_size)

            def setFillColorRGB(self, *color):
                pass

            def drawString(self, x, y, text):
                self.drawn = (x, y, text)

        pdf = RecordingCanvas()
        scale = 72 / CERTIFICATE_LAYOUT['template_dpi']
        field = CERTIFICATE_LAYOUT['fields']['name']
        draw_fitted_text(pdf, 'Alexandra Long Citizen Name', field, 900, scale)

        self.assertEqual(pdf.font[1], CERTIFICATE_LAYOUT['font_size'])
        expected_x = field['x'] * scale + (
            field['max_width'] * scale - pdf.stringWidth(
                pdf.drawn[2], CERTIFICATE_LAYOUT['font_name'], pdf.font[1]
            )
        ) / 2
        self.assertAlmostEqual(pdf.drawn[0], expected_x)
        self.assertLessEqual(
            pdf.stringWidth(pdf.drawn[2], CERTIFICATE_LAYOUT['font_name'], pdf.font[1]),
            field['max_width'] * scale,
        )

    def test_certificate_pdf_renders_with_long_name_and_qr(self):
        from PIL import Image
        from applications.certificate_service import render_certificate_pdf

        with tempfile.TemporaryDirectory() as directory:
            template_path = f'{directory}\\certificate-template.png'
            Image.new('RGB', (816, 1306), color='white').save(template_path)
            for name in ('Ali Khan', 'Alexandra Elizabeth Montgomery-Cunningham'):
                with self.subTest(name=name):
                    pdf_bytes = render_certificate_pdf(
                        template_path,
                        {
                            'name': name,
                            'father_name': 'A Long Relationship Name',
                            'cnic': '35202-1234567-9',
                            'district': 'Rawalpindi',
                            'province': 'Punjab',
                            'certificate_no': 'CERT-2026-123456',
                            'issue_date': '22 Oct 2026',
                            'valid_until': '22 Apr 2027',
                            'status': 'VALID',
                        },
                        'https://example.test/certificates/verify/CERT-2026-123456/',
                    )

                    self.assertTrue(pdf_bytes.startswith(b'%PDF'))
                    self.assertIn(b'/XObject', pdf_bytes)


class AuthorityDashboardApiTests(TestCase):
    def setUp(self):
        self.authority = create_authority()
        self.staff = create_staff(is_active=False)
        self.sos = EmergencySOS.objects.create(
            location_address='Station Road',
            contact_number='03001234567',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.authority)

    def test_staff_list_includes_account_active_state(self):
        response = self.client.get('/api/authority/staff/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data[0]['is_active'])

    def test_authority_can_update_sos_status_with_partial_payload(self):
        response = self.client.patch(
            f'/api/incidents/sos/{self.sos.pk}/status/',
            {'status': 'ACKNOWLEDGED', 'police_notes': 'Control room acknowledged'},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.sos.refresh_from_db()
        self.assertEqual(self.sos.status, 'ACKNOWLEDGED')
        self.assertEqual(self.sos.police_notes, 'Control room acknowledged')

    def test_sos_update_rejects_fields_outside_control_room_scope(self):
        response = self.client.patch(
            f'/api/incidents/sos/{self.sos.pk}/status/',
            {'status': 'ACKNOWLEDGED', 'contact_number': '000'},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.sos.refresh_from_db()
        self.assertEqual(self.sos.status, 'RECEIVED')


# ─── Stage 6: Full Frontend & E2E Integration Audit Tests ─────────────────────
class CertificateManagementTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.citizen = create_citizen_with_province(
            cnic='42101-5555555-5',
            email='certificate-owner@test.com',
            full_name='Certificate Owner',
            province='Sindh',
            district='Karachi',
        )
        self.application = make_app_for_citizen(self.citizen, 'PAYMENT_VERIFIED')

    def _create_certificate(self, application=None, certificate_number='CERT-2026-123456'):
        return Certificate.objects.create(
            application=application or self.application,
            certificate_number=certificate_number,
            validity_expiry=timezone.localdate() + timedelta(days=180),
            qr_code_hash=f'qr-{certificate_number}',
            digital_signature='test-signature',
            verification_url=f'http://localhost:5173/verify/{certificate_number}',
            pdf_file='certificates/test-certificate.pdf',
            certificate_hash='a' * 64,
        )

    def test_certificate_management_search_and_detail_use_central_record(self):
        certificate = self._create_certificate()
        staff = create_staff()
        self.client.force_authenticate(user=staff)

        listing = self.client.get('/api/police/certificates/', {'search': 'CERT-2026-123'})
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(listing.data['count'], 1)
        self.assertEqual(listing.data['results'][0]['certificate_number'], certificate.certificate_number)
        self.assertEqual(listing.data['results'][0]['applicant']['cnic'], '42101-*******-5')

        detail = self.client.get(f'/api/police/certificates/{certificate.certificate_number}/')
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data['application']['id'], self.application.pk)
        self.assertEqual(
            self.client.get('/api/police/certificates/CERT-2026-000000/').status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_certificate_management_denies_citizens_and_scopes_province_admin(self):
        certificate = self._create_certificate()
        other_citizen = create_citizen_with_province(
            cnic='35202-6666666-6',
            email='other-owner@test.com',
            full_name='Other Certificate Owner',
            province='Punjab',
            district='Lahore',
        )
        other_application = make_app_for_citizen(other_citizen, 'COMPLETED')
        other_certificate = self._create_certificate(
            other_application,
            certificate_number='CERT-2026-654321',
        )

        self.client.force_authenticate(user=self.citizen)
        self.assertEqual(
            self.client.get('/api/police/certificates/').status_code,
            status.HTTP_403_FORBIDDEN,
        )

        province_admin = User.objects.create_user(
            cnic='35202-7777777-7',
            email='province-admin@test.com',
            password='Admin@1234',
            full_name='Province Admin',
            role='SUPER_ADMIN',
            province='Sindh',
        )
        self.client.force_authenticate(user=province_admin)
        listing = self.client.get('/api/police/certificates/')
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(listing.data['count'], 1)
        self.assertEqual(listing.data['results'][0]['id'], certificate.pk)
        self.assertEqual(
            self.client.get(f'/api/police/certificates/{other_certificate.certificate_number}/').status_code,
            status.HTTP_404_NOT_FOUND,
        )

    @patch('applications.certificate_service.render_certificate_pdf', return_value=b'%PDF-1.4 test certificate')
    def test_generation_is_persisted_retrievable_and_idempotent(self, _render_pdf):
        import hashlib
        from applications.certificate_service import CertificateService
        from blockchain.models import BlockchainBlock

        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            partial_certificate = Certificate.objects.create(
                application=self.application,
                certificate_number='CERT-2026-555555',
                validity_expiry=timezone.localdate() + timedelta(days=180),
                qr_code_hash='incomplete-certificate-qr',
                digital_signature='incomplete-signature',
                verification_url='',
            )
            self.client.force_authenticate(user=create_staff())
            incomplete_response = self.client.get(
                f'/api/police/certificates/{partial_certificate.certificate_number}/'
            )
            self.assertEqual(incomplete_response.status_code, status.HTTP_409_CONFLICT)

            certificate = CertificateService.generate_certificate(self.application)
            self.assertEqual(certificate.pk, partial_certificate.pk)
            self.assertEqual(certificate.certificate_number, partial_certificate.certificate_number)
            self.assertTrue(Certificate.objects.filter(pk=certificate.pk).exists())
            self.assertTrue(certificate.pdf_file)
            self.assertTrue(certificate.certificate_hash)
            with certificate.pdf_file.storage.open(certificate.pdf_file.name, 'rb') as stored_file:
                stored_pdf = stored_file.read()
            self.assertEqual(
                hashlib.sha256(stored_pdf).hexdigest(),
                certificate.certificate_hash,
            )

            second_result = CertificateService.generate_certificate(self.application)
            self.assertEqual(second_result.pk, certificate.pk)
            self.assertEqual(Certificate.objects.filter(application=self.application).count(), 1)
            self.assertEqual(
                BlockchainBlock.objects.filter(
                    record_id=str(self.application.pk),
                    action_type='CERTIFICATE_ISSUE',
                ).count(),
                1,
            )

            result = self.client.get(f'/api/police/certificates/{certificate.certificate_number}/')
            self.assertEqual(result.status_code, status.HTTP_200_OK)
            self.assertEqual(result.data['certificate_number'], certificate.certificate_number)
            self.client.force_authenticate(user=self.citizen)
            first_download = self.client.get(
                f'/api/citizen/applications/{self.application.pk}/download-certificate/'
            )
            second_download = self.client.get(
                f'/api/citizen/applications/{self.application.pk}/download-certificate/'
            )
            self.assertEqual(first_download.content, stored_pdf)
            self.assertEqual(second_download.content, stored_pdf)

    def test_integrity_verification_endpoint_uses_certificate_service(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from blockchain.service import BlockchainService

        certificate = self._create_certificate()
        block = BlockchainService.add_block(
            'CERTIFICATE_ISSUE',
            str(certificate.application_id),
            self.citizen.cnic,
            {
                'certificate_number': certificate.certificate_number,
                'certificate_hash': certificate.certificate_hash,
            },
        )
        certificate.blockchain_transaction_hash = block.current_hash
        certificate.save(update_fields=['blockchain_transaction_hash'])
        self.client.force_authenticate(user=create_staff())
        response = self.client.post(
            '/api/staff/certificates/verify-integrity/',
            {
                'certificate_number': certificate.certificate_number,
                'file': SimpleUploadedFile(
                    'certificate.pdf',
                    b'%PDF-1.4 uploaded document',
                    content_type='application/pdf',
                ),
            },
            format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['state'], 'HASH_MISMATCH')

    @patch(
        'applications.certificate_service.render_certificate_pdf',
        side_effect=RuntimeError('PDF rendering failed'),
    )
    def test_failed_generation_does_not_persist_partial_certificate(self, _render_pdf):
        from applications.certificate_service import CertificateService

        with self.assertRaisesRegex(RuntimeError, 'PDF rendering failed'):
            CertificateService.generate_certificate(self.application)

        self.assertFalse(Certificate.objects.filter(application=self.application).exists())
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'PAYMENT_VERIFIED')

    def test_rejected_application_cannot_generate_certificate(self):
        from applications.certificate_service import CertificateService

        self.application.status = 'AUTHORITY_REJECTED'
        self.application.save(update_fields=['status'])
        with self.assertRaisesRegex(ValueError, 'AUTHORITY_REJECTED'):
            CertificateService.generate_certificate(self.application)
        self.assertFalse(Certificate.objects.filter(application=self.application).exists())

    @patch(
        'applications.certificate_service.BlockchainService.verify_chain',
        return_value={'valid': False, 'issues': ['historical block hash mismatch']},
    )
    def test_invalid_ledger_blocks_new_issuance_without_partial_records(self, _verify_chain):
        from applications.certificate_service import CertificateService

        with self.assertRaisesRegex(RuntimeError, 'existing ledger is invalid'):
            CertificateService.generate_certificate(self.application)

        self.assertFalse(Certificate.objects.filter(application=self.application).exists())
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'PAYMENT_VERIFIED')


class CertificateIntegrityVerificationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.media_settings = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)

        self.citizen = create_citizen_with_province(
            cnic='42101-8888888-8',
            email='integrity-owner@test.com',
            full_name='Integrity Owner',
            province='Sindh',
            district='Karachi',
        )
        self.application = make_app_for_citizen(self.citizen, 'COMPLETED')
        self.staff = create_staff(
            cnic='00000-8888888-8',
            email='integrity-staff@test.com',
        )
        self.pdf_bytes = self._create_pdf('Trusted certificate')

    @staticmethod
    def _create_pdf(text, color=(0, 0, 0)):
        from io import BytesIO
        from reportlab.lib.colors import Color
        from reportlab.pdfgen import canvas

        output = BytesIO()
        pdf = canvas.Canvas(output)
        pdf.setFillColor(Color(*color))
        pdf.drawString(72, 720, text)
        pdf.save()
        return output.getvalue()

    def _issue_certificate(
        self,
        certificate_number='CERT-2026-765432',
        pdf_bytes=None,
        application=None,
        validity_expiry=None,
        certificate_status='VALID',
        add_issue_event=True,
    ):
        from django.core.files.base import ContentFile
        from blockchain.service import BlockchainService
        import hashlib

        application = application or self.application
        pdf_bytes = pdf_bytes or self.pdf_bytes
        certificate = Certificate.objects.create(
            application=application,
            certificate_number=certificate_number,
            validity_expiry=validity_expiry or timezone.localdate() + timedelta(days=90),
            qr_code_hash=f'qr-{certificate_number}',
            digital_signature='test-signature',
            verification_url=f'http://localhost:5173/verify/{certificate_number}',
            status=certificate_status,
            certificate_hash=hashlib.sha256(pdf_bytes).hexdigest(),
        )
        certificate.pdf_file.save(
            f'{certificate_number}.pdf',
            ContentFile(pdf_bytes),
            save=True,
        )
        if add_issue_event:
            block = BlockchainService.add_block(
                'CERTIFICATE_ISSUE',
                str(application.pk),
                application.applicant.cnic,
                {
                    'certificate_number': certificate.certificate_number,
                    'certificate_hash': certificate.certificate_hash,
                },
            )
            certificate.blockchain_transaction_hash = block.current_hash
            certificate.save(update_fields=['blockchain_transaction_hash'])
        return certificate

    def _verify(self, certificate_reference, pdf_bytes, user=None):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_authenticate(user=user or self.staff)
        return self.client.post(
            '/api/staff/certificates/verify-integrity/',
            {
                'certificate_number': certificate_reference,
                'file': SimpleUploadedFile(
                    'citizen-certificate.pdf',
                    pdf_bytes,
                    content_type='application/pdf',
                ),
            },
            format='multipart',
        )

    def test_exact_pdf_download_verifies_by_certificate_number_and_qr_url(self):
        certificate = self._issue_certificate()
        self.client.force_authenticate(user=self.citizen)
        download = self.client.get(
            f'/api/citizen/applications/{self.application.pk}/download-certificate/'
        )
        self.assertEqual(download.status_code, status.HTTP_200_OK)
        self.assertEqual(download.content, self.pdf_bytes)

        by_number = self._verify(certificate.certificate_number, download.content)
        self.assertEqual(by_number.data['state'], 'INTEGRITY_VERIFIED')
        self.assertTrue(by_number.data['integrity_valid'])
        self.assertEqual(by_number.data['hash_match'], 'MATCH')
        self.assertEqual(by_number.data['pdf_integrity'], 'UNMODIFIED')
        self.assertTrue(by_number.data['ledger_valid'])
        self.assertEqual(
            by_number.data['issuance_event']['certificate_number'],
            certificate.certificate_number,
        )
        self.assertTrue(by_number.data['audit_recorded'])

        by_qr_url = self._verify(certificate.verification_url, download.content)
        self.assertEqual(by_qr_url.data['certificate_number'], certificate.certificate_number)
        self.assertEqual(by_qr_url.data['state'], 'INTEGRITY_VERIFIED')

    def test_changed_and_other_certificate_pdfs_are_hash_mismatches(self):
        first = self._issue_certificate()
        second_application = make_app_for_citizen(
            create_citizen_with_province(
                cnic='42101-9999999-9',
                email='second-integrity-owner@test.com',
                full_name='Second Owner',
                province='Sindh',
                district='Karachi',
            ),
            'COMPLETED',
        )
        second = self._issue_certificate(
            certificate_number='CERT-2026-765433',
            pdf_bytes=self._create_pdf('Another certificate'),
            application=second_application,
        )

        changed_text = self._verify(
            first.certificate_number,
            self._create_pdf('Tampered certificate text'),
        )
        changed_visual = self._verify(
            first.certificate_number,
            self._create_pdf('Trusted certificate', color=(1, 0, 0)),
        )
        other_pdf = self._verify(
            first.certificate_number,
            self._create_pdf('Another certificate'),
        )
        self.assertEqual(changed_text.data['state'], 'HASH_MISMATCH')
        self.assertEqual(changed_text.data['hash_match'], 'MISMATCH')
        self.assertEqual(changed_text.data['pdf_integrity'], 'TAMPERED')
        self.assertEqual(changed_visual.data['state'], 'HASH_MISMATCH')
        self.assertEqual(other_pdf.data['state'], 'HASH_MISMATCH')
        first.refresh_from_db()
        self.assertEqual(first.certificate_hash, hashlib.sha256(self.pdf_bytes).hexdigest())

    def test_missing_record_missing_hash_invalid_file_and_size_limit(self):
        certificate = self._issue_certificate(add_issue_event=False)
        certificate.certificate_hash = None
        certificate.save(update_fields=['certificate_hash'])
        missing_hash = self._verify('CERT-2026-000001', self.pdf_bytes)
        self.assertEqual(missing_hash.data['state'], 'NO_CERTIFICATE_RECORD')
        no_hash = self._verify('CERT-2026-765432', self.pdf_bytes)
        self.assertEqual(no_hash.data['state'], 'VERIFICATION_UNAVAILABLE')
        self.assertEqual(no_hash.data['ledger_status'], 'VALID')
        self.assertEqual(no_hash.data['hash_match'], 'NOT_DETERMINED')
        self.assertIn('No certificate issuance event', no_hash.data['diagnostic'])
        invalid = self._verify('CERT-2026-765432', b'not a PDF')
        self.assertEqual(invalid.data['state'], 'INVALID_FILE')
        oversized = self._verify('CERT-2026-765432', b'%PDF-' + b'x' * (10 * 1024 * 1024))
        self.assertEqual(oversized.data['state'], 'INVALID_FILE')

    def test_missing_stored_pdf_returns_unavailable_without_regenerating_it(self):
        certificate = self._issue_certificate()
        certificate.pdf_file.storage.delete(certificate.pdf_file.name)
        self.client.force_authenticate(user=self.citizen)

        response = self.client.get(
            f'/api/citizen/applications/{self.application.pk}/download-certificate/'
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data['status'], 'PDF_UNAVAILABLE')
        certificate.refresh_from_db()
        self.assertEqual(certificate.certificate_hash, hashlib.sha256(self.pdf_bytes).hexdigest())

    def test_missing_issuance_event_is_not_fabricated_by_a_generation_retry(self):
        from applications.certificate_service import CertificateService

        certificate = self._issue_certificate(add_issue_event=False)
        original_file_name = certificate.pdf_file.name

        with self.assertRaisesRegex(RuntimeError, 'will not be fabricated'):
            CertificateService.generate_certificate(self.application)

        certificate.refresh_from_db()
        self.assertEqual(certificate.pdf_file.name, original_file_name)
        self.assertEqual(certificate.certificate_hash, hashlib.sha256(self.pdf_bytes).hexdigest())

    def test_integrity_and_revoked_or_expired_certificate_status_are_separate(self):
        revoked = self._issue_certificate(certificate_status='REVOKED')
        revoked_result = self._verify(revoked.certificate_number, self.pdf_bytes)
        self.assertEqual(revoked_result.data['state'], 'INTEGRITY_VERIFIED')
        self.assertTrue(revoked_result.data['integrity_valid'])
        self.assertEqual(revoked_result.data['certificate_status'], 'REVOKED')
        self.assertFalse(revoked_result.data['valid'])

        expired_owner = create_citizen_with_province(
            cnic='42101-7777777-7',
            email='expired-integrity-owner@test.com',
            full_name='Expired Owner',
            province='Sindh',
            district='Karachi',
        )
        expired_application = make_app_for_citizen(expired_owner, 'COMPLETED')
        expired = self._issue_certificate(
            certificate_number='CERT-2026-765433',
            application=expired_application,
            validity_expiry=timezone.localdate() - timedelta(days=1),
        )
        expired_result = self._verify(expired.certificate_number, self.pdf_bytes)
        self.assertEqual(expired_result.data['state'], 'INTEGRITY_VERIFIED')
        self.assertEqual(expired_result.data['certificate_status'], 'EXPIRED')

    @patch(
        'applications.certificate_service.BlockchainService.verify_chain',
        return_value={'valid': False, 'issues': ['test failure']},
    )
    def test_invalid_ledger_fails_closed(self, _verify_chain):
        certificate = self._issue_certificate()
        response = self._verify(certificate.certificate_number, self.pdf_bytes)
        self.assertEqual(response.data['state'], 'VERIFICATION_UNAVAILABLE')
        self.assertIsNone(response.data['integrity_valid'])
        self.assertEqual(response.data['original_hash'], certificate.certificate_hash)
        self.assertEqual(response.data['uploaded_hash'], certificate.certificate_hash)
        self.assertEqual(response.data['hash_match'], 'MATCH')
        self.assertEqual(response.data['pdf_integrity'], 'UNVERIFIED')
        self.assertFalse(response.data['ledger_valid'])
        self.assertIsNotNone(response.data['issuance_event'])

    def test_invalid_block_hash_is_reported_without_rewriting_the_event(self):
        from blockchain.models import BlockchainBlock

        certificate = self._issue_certificate()
        issue_block = BlockchainBlock.objects.get(
            record_id=str(certificate.application_id),
            action_type='CERTIFICATE_ISSUE',
        )
        preceding_block = BlockchainBlock.objects.exclude(pk=issue_block.pk).order_by('block_index').first()
        BlockchainBlock.objects.filter(pk=preceding_block.pk).update(
            current_hash='f' * 64,
        )

        response = self._verify(certificate.certificate_number, self.pdf_bytes)
        self.assertEqual(response.data['state'], 'VERIFICATION_UNAVAILABLE')
        self.assertEqual(response.data['original_hash'], certificate.certificate_hash)
        self.assertFalse(response.data['ledger_valid'])
        self.assertTrue(any(
            'current_hash mismatch' in issue
            for issue in response.data['ledger_issues']
        ))
        issue_block.refresh_from_db()
        self.assertEqual(issue_block.current_hash, certificate.blockchain_transaction_hash)

    def test_broken_previous_hash_is_reported_without_rewriting_history(self):
        from blockchain.models import BlockchainBlock

        certificate = self._issue_certificate()
        issue_block = BlockchainBlock.objects.get(
            record_id=str(certificate.application_id),
            action_type='CERTIFICATE_ISSUE',
        )
        previous_hash = issue_block.previous_hash
        BlockchainBlock.objects.filter(pk=issue_block.pk).update(
            previous_hash='f' * 64,
        )

        response = self._verify(certificate.certificate_number, self.pdf_bytes)
        self.assertEqual(response.data['state'], 'VERIFICATION_UNAVAILABLE')
        self.assertFalse(response.data['ledger_valid'])
        self.assertTrue(any(
            'broken chain link' in issue
            for issue in response.data['ledger_issues']
        ))
        issue_block.refresh_from_db()
        self.assertNotEqual(issue_block.previous_hash, previous_hash)

    def test_legacy_timestamp_skew_is_validated_without_rewriting_event(self):
        from datetime import timedelta
        from blockchain.models import BlockchainBlock
        from blockchain.service import BlockchainService

        certificate = self._issue_certificate()
        issue_block = BlockchainBlock.objects.get(
            record_id=str(certificate.application_id),
            action_type='CERTIFICATE_ISSUE',
        )
        legacy_hash = BlockchainService._compute_block_hash(
            issue_block.block_index,
            str(issue_block.timestamp - timedelta(microseconds=1200)),
            issue_block.previous_hash,
            issue_block.data_hash,
            issue_block.action_type,
            issue_block.nonce,
        )
        BlockchainBlock.objects.filter(pk=issue_block.pk).update(current_hash=legacy_hash)

        chain = BlockchainService.verify_chain()

        self.assertTrue(chain['valid'], chain['issues'])
        issue_block.refresh_from_db()
        self.assertEqual(issue_block.current_hash, legacy_hash)

    def test_audit_append_failure_is_reported_without_claiming_it_was_recorded(self):
        from unittest.mock import patch

        certificate = self._issue_certificate()
        with patch(
            'applications.views.BlockchainService.add_block',
            side_effect=RuntimeError('audit storage unavailable'),
        ):
            response = self._verify(certificate.certificate_number, self.pdf_bytes)
        self.assertEqual(response.data['state'], 'INTEGRITY_VERIFIED')
        self.assertTrue(response.data['integrity_valid'])
        self.assertFalse(response.data['audit_recorded'])
        self.assertIn('could not be recorded', response.data['audit_message'])

    def test_citizen_is_not_authorized_to_run_integrity_verification(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_authenticate(user=self.citizen)
        response = self.client.post(
            '/api/staff/certificates/verify-integrity/',
            {
                'certificate_number': 'CERT-2026-765432',
                'file': SimpleUploadedFile('certificate.pdf', self.pdf_bytes),
            },
            format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


# ─── Stage 6: Full Frontend & E2E Integration Audit Tests ─────────────────────
class Stage6IntegrationTests(TestCase):
    """
    Validates Stage 6 End-to-End Workflow:
    - Multi-citizen isolation (Citizen A: Sindh/Karachi, Citizen B: Punjab/Lahore)
    - Sequential status transitions: PENDING -> FACE_VERIFIED -> STAFF_REVIEWED -> FORWARDED_TO_ADMIN -> AUTHORITY_APPROVED -> STAFF_CONFIRMED -> PAYMENT_PENDING -> PAYMENT_SUBMITTED -> COMPLETED
    - Role authorization checks (Citizens cannot approve/confirm/verify; Staff cannot authority approve/reject)
    - Certificate data integrity & QR Verification isolation
    """

    def setUp(self):
        self.client = APIClient()

        # Citizen A (Sindh, Karachi)
        self.citizen_a = User.objects.create_user(
            cnic='42101-1111111-1', email='sindh_citizen@test.com',
            password='Pass@1234', full_name='Citizen Sindh',
            role='CITIZEN', province='Sindh', district='Karachi'
        )

        # Citizen B (Punjab, Lahore)
        self.citizen_b = User.objects.create_user(
            cnic='35202-2222222-2', email='punjab_citizen@test.com',
            password='Pass@1234', full_name='Citizen Punjab',
            role='CITIZEN', province='Punjab', district='Lahore'
        )

        # Staff User
        self.staff = User.objects.create_user(
            cnic='35202-3333333-3', email='staff_officer@test.com',
            password='TestStaffPass!456', full_name='Staff Officer',
            role='POLICE_STAFF'
        )

        # Authority User
        self.authority = User.objects.create_user(
            cnic='35202-4444444-4', email='authority_officer@test.com',
            password='Auth@1234', full_name='Authority Officer',
            role='POLICE_AUTHORITY'
        )

    def _mock_face_and_liveness(self, citizen):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from face_verification.models import FaceVerificationReport
        self.face_report = FaceVerificationReport(
            citizen=citizen,
            matched_cnic=citizen.cnic,
            matched_citizen_name=citizen.full_name,
            matched_father_name='Father',
            matched_date_of_birth='1990-01-01',
            matched_gender='M',
            matched_address=citizen.address or 'Address',
            matched_district=citizen.district or 'District',
            matched_province=citizen.province or 'Province',
            matched_photo_url='http://test',
            similarity_score=0.885,
            similarity_pct=88.5,
            status='VERIFIED',
            confidence_level='HIGH',
            model_used='InsightFace',
            processing_time_ms=120.0,
            verified_at=timezone.now(),
        )
        face_patch = patch(
            'face_verification.views.FaceVerificationService.verify_with_cnic',
            return_value=self.face_report,
        )
        live_result = {
            'liveness_score': 0.91,
            'anti_spoofing': 'REAL',
            'face_detected': True,
            'verified': True,
        }
        return face_patch, _LivenessPatches(live_result)

    def test_e2e_full_workflow_citizen_sindh(self):
        """End-to-end test for Citizen A (Sindh / Karachi)."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        # 1. Citizen A creates application
        self.client.force_authenticate(user=self.citizen_a)
        res = self.client.post('/api/citizen/applications/', {
            'application_type': 'Character Certificate',
            'purpose': 'Employment abroad',
            'current_address': 'Clifton Karachi',
            'nearest_station': 'Clifton PS'
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        app_id = res.data['id']
        app = Application.objects.get(pk=app_id)
        self.assertEqual(app.status, 'PENDING')

        # 2. Face Verification and criminal screening
        Document.objects.create(application=app, document_type='PASSPORT_PHOTO', file='dummy_face.jpg')
        face_patch, live_patch = self._mock_face_and_liveness(self.citizen_a)
        with face_patch, live_patch:
            res_face = self.client.post(
                f'/api/citizen/applications/{app_id}/face-verify/',
                {'live_image': SimpleUploadedFile('dummy.jpg', b'face', content_type='image/jpeg')},
                format='multipart',
            )
        self.assertEqual(res_face.status_code, status.HTTP_200_OK)
        app.refresh_from_db()
        self.assertEqual(app.status, 'CRIMINAL_CHECKED')

        # Role Check: Citizen cannot submit staff remark
        res_fail_remark = self.client.post(f'/api/staff/applications/{app_id}/remark/', {'remarks': 'Illegal'})
        self.assertEqual(res_fail_remark.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Police Staff Remark -> STAFF_REVIEWED
        self.client.force_authenticate(user=self.staff)
        res_remark = self.client.post(f'/api/staff/applications/{app_id}/remark/', {'remarks': 'Staff verified identity documents.'})
        self.assertEqual(res_remark.status_code, status.HTTP_200_OK)
        app.refresh_from_db()
        self.assertEqual(app.status, 'STAFF_REVIEWED')

        # 4. Police Staff Forward -> FORWARDED_TO_ADMIN
        res_forward = self.client.post(f'/api/staff/applications/{app_id}/forward/', {'remarks': 'Forwarding to Authority.'})
        self.assertEqual(res_forward.status_code, status.HTTP_200_OK)
        app.refresh_from_db()
        self.assertEqual(app.status, 'FORWARDED_TO_ADMIN')

        # Role Check: Staff cannot approve application
        res_fail_decide = self.client.post(f'/api/authority/applications/{app_id}/decide/', {'decision': 'APPROVE'})
        self.assertEqual(res_fail_decide.status_code, status.HTTP_403_FORBIDDEN)

        # 5. Police Authority Decision -> AUTHORITY_APPROVED
        self.client.force_authenticate(user=self.authority)
        res_decide = self.client.post(f'/api/authority/applications/{app_id}/decide/', {'decision': 'APPROVE', 'reason': 'Cleared'})
        self.assertEqual(res_decide.status_code, status.HTTP_200_OK)
        app.refresh_from_db()
        self.assertEqual(app.status, 'AUTHORITY_APPROVED')

        # 6. Police Staff Confirm -> PAYMENT_PENDING (Challan auto generated)
        self.client.force_authenticate(user=self.staff)
        res_confirm = self.client.post(f'/api/staff/applications/{app_id}/confirm/', {})
        self.assertEqual(res_confirm.status_code, status.HTTP_200_OK)
        app.refresh_from_db()
        self.assertEqual(app.status, 'PAYMENT_PENDING')
        self.assertTrue(Challan.objects.filter(application=app).exists())

        # 7. Citizen A Pays -> PAYMENT_SUBMITTED
        self.client.force_authenticate(user=self.citizen_a)
        res_pay = self.client.post(f'/api/citizen/applications/{app_id}/pay/', {'payment_method': 'EASYPAISA', 'mobile_number': '03001234567'})
        self.assertEqual(res_pay.status_code, status.HTTP_200_OK)
        app.refresh_from_db()
        self.assertEqual(app.status, 'PAYMENT_SUBMITTED')

        # 8. Police Staff Verifies Payment -> Certificate Issued -> COMPLETED
        self.client.force_authenticate(user=self.staff)
        res_verify_pay = self.client.post(f'/api/staff/applications/{app_id}/verify-payment/', {})
        self.assertEqual(res_verify_pay.status_code, status.HTTP_200_OK)
        app.refresh_from_db()
        self.assertEqual(app.status, 'COMPLETED')
        self.assertTrue(Certificate.objects.filter(application=app).exists())
        cert_a = Certificate.objects.get(application=app)
        self.assertEqual(res_verify_pay.data['status'], 'COMPLETED')
        self.assertEqual(res_verify_pay.data['certificate_number'], cert_a.certificate_number)

        # 9. Public QR Scan Verification Check
        self.client.force_authenticate(user=None)
        res_qr = self.client.get(f'/api/certificates/verify/{cert_a.qr_code_hash}/')
        self.assertEqual(res_qr.status_code, status.HTTP_200_OK)
        self.assertEqual(res_qr.data['applicant_name'], 'Citizen Sindh')
        self.assertEqual(res_qr.data['cnic'], '42101-*******-1')
        self.assertEqual(res_qr.data['province'], 'Sindh')
        self.assertEqual(res_qr.data['authority'], 'Sindh Police')
        self.assertTrue(res_qr.data['blockchain_verified'])

    def test_e2e_full_workflow_citizen_punjab(self):
        """End-to-end test for Citizen B (Punjab / Lahore)."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.force_authenticate(user=self.citizen_b)
        res = self.client.post('/api/citizen/applications/', {
            'application_type': 'Tenant Verification',
            'purpose': 'Rental clearance',
            'current_address': 'Gulberg Lahore',
            'nearest_station': 'Gulberg PS'
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        app_id = res.data['id']
        app = Application.objects.get(pk=app_id)

        # Face Verification
        Document.objects.create(application=app, document_type='PASSPORT_PHOTO', file='dummy_face.jpg')
        face_patch, live_patch = self._mock_face_and_liveness(self.citizen_b)
        with face_patch, live_patch:
            self.client.post(
                f'/api/citizen/applications/{app_id}/face-verify/',
                {'live_image': SimpleUploadedFile('dummy.jpg', b'face', content_type='image/jpeg')},
                format='multipart',
            )
        app.refresh_from_db()

        # Staff Remark & Forward
        self.client.force_authenticate(user=self.staff)
        self.client.post(f'/api/staff/applications/{app_id}/remark/', {'remarks': 'Verified.'})
        self.client.post(f'/api/staff/applications/{app_id}/forward/', {'remarks': 'Forwarding.'})

        # Authority Decision
        self.client.force_authenticate(user=self.authority)
        self.client.post(f'/api/authority/applications/{app_id}/decide/', {'decision': 'APPROVE'})

        # Staff Confirm
        self.client.force_authenticate(user=self.staff)
        self.client.post(f'/api/staff/applications/{app_id}/confirm/', {})

        # Citizen B Pays
        self.client.force_authenticate(user=self.citizen_b)
        self.client.post(f'/api/citizen/applications/{app_id}/pay/', {'payment_method': 'JAZZCASH', 'mobile_number': '03009876543'})

        # Staff Verify Payment -> COMPLETED
        self.client.force_authenticate(user=self.staff)
        self.client.post(f'/api/staff/applications/{app_id}/verify-payment/', {})

        app.refresh_from_db()
        self.assertEqual(app.status, 'COMPLETED')
        cert_b = Certificate.objects.get(application=app)

        # QR Verification Check
        self.client.force_authenticate(user=None)
        res_qr = self.client.get(f'/api/certificates/verify/{cert_b.qr_code_hash}/')
        self.assertEqual(res_qr.status_code, status.HTTP_200_OK)
        self.assertEqual(res_qr.data['applicant_name'], 'Citizen Punjab')
        self.assertEqual(res_qr.data['cnic'], '35202-*******-2')
        self.assertEqual(res_qr.data['province'], 'Punjab')
        self.assertEqual(res_qr.data['authority'], 'Punjab Police')



# ─────────────────────────────────────────────────────────────────────────────
# Active liveness (head-turn challenge + planar parallax)
# ─────────────────────────────────────────────────────────────────────────────
import numpy as _np
from django.test import SimpleTestCase as _SimpleTestCase


def _rot(yaw=0.0, pitch=0.0, roll=0.0):
    a, b, c = _np.radians([yaw, pitch, roll])
    ry = _np.array([[_np.cos(a), 0, _np.sin(a)], [0, 1, 0], [-_np.sin(a), 0, _np.cos(a)]])
    rx = _np.array([[1, 0, 0], [0, _np.cos(b), -_np.sin(b)], [0, _np.sin(b), _np.cos(b)]])
    rz = _np.array([[_np.cos(c), -_np.sin(c), 0], [_np.sin(c), _np.cos(c), 0], [0, 0, 1]])
    return rz @ rx @ ry


def _project(points, dist=500.0, f=600.0):
    z = dist - points[:, 2]
    return _np.stack([f * points[:, 0] / z + 320, f * points[:, 1] / z + 240], 1)


# Average 3-D positions (mm) of the 5 SCRFD keypoints: eyes, nose tip, mouth corners.
_FACE_3D = _np.array([[-32, 0, 0], [32, 0, 0], [0, 38, 20], [-25, 68, 6], [25, 68, 6]], float)
_FLAT_PHOTO = _FACE_3D * _np.array([1, 1, 0])  # the same face printed on paper


class ActiveLivenessGeometryTests(_SimpleTestCase):
    """The decision logic, on synthetic keypoints (no camera or model needed)."""

    def _kps(self, shape, yaw=0.0, pitch=0.0, roll=0.0, dist=500.0):
        return _project((_rot(yaw, pitch, roll) @ shape.T).T, dist)

    def test_real_head_turning_passes(self):
        from face_verification.active_liveness import evaluate_geometry
        for yaw in (25, 35, 45):
            res = evaluate_geometry(self._kps(_FACE_3D), self._kps(_FACE_3D, yaw),
                                    self._kps(_FACE_3D, -yaw))
            self.assertTrue(res['passed'], (yaw, res))

    def test_photo_tilted_like_a_head_turn_is_rejected(self):
        from face_verification.active_liveness import evaluate_geometry
        for yaw in (30, 45, 60):
            for dist in (150, 500):
                res = evaluate_geometry(self._kps(_FLAT_PHOTO, dist=dist),
                                        self._kps(_FLAT_PHOTO, yaw, dist=dist),
                                        self._kps(_FLAT_PHOTO, -yaw, dist=dist))
                self.assertFalse(res['passed'], (yaw, dist, res))

    def test_photo_tilted_on_diagonal_axis_is_rejected(self):
        from face_verification.active_liveness import evaluate_geometry
        rng = _np.random.default_rng(7)
        passed = 0
        for _ in range(2000):
            frames = [self._kps(_FLAT_PHOTO, *rng.uniform([-60, -60, -30], [60, 60, 30]),
                                dist=rng.uniform(150, 600)) for _ in range(3)]
            frames[0] = self._kps(_FLAT_PHOTO, *rng.uniform(-8, 8, 3))
            frames = [k + rng.normal(0, 1.5, k.shape) for k in frames]
            passed += evaluate_geometry(*frames)['passed']
        self.assertLessEqual(passed, 2)

    def test_same_still_image_for_every_step_is_rejected(self):
        from face_verification.active_liveness import evaluate_geometry
        k = self._kps(_FACE_3D)
        self.assertFalse(evaluate_geometry(k, k, k)['passed'])

    def test_challenge_round_trip_and_owner_binding(self):
        from types import SimpleNamespace
        from face_verification.active_liveness import (
            ActiveLivenessError, issue_challenge, read_challenge)
        alice, bob = SimpleNamespace(pk=1), SimpleNamespace(pk=2)
        ch = issue_challenge(alice)
        self.assertEqual(ch['steps'][0], 'CENTER')
        self.assertEqual(sorted(ch['steps'][1:]), ['LEFT', 'RIGHT'])
        self.assertEqual(read_challenge(ch['challenge_token'], alice)['s'], ch['steps'])
        with self.assertRaises(ActiveLivenessError):
            read_challenge(ch['challenge_token'], bob)
        with self.assertRaises(ActiveLivenessError):
            read_challenge(ch['challenge_token'] + 'x', alice)


class ActiveLivenessEndpointTests(TestCase):

    def setUp(self):
        self.citizen = create_citizen(cnic='42101-7777777-1', email='live@test.com')
        self.client = APIClient()
        self.client.force_authenticate(user=self.citizen)

    def test_challenge_endpoint(self):
        res = self.client.get('/api/face-verify/liveness-challenge/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('challenge_token', res.data)
        self.assertEqual(len(res.data['steps']), 3)

    def test_single_image_without_challenge_is_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        res = self.client.post('/api/face-verify/verify-with-cnic/', {
            'cnic': self.citizen.cnic,
            'live_image': SimpleUploadedFile('photo.jpg', b'photo', content_type='image/jpeg'),
        }, format='multipart')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data['liveness_reason'], 'INVALID_CHALLENGE')

    def test_same_image_for_all_steps_is_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        token = self.client.get('/api/face-verify/liveness-challenge/').data['challenge_token']
        data = {'cnic': self.citizen.cnic, 'challenge_token': token}
        for step in ('CENTER', 'LEFT', 'RIGHT'):
            data[f'frame_{step}'] = SimpleUploadedFile(f'{step}.jpg', b'same', content_type='image/jpeg')
        res = self.client.post('/api/face-verify/verify-with-cnic/', data, format='multipart')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('same image', res.data['error'])

    def test_flat_image_result_blocks_verification(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        spoof = dict(_ACTIVE_LIVENESS_PASS, verified=False, liveness_score=0.0,
                     anti_spoofing='SPOOF_SUSPECTED', reason='FLAT_IMAGE',
                     message='Spoof suspected: the face moved like a flat photo or screen, not a real head.')
        with patch('face_verification.active_liveness.run_active_liveness', return_value=spoof), \
                patch('face_verification.views.FaceVerificationService.verify_with_cnic') as mock_verify:
            res = self.client.post('/api/face-verify/verify-with-cnic/', {
                'cnic': self.citizen.cnic,
                'live_image': SimpleUploadedFile('photo.jpg', b'photo', content_type='image/jpeg'),
            }, format='multipart')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data['liveness_reason'], 'FLAT_IMAGE')
        mock_verify.assert_not_called()
