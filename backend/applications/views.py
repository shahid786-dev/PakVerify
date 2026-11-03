from rest_framework import status, permissions, generics
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.views import APIView
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.http import FileResponse, HttpResponse
from django.db.models import Count, Sum, Q
from django.db.models.functions import ExtractMonth
import requests, datetime, io, uuid, qrcode
import json
import logging
import mimetypes
import os

logger = logging.getLogger(__name__)

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.units import inch

from .models import Application, Document, Challan, Certificate, Payment
from .serializers import (
    ApplicationSerializer, ApplicationCreateSerializer, DocumentSerializer,
    CertificateManagementSerializer,
)

from blockchain.service import BlockchainService
from notifications.service import (
    notify_application_submitted, notify_ai_verified, notify_nadra_verified,
    notify_criminal_checked, notify_staff_reviewed, notify_authority_decision,
    notify_challan_generated, notify_payment_confirmed, notify_certificate_ready,
)

User = get_user_model()


class StaffWorkflowPermission(permissions.BasePermission):
    """Allow operational police roles to read and process workflow records."""

    allowed_roles = {'POLICE_STAFF', 'POLICE_AUTHORITY', 'SUPER_ADMIN'}

    def has_permission(self, request, view):
        return (
            bool(request.user and request.user.is_authenticated)
            and request.user.role in self.allowed_roles
        )


class PublicPortalStatsView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        verified_citizens = Application.objects.filter(
            status='COMPLETED',
            certificate__pdf_file__gt='',
            certificate__certificate_hash__gt='',
        ).values('applicant_id').distinct().count()
        return Response({'verified_citizens': verified_citizens})


class CertificateManagementPagination(LimitOffsetPagination):
    default_limit = 20
    max_limit = 100


class CertificateManagementQueryset:
    allowed_roles = {'POLICE_STAFF', 'POLICE_AUTHORITY', 'SUPER_ADMIN'}

    @classmethod
    def for_user(cls, user):
        if user.role not in cls.allowed_roles:
            return Certificate.objects.none()

        queryset = Certificate.objects.select_related(
            'application__applicant',
            'application__staff_reviewed_by',
            'application__admin_decided_by',
            'application__payment_record',
        )
        if user.role == 'SUPER_ADMIN' and user.province:
            queryset = queryset.filter(
                Q(application__applicant_province=user.province)
                | Q(application__applicant__province=user.province)
            )
        return queryset


class CertificateManagementListView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = CertificateManagementPagination

    def get(self, request):
        if request.user.role not in CertificateManagementQueryset.allowed_roles:
            return Response({'error': 'Unauthorized'}, status=status.HTTP_403_FORBIDDEN)

        queryset = CertificateManagementQueryset.for_user(request.user)
        queryset = queryset.exclude(
            pdf_file=''
        ).exclude(
            pdf_file__isnull=True
        ).exclude(
            certificate_hash=''
        ).exclude(
            certificate_hash__isnull=True
        )
        search = request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(certificate_number__icontains=search)
                | Q(application__tracking_id__icontains=search)
                | Q(application__applicant__full_name__icontains=search)
                | Q(application__applicant__cnic__icontains=search)
            )
        queryset = queryset.order_by('-issue_date', '-pk')
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = CertificateManagementSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class CertificateManagementDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, certificate_number):
        if request.user.role not in CertificateManagementQueryset.allowed_roles:
            return Response({'error': 'Unauthorized'}, status=status.HTTP_403_FORBIDDEN)
        certificate = CertificateManagementQueryset.for_user(request.user).filter(
            certificate_number=certificate_number
        ).first()
        if certificate is None:
            return Response(
                {'error': 'Certificate not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not certificate.pdf_file or not certificate.certificate_hash:
            return Response(
                {
                    'error': 'This certificate record exists, but issuance is incomplete. Retry certificate generation.',
                    'status': 'ISSUANCE_INCOMPLETE',
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(CertificateManagementSerializer(certificate).data)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _generate_challan(application):
    due_date = timezone.now().date() + datetime.timedelta(days=14)
    challan  = Challan.objects.create(application=application, due_date=due_date, status='PENDING')
    application.status = 'PAYMENT_PENDING'
    application.save()
    BlockchainService.add_block(
        'CHALLAN_GENERATE', str(application.id),
        application.applicant.cnic,
        {'challan_number': challan.challan_number, 'amount': str(challan.amount),
         'tracking_id': application.tracking_id},
    )
    notify_challan_generated(
        application.applicant, application.tracking_id,
        challan.challan_number, challan.amount,
    )
    return challan


def _generate_qr_bytes(url: str) -> bytes:
    qr = qrcode.QRCode(version=1, box_size=6, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


# ─── Citizen — Applications ───────────────────────────────────────────────────

class ApplicationListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        return ApplicationCreateSerializer if self.request.method == 'POST' else ApplicationSerializer

    def get_queryset(self):
        user = self.request.user
        if user.role == 'SUPER_ADMIN':
            # Province-scoped admins see their own province's applications
            if user.province:
                return Application.objects.filter(applicant_province=user.province).order_by('-submitted_at')
            return Application.objects.all().order_by('-submitted_at')
        if user.role in ['POLICE_STAFF', 'POLICE_AUTHORITY']:
            return Application.objects.all().order_by('-submitted_at')
        return Application.objects.filter(applicant=user).order_by('-submitted_at')

    def create(self, request, *args, **kwargs):
        if request.user.role != 'CITIZEN':
            return Response({'error': 'Only citizens can create applications.'}, status=status.HTTP_403_FORBIDDEN)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        application = serializer.save(
            applicant=request.user,
            applicant_province=request.user.province or ''
        )

        BlockchainService.add_block(
            'APPLICATION_SUBMIT', str(application.id), request.user.cnic,
            {'tracking_id': application.tracking_id,
             'application_type': application.application_type},
        )
        notify_application_submitted(request.user, application.tracking_id)

        return Response(ApplicationSerializer(application).data, status=status.HTTP_201_CREATED)


class ApplicationDetailView(generics.RetrieveAPIView):
    serializer_class   = ApplicationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role in ['POLICE_STAFF', 'POLICE_AUTHORITY', 'SUPER_ADMIN']:
            return Application.objects.all()
        return Application.objects.filter(applicant=user)


def _can_view_application_document(user, document):
    if user.role == 'CITIZEN':
        return document.application.applicant_id == user.pk
    return user.role in StaffWorkflowPermission.allowed_roles


def _document_file_response(document):
    filename = os.path.basename(document.file.name)
    guessed_type, _ = mimetypes.guess_type(filename)
    inline_types = {
        'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/bmp',
        'application/pdf',
    }
    content_type = guessed_type if guessed_type in inline_types else 'application/octet-stream'
    response = FileResponse(
        document.file.open('rb'),
        as_attachment=content_type not in inline_types,
        filename=filename,
        content_type=content_type,
    )
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


class DocumentFileView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        try:
            document = Document.objects.select_related(
                'application__applicant'
            ).get(pk=pk)
        except Document.DoesNotExist:
            return Response({'error': 'Document not found.'}, status=status.HTTP_404_NOT_FOUND)

        if not _can_view_application_document(request.user, document):
            return Response({'error': 'Document not found.'}, status=status.HTTP_404_NOT_FOUND)
        return _document_file_response(document)


class ProtectedDocumentMediaView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, filename):
        storage_name = f'application_docs/{filename}'
        try:
            document = Document.objects.select_related(
                'application__applicant'
            ).get(file=storage_name)
        except Document.DoesNotExist:
            return Response({'error': 'Document not found.'}, status=status.HTTP_404_NOT_FOUND)

        if not _can_view_application_document(request.user, document):
            return Response({'error': 'Document not found.'}, status=status.HTTP_404_NOT_FOUND)
        return _document_file_response(document)


class StaffApplicationQueueView(generics.ListAPIView):
    serializer_class = ApplicationSerializer
    permission_classes = [StaffWorkflowPermission]

    def get_queryset(self):
        return Application.objects.filter(
            status__in=[
                'PENDING', 'FACE_VERIFIED', 'REJECTED', 'CRIMINAL_CHECKED', 'STAFF_REVIEWED',
                'FORWARDED_TO_ADMIN', 'AUTHORITY_APPROVED',
                'AUTHORITY_REJECTED', 'STAFF_CONFIRMED', 'PAYMENT_PENDING',
                'PAYMENT_SUBMITTED', 'PAYMENT_VERIFIED', 'PAYMENT_CONFIRMED',
                'COMPLETED',
            ]
        ).order_by('-submitted_at')


class UploadDocumentView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            application = Application.objects.get(pk=pk, applicant=request.user)
        except Application.DoesNotExist:
            return Response({'error': 'Application not found'}, status=status.HTTP_404_NOT_FOUND)

        document_type = request.data.get('document_type')
        file_obj      = request.FILES.get('file')
        if not document_type or not file_obj:
            return Response({'error': 'Document type and file are required.'}, status=400)

        Document.objects.filter(application=application, document_type=document_type).delete()
        doc = Document.objects.create(application=application, document_type=document_type, file=file_obj)

        BlockchainService.add_block(
            'DOCUMENT_UPLOAD', str(application.id), request.user.cnic,
            {'document_type': document_type, 'tracking_id': application.tracking_id},
        )
        return Response(DocumentSerializer(doc).data, status=status.HTTP_201_CREATED)


class AIFaceVerifyView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            application = Application.objects.get(pk=pk, applicant=request.user)
        except Application.DoesNotExist:
            return Response({'error': 'Application not found'}, status=404)

        if application.status not in ['PENDING', 'REJECTED']:
            if application.status in ['CRIMINAL_CHECKED', 'STAFF_REVIEWED', 'FORWARDED_TO_ADMIN', 'AUTHORITY_APPROVED', 'PAYMENT_PENDING', 'PAYMENT_SUBMITTED', 'PAYMENT_VERIFIED', 'PAYMENT_CONFIRMED', 'COMPLETED']:
                return Response({
                    'message': 'Face verification already completed successfully.',
                    'confidence': application.face_confidence,
                    'liveness_score': application.liveness_score,
                    'status': application.status,
                })
            return Response({'error': f'Face verification is not available in status {application.status}.'}, status=400)

        live_image = request.FILES.get('live_image') or request.FILES.get('frame_CENTER')
        if not live_image:
            return Response({'error': 'A live face image is required.'}, status=400)

        from criminals.service import perform_criminal_check
        from face_verification.service import FaceVerificationService
        from face_verification.views import run_live_checks

        # Same head-turn challenge + parallax test as /api/face-verify/verify-with-cnic/
        image_bytes, liveness, error_response = run_live_checks(request)
        if error_response is not None:
            payload = dict(error_response.data)
            payload.pop('success', None)
            return Response(payload, status=error_response.status_code)

        report = FaceVerificationService.verify_with_cnic(
            cnic=request.user.cnic,
            image_bytes=image_bytes,
            citizen=request.user,
            application=application,
            ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )

        application.face_confidence = report.similarity_pct
        application.liveness_score = liveness['liveness_score']
        if not report.is_verified:
            application.status = 'REJECTED'
            application.save(update_fields=['face_confidence', 'liveness_score', 'status', 'updated_at'])
            return Response({
                'message': 'Face verification failed for the registered CNIC.',
                'confidence': report.similarity_pct,
                'liveness_score': liveness['liveness_score'],
                'status': application.status,
            }, status=400)

        criminal_check = perform_criminal_check(application)
        application.status = 'CRIMINAL_CHECKED'
        application.save(update_fields=['face_confidence', 'liveness_score', 'status', 'updated_at'])
        notify_ai_verified(request.user, application.tracking_id, report.similarity_pct)
        notify_criminal_checked(request.user, application.tracking_id, criminal_check.result)

        return Response({
            'message': 'Face verified and criminal record check completed.',
            'confidence': report.similarity_pct,
            'liveness_score': liveness['liveness_score'],
            'criminal_check': criminal_check.result,
            'status': application.status,
        })


# ─── Citizen — Payment ────────────────────────────────────────────────────────

class ProcessPaymentView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            application = Application.objects.get(pk=pk, applicant=request.user)
            challan     = Challan.objects.get(application=application)
        except (Application.DoesNotExist, Challan.DoesNotExist):
            return Response({'error': 'Application/Challan not found'}, status=404)

        if application.status != 'PAYMENT_PENDING' or challan.status != 'PENDING':
            return Response({'error': 'Payment is not currently available for this application.'}, status=400)

        if hasattr(application, 'payment_record'):
            return Response({'error': 'Payment has already been submitted.'}, status=400)

        payment_method = (request.data.get('payment_method') or '').upper()
        valid_methods = {choice[0] for choice in Payment.PAYMENT_METHOD_CHOICES}
        if payment_method not in valid_methods:
            return Response({'error': 'Payment method is required.'}, status=400)

        mobile_number = request.data.get('mobile_number', '')

        # Create Payment record
        payment = Payment.objects.create(
            application=application,
            challan=challan,
            amount=challan.amount,
            payment_method=payment_method,
            mobile_number=mobile_number
        )

        challan.status         = 'PAID'
        challan.paid_at        = timezone.now()
        challan.payment_method = payment_method
        challan.save()

        application.status = 'PAYMENT_SUBMITTED'
        application.save()

        BlockchainService.add_block(
            'PAYMENT_SUBMIT', str(application.id), request.user.cnic,
            {'transaction_id': payment.transaction_id, 'payment_method': payment_method,
             'amount': str(challan.amount), 'tracking_id': application.tracking_id},
        )
        
        # We don't notify payment confirmed yet, wait for staff verification

        return Response({'message': 'Payment submitted for verification.', 'challan_status': 'PAID',
                         'application_status': application.status, 'transaction_id': payment.transaction_id})


# ─── Staff — Workflow Views ───────────────────────────────────────────────────

class StaffForwardView(APIView):
    permission_classes = [StaffWorkflowPermission]

    # ── Stage 2 Guard ────────────────────────────────────────────────────────
    # Staff can only forward AFTER completing the staff review (STAFF_REVIEWED).
    # PENDING, FACE_VERIFIED, UNDER_REVIEW etc. are explicitly rejected.
    REQUIRED_STATUS = 'STAFF_REVIEWED'

    def post(self, request, pk):
        if request.user.role not in ['POLICE_STAFF', 'SUPER_ADMIN']:
            return Response({'error': 'Unauthorized'}, status=403)
        try:
            application = Application.objects.get(pk=pk)
        except Application.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)

        # ── Idempotency: already forwarded ────────────────────────────────────
        if application.status == 'FORWARDED_TO_ADMIN':
            return Response({
                'message': 'Application has already been forwarded to admin.',
                'status': application.status,
            })

        # ── Guard: application must be in STAFF_REVIEWED ─────────────────────
        if application.status != self.REQUIRED_STATUS:
            return Response(
                {
                    'error': (
                        f'Cannot forward application in status "{application.status}". '
                        f'Application must be reviewed by Police Staff first '
                        f'(required status: {self.REQUIRED_STATUS}).'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        remarks = request.data.get('remarks', '')

        application.staff_notes = remarks
        application.staff_reviewed_by = request.user
        application.staff_reviewed_at = timezone.now()
        application.status = 'FORWARDED_TO_ADMIN'
        application.save()

        BlockchainService.add_block(
            'STAFF_FORWARD', str(application.id), request.user.cnic,
            {'tracking_id': application.tracking_id},
        )
        logger.info('Application %s forwarded to admin by %s.', application.tracking_id, request.user.cnic)

        return Response({'message': 'Application forwarded to admin.', 'status': application.status})


class StaffConfirmView(APIView):
    permission_classes = [StaffWorkflowPermission]

    # ── Stage 2 Guard ────────────────────────────────────────────────────────
    # Staff can only confirm AFTER the authority has approved.
    # These are statuses that have already passed this step — return safe response.
    ALREADY_CONFIRMED_STATUSES = [
        'STAFF_CONFIRMED', 'PAYMENT_PENDING', 'PAYMENT_SUBMITTED',
        'PAYMENT_VERIFIED', 'PAYMENT_CONFIRMED', 'COMPLETED',
    ]

    def post(self, request, pk):
        if request.user.role not in ['POLICE_STAFF', 'SUPER_ADMIN']:
            return Response({'error': 'Unauthorized'}, status=403)
        try:
            application = Application.objects.get(pk=pk)
        except Application.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)

        # ── Idempotency: already confirmed ────────────────────────────────────
        if application.status in self.ALREADY_CONFIRMED_STATUSES:
            return Response({
                'message': 'Application has already been confirmed. Challan was previously generated.',
                'status': application.status,
            })

        # ── Guard: must be AUTHORITY_APPROVED ─────────────────────────────────
        if application.status != 'AUTHORITY_APPROVED':
            return Response(
                {
                    'error': (
                        f'Cannot confirm application in status "{application.status}". '
                        f'Application must be approved by Police Authority first '
                        f'(required status: AUTHORITY_APPROVED).'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        application.staff_confirmed_by = request.user
        application.staff_confirmed_at = timezone.now()
        application.status = 'STAFF_CONFIRMED'
        application.save()

        # Generate challan upon staff confirmation
        _generate_challan(application)

        BlockchainService.add_block(
            'STAFF_CONFIRM', str(application.id), request.user.cnic,
            {'tracking_id': application.tracking_id},
        )
        logger.info('Application %s confirmed by staff %s. Challan generated.', application.tracking_id, request.user.cnic)

        return Response({'message': 'Application confirmed and challan generated.', 'status': application.status})


class StaffVerifyPaymentView(APIView):
    permission_classes = [StaffWorkflowPermission]

    # ── Stage 2 Guard ────────────────────────────────────────────────────────
    # Staff can only verify payment when the citizen has actually submitted it.
    # These statuses indicate the payment step has already passed.
    ALREADY_VERIFIED_STATUSES = [
        'PAYMENT_VERIFIED', 'PAYMENT_CONFIRMED', 'COMPLETED',
    ]

    def post(self, request, pk):
        if request.user.role not in ['POLICE_STAFF', 'SUPER_ADMIN']:
            return Response({'error': 'Unauthorized'}, status=403)
        try:
            application = Application.objects.get(pk=pk)
        except Application.DoesNotExist:
            return Response({'error': 'Application not found'}, status=404)

        # ── Idempotency: payment already verified ─────────────────────────────
        if application.status in self.ALREADY_VERIFIED_STATUSES:
            return Response({
                'message': 'Payment has already been verified for this application.',
                'status': application.status,
            })

        # ── Guard: must be PAYMENT_SUBMITTED ──────────────────────────────────
        if application.status != 'PAYMENT_SUBMITTED':
            return Response(
                {
                    'error': (
                        f'Cannot verify payment for application in status "{application.status}". '
                        f'The citizen must submit payment first (required status: PAYMENT_SUBMITTED).'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Safely access the payment record (OneToOneField raises RelatedObjectDoesNotExist)
        try:
            payment = application.payment_record
        except Exception:
            return Response({'error': 'Payment record not found. Citizen has not submitted payment yet.'}, status=404)

        payment.is_verified = True
        payment.verified_by = request.user
        payment.verified_at = timezone.now()
        payment.save()

        application.payment_verified_by = request.user
        application.payment_verified_at = timezone.now()
        application.status = 'PAYMENT_VERIFIED'
        application.save()

        BlockchainService.add_block(
            'PAYMENT_VERIFY', str(application.id), request.user.cnic,
            {'transaction_id': payment.transaction_id, 'tracking_id': application.tracking_id},
        )
        notify_payment_confirmed(application.applicant, application.tracking_id)
        logger.info('Payment verified for application %s by staff %s.', application.tracking_id, request.user.cnic)

        # ── Stage 4: Delegate certificate generation to the ONE authoritative service ──
        # CertificateService.generate_certificate is idempotent and handles
        # status transitions (PAYMENT_VERIFIED → PAYMENT_CONFIRMED → COMPLETED).
        try:
            from applications.certificate_service import CertificateService
            cert = CertificateService.generate_certificate(application)
            notify_certificate_ready(
                application.applicant, application.tracking_id, cert.certificate_number
            )
            return Response({
                'message': 'Payment verified and certificate issued successfully.',
                'status': application.status,
                'certificate_number': cert.certificate_number,
            })
        except Exception as cert_err:
            # Certificate generation failed — stay at PAYMENT_VERIFIED so
            # IssueCertificateView can retry.  Do NOT falsely mark COMPLETED.
            logger.error(
                'Certificate generation failed for %s: %s',
                application.tracking_id, str(cert_err),
            )
            return Response({
                'message': f'Payment verified successfully. Certificate generation pending: {str(cert_err)}',
                'status': application.status,
            })


# ─── Staff — Remark + Recommend (Legacy/Optional) ────────────────────────────

class StaffRemarkView(APIView):
    permission_classes = [StaffWorkflowPermission]

    # ── Stage 2 Guard ────────────────────────────────────────────────────────
    # Staff review (remark) is only valid AFTER face verification is complete.
    # Applications that have progressed beyond STAFF_REVIEWED are idempotent.
    REVIEWABLE_STATUSES = ['CRIMINAL_CHECKED']
    ALREADY_REVIEWED_STATUSES = [
        'STAFF_REVIEWED', 'FORWARDED_TO_ADMIN', 'AUTHORITY_APPROVED',
        'AUTHORITY_REJECTED', 'STAFF_CONFIRMED', 'PAYMENT_PENDING',
        'PAYMENT_SUBMITTED', 'PAYMENT_VERIFIED', 'PAYMENT_CONFIRMED', 'COMPLETED',
    ]

    def post(self, request, pk):
        if request.user.role not in ['POLICE_STAFF', 'SUPER_ADMIN']:
            return Response({'error': 'Unauthorized'}, status=403)
        try:
            application = Application.objects.get(pk=pk)
        except Application.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)

        # ── Idempotency: already reviewed ─────────────────────────────────────
        if application.status in self.ALREADY_REVIEWED_STATUSES:
            return Response({
                'message': f'Application has already been reviewed (current status: {application.status}).',
                'status': application.status,
            })

        from criminals.models import CriminalCheckResult
        criminal_check = CriminalCheckResult.objects.filter(application=application).first()
        if not criminal_check or criminal_check.result != 'CLEAN':
            return Response(
                {'error': 'Staff review requires a completed clean criminal record check.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Guard: face and criminal checks must be complete ──────────────────
        if application.status not in self.REVIEWABLE_STATUSES:
            return Response(
                {
                    'error': (
                        f'Cannot review application in status "{application.status}". '
                        f'Face verification and criminal checking must be completed first '
                        f'(required status: CRIMINAL_CHECKED).'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        remarks        = request.data.get('remarks', '')
        recommendation = request.data.get('recommendation', 'PENDING')  # APPROVE / REJECT / MORE_INFO

        application.staff_notes = remarks
        application.staff_reviewed_by = request.user
        application.staff_reviewed_at = timezone.now()
        application.status = 'STAFF_REVIEWED'
        application.save()

        BlockchainService.add_block(
            'STAFF_REVIEW', str(application.id), request.user.cnic,
            {'recommendation': recommendation, 'tracking_id': application.tracking_id},
        )
        notify_staff_reviewed(application.applicant, application.tracking_id, remarks)
        logger.info('Application %s staff-reviewed by %s.', application.tracking_id, request.user.cnic)

        return Response({'message': 'Remark saved.', 'status': application.status,
                         'recommendation': recommendation})


# ─── Authority — Final Decision ───────────────────────────────────────────────

class AuthorityDecisionView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    # ── Stage 2 Guard ────────────────────────────────────────────────────────
    # Authority can ONLY approve or reject applications that have been forwarded
    # by Police Staff.  Any other status is an invalid transition.
    REQUIRED_STATUS = 'FORWARDED_TO_ADMIN'

    # These statuses mean a decision has already been recorded.
    ALREADY_DECIDED_STATUSES = [
        'AUTHORITY_APPROVED', 'AUTHORITY_REJECTED',
        'STAFF_CONFIRMED', 'PAYMENT_PENDING', 'PAYMENT_SUBMITTED',
        'PAYMENT_VERIFIED', 'PAYMENT_CONFIRMED', 'COMPLETED',
    ]

    def post(self, request, pk):
        if request.user.role not in ['POLICE_AUTHORITY', 'SUPER_ADMIN']:
            return Response({'error': 'Unauthorized'}, status=403)
        try:
            application = Application.objects.get(pk=pk)
        except Application.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)

        decision = request.data.get('decision')   # APPROVE / REJECT
        reason   = request.data.get('reason', '')

        if decision not in ['APPROVE', 'REJECT']:
            return Response({'error': 'decision must be APPROVE or REJECT'}, status=400)

        # ── Idempotency: decision already recorded ────────────────────────────
        if application.status in self.ALREADY_DECIDED_STATUSES:
            return Response({
                'message': f'A decision has already been recorded for this application (current status: {application.status}).',
                'status': application.status,
            })

        # ── Guard: must be FORWARDED_TO_ADMIN ─────────────────────────────────
        if application.status != self.REQUIRED_STATUS:
            return Response(
                {
                    'error': (
                        f'Cannot make a decision on application in status "{application.status}". '
                        f'Application must be forwarded by Police Staff first '
                        f'(required status: {self.REQUIRED_STATUS}).'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        application.admin_notes = reason
        application.admin_decided_by = request.user
        application.admin_decided_at = timezone.now()

        if decision == 'APPROVE':
            application.status = 'AUTHORITY_APPROVED'
            application.save()
            BlockchainService.add_block(
                'AUTHORITY_APPROVE', str(application.id), request.user.cnic,
                {'tracking_id': application.tracking_id},
            )
            notify_authority_decision(application.applicant, application.tracking_id, True)
            logger.info('Application %s APPROVED by authority %s.', application.tracking_id, request.user.cnic)
        else:
            application.status = 'AUTHORITY_REJECTED'
            application.notes  = reason
            application.save()
            BlockchainService.add_block(
                'AUTHORITY_REJECT', str(application.id), request.user.cnic,
                {'tracking_id': application.tracking_id},
            )
            notify_authority_decision(application.applicant, application.tracking_id, False, reason)
            logger.info('Application %s REJECTED by authority %s.', application.tracking_id, request.user.cnic)

        return Response({'message': f'Application {decision}D.', 'status': application.status})


class CertificateIntegrityVerificationView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if request.user.role not in ['POLICE_STAFF', 'POLICE_AUTHORITY', 'SUPER_ADMIN']:
            return Response({'error': 'Unauthorized'}, status=403)

        certificate_number = (
            request.data.get('certificate_number')
            or request.data.get('certificate_id')
            or request.data.get('certificate')
            or request.data.get('qr_code_hash')
        )
        uploaded_file = request.FILES.get('file') or request.FILES.get('pdf_file')
        from applications.certificate_service import CertificateService

        if not certificate_number:
            normalized_reference = None
        else:
            normalized_reference = CertificateService.normalize_certificate_reference(
                certificate_number
            )

        cert = None
        ambiguous_reference = False
        if normalized_reference:
            matches = CertificateManagementQueryset.for_user(request.user).filter(
                Q(certificate_number__iexact=normalized_reference)
                | Q(qr_code_hash__iexact=normalized_reference)
            ).distinct()
            match_count = matches.count()
            if match_count == 1:
                cert = matches.first()
            elif match_count > 1:
                ambiguous_reference = True

        result = CertificateService.evaluate_uploaded_pdf(cert, uploaded_file)
        if ambiguous_reference and result.get('state') != 'INVALID_FILE':
            result = {
                'state': 'VERIFICATION_UNAVAILABLE',
                'status': 'VERIFICATION_UNAVAILABLE',
                'valid': False,
                'integrity_valid': None,
                'pdf_integrity': 'UNVERIFIED',
                'hash_match': 'NOT_DETERMINED',
                'ledger_valid': None,
                'ledger_status': 'NOT_CHECKED',
                'ledger_issues': [],
                'issuance_event': None,
                'diagnostic': 'The supplied reference matches more than one certificate record.',
                'message': 'The supplied reference matches more than one certificate record.',
                'original_hash': None,
                'uploaded_hash': result.get('uploaded_hash'),
            }

        expired = bool(
            cert
            and cert.validity_expiry
            and cert.validity_expiry < timezone.localdate()
        )
        certificate_status = None
        certificate_state = None
        if cert:
            certificate_status = (
                'REVOKED' if cert.status == 'REVOKED'
                else 'EXPIRED' if expired or cert.status == 'EXPIRED'
                else cert.status
            )
            certificate_state = {
                'REVOKED': 'CERTIFICATE_REVOKED',
                'EXPIRED': 'CERTIFICATE_EXPIRED',
            }.get(certificate_status, 'CERTIFICATE_ACTIVE')

        event_payload = {
            'certificate_number': cert.certificate_number if cert else None,
            'certificate_reference': normalized_reference,
            'state': result.get('state', 'VERIFICATION_UNAVAILABLE'),
            'integrity_valid': result.get('integrity_valid'),
            'pdf_integrity': result.get('pdf_integrity', 'NOT_DETERMINED'),
            'hash_match': result.get('hash_match', 'NOT_DETERMINED'),
            'ledger_valid': result.get('ledger_valid'),
            'original_hash': result.get('original_hash'),
            'uploaded_hash': result.get('uploaded_hash'),
            'issuance_event': result.get('issuance_event'),
            'verified_by_role': request.user.role,
        }

        audit_recorded = False
        audit_error = None
        try:
            block = BlockchainService.add_block(
                'RECORD_MODIFY',
                str(cert.application_id) if cert else 'CERTIFICATE_LOOKUP',
                str(request.user.cnic),
                event_payload,
            )
            audit_recorded = block is not None
            if not audit_recorded:
                audit_error = 'The verification result was produced, but the audit event was not recorded.'
                logger.error('Certificate integrity audit append returned no block.')
        except Exception:
            audit_error = 'The verification result was produced, but the audit event could not be recorded.'
            logger.exception('Could not append certificate integrity audit event.')

        response = {
            'certificate_number': cert.certificate_number if cert else None,
            'certificate_reference': normalized_reference,
            'status': result.get('status', 'VERIFICATION_UNAVAILABLE'),
            'state': result.get('state', 'VERIFICATION_UNAVAILABLE'),
            'valid': bool(result.get('integrity_valid') is True and certificate_status == 'VALID'),
            'integrity_valid': result.get('integrity_valid'),
            'pdf_integrity': result.get('pdf_integrity', 'NOT_DETERMINED'),
            'hash_match': result.get('hash_match', 'NOT_DETERMINED'),
            'ledger_valid': result.get('ledger_valid'),
            'ledger_status': result.get('ledger_status', 'NOT_CHECKED'),
            'ledger_issues': result.get('ledger_issues', []),
            'trusted_hash_validated': bool(
                result.get('ledger_valid') is True and result.get('original_hash')
            ),
            'message': result.get('message', 'Verification unavailable.'),
            'diagnostic': result.get('diagnostic'),
            'original_hash': result.get('original_hash'),
            'uploaded_hash': result.get('uploaded_hash'),
            'issuance_event': result.get('issuance_event'),
            'certificate_status': certificate_status,
            'certificate_state': certificate_state,
            'expired': expired,
            'verified_at': timezone.now().isoformat(),
            'audit_recorded': audit_recorded,
            'audit_message': audit_error,
        }
        return Response(response)


# ─── Issue Certificate (after payment verified) ────────────────────────────

class IssueCertificateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        if request.user.role not in ['POLICE_STAFF', 'POLICE_AUTHORITY', 'SUPER_ADMIN']:
            return Response({'error': 'Unauthorized. Only Police Staff/Authority can issue certificates.'}, status=403)
        try:
            application = Application.objects.get(pk=pk)
        except Application.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)

        # ── Stage 4 Guards ─────────────────────────────────────────────────────
        # Only allow certificate issuance/retry if payment was verified or confirmed
        if application.status not in ['PAYMENT_VERIFIED', 'PAYMENT_CONFIRMED', 'COMPLETED']:
            return Response(
                {
                    'error': (
                        f'Cannot issue certificate for application in status "{application.status}". '
                        f'Payment must be verified first (required status: PAYMENT_VERIFIED).'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from applications.certificate_service import CertificateService
            cert = CertificateService.generate_certificate(application)
            
            notify_certificate_ready(
                application.applicant, application.tracking_id, cert.certificate_number
            )
            
            return Response({
                'message': 'Certificate issued successfully.',
                'certificate_number': cert.certificate_number,
                'status': 'COMPLETED'
            })
        except Exception as e:
            # Revert status to PAYMENT_VERIFIED if generation fails
            if application.status == 'PAYMENT_CONFIRMED':
                application.status = 'PAYMENT_VERIFIED'
                application.save()
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

# ─── Analytics ────────────────────────────────────────────────────────────────


class AuthorityAnalyticsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.user.role not in ['POLICE_AUTHORITY', 'POLICE_STAFF', 'SUPER_ADMIN']:
            return Response({'error': 'Unauthorized'}, status=403)

        total     = Application.objects.count()
        completed = Application.objects.filter(status='COMPLETED').count()
        rejected  = Application.objects.filter(status='AUTHORITY_REJECTED').count()
        pending   = Application.objects.filter(status__in=['PENDING','CRIMINAL_CHECKED','STAFF_REVIEWED']).count()
        revenue   = Challan.objects.filter(status='PAID').aggregate(t=Sum('amount'))['t'] or 0
        rate      = round((completed / (completed + rejected) * 100) if (completed + rejected) > 0 else 100.0, 1)

        # Database-independent month grouping (strftime() only exists in SQLite).
        monthly   = Application.objects.annotate(
            month=ExtractMonth('submitted_at')
        ).values('month').annotate(count=Count('id')).order_by('month')
        month_map = {'01':'Jan','02':'Feb','03':'Mar','04':'Apr','05':'May','06':'Jun',
                     '07':'Jul','08':'Aug','09':'Sep','10':'Oct','11':'Nov','12':'Dec'}
        trends    = [{'name': month_map.get(f"{m['month']:02d}", str(m['month'])), 'applications': m['count']}
                     for m in monthly if m['month'] is not None] or [{'name':'Aug','applications': total or 1}]

        districts = Application.objects.values('applicant__district').annotate(
            count=Count('id')).order_by('-count')

        return Response({
            'total_applications': total,
            'completed_applications': completed,
            'rejected_applications': rejected,
            'pending_applications': pending,
            'total_revenue': float(revenue),
            'success_rate': rate,
            'monthly_trends': trends,
            'district_reports': [
                {'district': d['applicant__district'] or 'Unknown', 'count': d['count']}
                for d in districts
            ],
        })


# ─── Staff Management (Authority only) ────────────────────────────────────────

class StaffListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.user.role not in ['POLICE_AUTHORITY', 'SUPER_ADMIN']:
            return Response({'error': 'Unauthorized'}, status=403)
        from users.serializers import UserSerializer
        staff = User.objects.filter(role='POLICE_STAFF')
        return Response(UserSerializer(staff, many=True).data)

    def post(self, request):
        if request.user.role not in ['POLICE_AUTHORITY', 'SUPER_ADMIN']:
            return Response({'error': 'Unauthorized'}, status=403)
        password = request.data.get('password')
        if not isinstance(password, str) or not password.strip():
            return Response({'password': 'A password is required.'}, status=400)
        from users.serializers import UserSerializer
        serializer = UserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        # Explicitly set role to POLICE_STAFF — UserSerializer has role as read_only
        # so setting it in data dict is silently ignored by the serializer
        user.role = 'POLICE_STAFF'
        user.set_password(password)
        user.save()

        # Generate a one-time OTP so the new staff officer can log in immediately
        from users.otp_service import get_otp_service
        otp_service = get_otp_service()
        otp_res = otp_service.generate_and_send(user, purpose="staff_account_activation")

        response_data = UserSerializer(user).data
        response_data['otp_code'] = otp_res.get('otp_code')  # Returned for admin to communicate to staff
        response_data['message'] = f'Staff officer created. OTP for first login: {otp_res.get("otp_code", "Check console")}'
        return Response(response_data, status=201)


class StaffDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_staff(self, pk):
        try:
            return User.objects.get(pk=pk, role='POLICE_STAFF')
        except User.DoesNotExist:
            return None

    def put(self, request, pk):
        if request.user.role not in ['POLICE_AUTHORITY', 'SUPER_ADMIN']:
            return Response({'error': 'Unauthorized'}, status=403)
        staff = self._get_staff(pk)
        if not staff:
            return Response({'error': 'Staff not found'}, status=404)
        from users.serializers import UserSerializer
        serializer = UserSerializer(staff, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        if request.user.role not in ['POLICE_AUTHORITY', 'SUPER_ADMIN']:
            return Response({'error': 'Unauthorized'}, status=403)
        staff = self._get_staff(pk)
        if not staff:
            return Response({'error': 'Staff not found'}, status=404)
        staff.delete()
        return Response({'message': 'Staff deleted.'})


class StaffToggleActiveView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        if request.user.role not in ['POLICE_AUTHORITY', 'SUPER_ADMIN']:
            return Response({'error': 'Unauthorized'}, status=403)
        try:
            staff = User.objects.get(pk=pk, role='POLICE_STAFF')
        except User.DoesNotExist:
            return Response({'error': 'Staff not found'}, status=404)
        staff.is_active = not staff.is_active
        staff.save()
        return Response({'is_active': staff.is_active})


class StaffResetPasswordView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        if request.user.role not in ['POLICE_AUTHORITY', 'SUPER_ADMIN']:
            return Response({'error': 'Unauthorized'}, status=403)
        try:
            staff = User.objects.get(pk=pk, role='POLICE_STAFF')
        except User.DoesNotExist:
            return Response({'error': 'Staff not found'}, status=404)
        new_pwd = request.data.get('password')
        if not isinstance(new_pwd, str) or not new_pwd.strip():
            return Response({'password': 'A password is required.'}, status=400)
        staff.set_password(new_pwd)
        staff.save()
        return Response({'message': 'Password reset successfully.'})


# ─── Public Certificate Verify ────────────────────────────────────────────────

class PublicCertificateVerifyView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, certificate_number):
        try:
            cert = Certificate.objects.get(
                Q(certificate_number=certificate_number) | Q(qr_code_hash=certificate_number)
            )
        except Certificate.DoesNotExist:
            return Response({'error': 'Invalid Certificate'}, status=404)

        applicant = cert.application.applicant
        application = cert.application
        masked_cnic = applicant.cnic[:5] + '-*******-' + applicant.cnic[-1:]

        # Province/district resolution — same priority chain as serializer
        province = (
            application.applicant_province
            or getattr(applicant, 'province', None)
            or None
        )
        district = getattr(applicant, 'district', None) or None

        # Dynamic authority name based on actual province
        from applications.certificate_service import CertificateService, get_authority_name
        authority = get_authority_name(province) if province else 'Pakistan Police'

        # A ledger row alone is not proof that the hash chain or certificate
        # issue payload is valid.
        from blockchain.models import BlockchainBlock
        bc_block = BlockchainBlock.objects.filter(
            record_id=str(application.id), action_type='CERTIFICATE_ISSUE'
        ).order_by('-block_index').first()
        blockchain_status = 'NO_ISSUE_RECORD'
        blockchain_verified = False
        if bc_block:
            try:
                chain_status = BlockchainService.verify_chain()
                payload = json.loads(bc_block.payload_json or '{}')
                record_matches = (
                    payload.get('certificate_number') == cert.certificate_number
                    and payload.get('certificate_hash') == cert.certificate_hash
                    and (
                        not cert.blockchain_transaction_hash
                        or cert.blockchain_transaction_hash == bc_block.current_hash
                    )
                )
                blockchain_verified = bool(chain_status.get('valid') and record_matches)
                blockchain_status = 'VERIFIED' if blockchain_verified else 'INVALID'
            except (TypeError, ValueError):
                blockchain_status = 'INVALID'
            except Exception:
                logger.exception(
                    'Blockchain verification failed for certificate %s.',
                    cert.certificate_number,
                )
                blockchain_status = 'UNAVAILABLE'

        integrity = CertificateService.verify_certificate_integrity(cert)
        is_expired = cert.validity_expiry < timezone.localdate()
        is_valid = (
            cert.status == 'VALID'
            and not is_expired
            and integrity['integrity_valid']
            and blockchain_verified
        )

        return Response({
            'valid':               is_valid,
            'certificate_number':  cert.certificate_number,
            'applicant_name':      applicant.full_name,
            'cnic':                masked_cnic,
            'province':            province,
            'district':            district,
            'authority':           authority,
            'issue_date':          cert.issue_date,
            'expiry_date':         cert.validity_expiry,
            'status':              cert.status,
            'certificate_status':  integrity['certificate_status'],
            'integrity_valid':     integrity['integrity_valid'],
            'integrity_reason':    integrity['reason'],
            'original_hash':       integrity['original_hash'],
            'current_hash':        integrity['current_hash'],
            'expired':             is_expired,
            'blockchain_hash':     bc_block.current_hash if bc_block else None,
            'blockchain_verified': blockchain_verified,
            'blockchain_status':   blockchain_status,
        })


# ─── Download Certificate PDF ─────────────────────────────────────────────────

class DownloadCertificatePDFView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        try:
            application = Application.objects.get(pk=pk)
            cert        = Certificate.objects.get(application=application)
        except (Application.DoesNotExist, Certificate.DoesNotExist):
            return Response({'error': 'Certificate not issued yet.'}, status=404)

        if request.user != application.applicant and request.user.role not in ['POLICE_STAFF','POLICE_AUTHORITY','SUPER_ADMIN']:
            return Response({'error': 'Unauthorized'}, status=403)

        if not cert.pdf_file or not cert.pdf_file.name:
            return Response(
                {
                    'error': 'The issued certificate PDF is unavailable.',
                    'status': 'PDF_UNAVAILABLE',
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            with cert.pdf_file.storage.open(cert.pdf_file.name, 'rb') as issued_pdf:
                pdf_bytes = issued_pdf.read()
        except FileNotFoundError:
            logger.exception(
                'Stored PDF for certificate %s is missing.',
                cert.certificate_number,
            )
            return Response(
                {
                    'error': 'The stored certificate PDF is missing and requires safe recovery.',
                    'status': 'PDF_UNAVAILABLE',
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception:
            logger.exception(
                'Could not read stored PDF for certificate %s.',
                cert.certificate_number,
            )
            return Response(
                {
                    'error': 'The stored certificate PDF could not be read.',
                    'status': 'PDF_UNAVAILABLE',
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="PakVerify_Cert_{cert.certificate_number}.pdf"'
        return response
