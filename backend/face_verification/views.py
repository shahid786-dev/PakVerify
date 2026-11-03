"""
face_verification/views.py
===========================
DRF views for the AI Face Verification API.

Endpoints
---------
POST   /api/face-verify/verify/          — Run live face verification (1:N search)
POST   /api/face-verify/verify-cnic/     — Validate CNIC against NADRA database
POST   /api/face-verify/verify-with-cnic/ — 1:1 face verification for submitted CNIC
GET    /api/face-verify/history/         — Citizen's verification history
GET    /api/face-verify/report/<uuid>/   — Retrieve a specific report
GET    /api/face-verify/status/          — Embedding store health status
"""

import logging

from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

import re

from .models import FaceVerificationReport
from .serializers import (
    FaceVerificationReportSerializer,
    FaceVerificationReportSummarySerializer,
    VerificationRequestSerializer,
    EmbeddingStoreStatusSerializer,
)
from .service import FaceVerificationService, check_liveness_with_ai
from .exceptions import LivenessServiceError
from . import active_liveness

logger = logging.getLogger('face_verification')


def _get_client_ip(request) -> str:
    """Extract real client IP, respecting proxy headers."""
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '127.0.0.1')


def run_live_checks(request, user=None):
    """
    Active (head-turn) liveness, then the AI-service passive check on the CENTER
    frame. Returns (center_image_bytes, liveness_dict, error_response_or_None).
    Shared by both face verification endpoints so no path skips the head-turn test.
    """
    try:
        active = active_liveness.run_active_liveness(request, user or request.user)
    except active_liveness.ActiveLivenessError as exc:
        return None, None, Response(
            {'success': False, 'error': str(exc), 'liveness_reason': 'INVALID_CHALLENGE'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:
        logger.error('Active liveness unavailable: %s', exc)
        return None, None, Response(
            {'success': False, 'error': 'Liveness service is unavailable. Please try again shortly.'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    if not active['verified']:
        return None, None, Response({
            'success': False,
            'error': f"Liveness verification failed. {active['message']}",
            'liveness_reason': active['reason'],
            'liveness_score': active['liveness_score'],
            'anti_spoofing': active['anti_spoofing'],
            'liveness_metrics': active['metrics'],
        }, status=status.HTTP_400_BAD_REQUEST)

    image_bytes = active['center_bytes']
    try:
        passive = check_liveness_with_ai(image_bytes)
    except LivenessServiceError as exc:
        return None, None, Response({'success': False, 'error': str(exc)}, status=503)

    if not passive['verified']:
        return None, None, Response({
            'success': False,
            'error': 'Liveness verification failed. Please capture a live image.',
            'liveness_reason': 'PASSIVE_CHECK_FAILED',
            'liveness_score': passive['liveness_score'],
            'anti_spoofing': passive['anti_spoofing'],
        }, status=status.HTTP_400_BAD_REQUEST)

    liveness = {
        'verified': True,
        'liveness_score': round(min(active['liveness_score'], passive['liveness_score']), 3),
        'anti_spoofing': 'REAL',
        'method': 'head-turn challenge + planar parallax',
        'metrics': active['metrics'],
    }
    return image_bytes, liveness, None


class LivenessChallengeView(APIView):
    """
    GET /api/face-verify/liveness-challenge/
    =========================================
    Returns a signed, user-bound challenge: CENTER, then LEFT/RIGHT in random
    order. The client captures one frame per step and posts them as
    frame_CENTER, frame_LEFT, frame_RIGHT with challenge_token.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(active_liveness.issue_challenge(request.user))


class LiveFaceVerifyView(APIView):
    """
    POST /api/face-verify/verify/
    ==============================
    Accepts a live webcam image (multipart upload), runs the full
    face verification pipeline, and returns a structured report.

    Authentication: Bearer JWT required.
    Permission: Citizen must be authenticated.

    Request (multipart/form-data)
    ─────────────────────────────
    live_image      : File   — JPEG/PNG webcam capture (required)
    application_id  : int    — Link to police application (optional)

    Response 200
    ────────────
    {
        "success": true,
        "report": { ... FaceVerificationReport fields ... },
        "message": "Verification successful — Identity confirmed."
    }

    Response 400 / 200 (with error)
    ───────────────────────────────
    Failed verifications still return HTTP 200 with success=false.
    Only malformed requests return HTTP 400.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        return Response(
            {
                'success': False,
                'error': (
                    'Unscoped face identification is disabled. '
                    'Use verify-with-cnic/ so the live face is compared only '
                    'with the authenticated citizen\'s registered CNIC.'
                ),
            },
            status=status.HTTP_410_GONE,
        )

        """Legacy implementation retained below for reference during migration."""
        # ── Validate request ─────────────────────────────────────────────
        serializer = VerificationRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Read image bytes ─────────────────────────────────────────────
        live_image_file = serializer.validated_data['live_image']
        application_id  = serializer.validated_data.get('application_id')
        image_bytes     = live_image_file.read()

        # ── Resolve optional application ─────────────────────────────────
        application = None
        if application_id:
            try:
                from applications.models import Application
                application = Application.objects.get(
                    id=application_id,
                    applicant=request.user,
                )
            except Exception:
                pass  # Non-fatal — we just don't link the application

        # ── Run verification ─────────────────────────────────────────────
        report = FaceVerificationService.verify(
            image_bytes=image_bytes,
            citizen=request.user,
            application=application,
            ip_address=_get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )

        # ── Serialize and respond ────────────────────────────────────────
        report_data = FaceVerificationReportSerializer(report).data

        if report.is_verified:
            message = (
                f"Verification successful - Identity confirmed. "
                f"Matched: {report.matched_citizen_name} "
                f"(Similarity: {report.similarity_pct:.2f}%)"
            )
        elif report.status == 'NO_FACE':
            message = "No face detected. Please ensure your face is clearly visible."
        elif report.status == 'MULTIPLE_FACES':
            message = "Multiple faces detected. Please verify alone."
        elif report.status == 'LOW_QUALITY':
            message = "Image quality too low. Improve lighting and try again."
        elif report.status == 'STORE_NOT_READY':
            message = "Verification system not ready. Contact administrator."
        else:
            message = (
                "Verification failed - your face did not match any NADRA record "
                f"with >={int(report.threshold_used * 100)}% confidence."
            )

        return Response({
            'success': report.is_verified,
            'report': report_data,
            'message': message,
        })


# ─────────────────────────────────────────────────────────────────────────────
# NEW: CNIC-based 1:1 Verification Endpoints
# ─────────────────────────────────────────────────────────────────────────────

CNIC_PATTERN = re.compile(r'^\d{5}-\d{7}-\d$')


class VerifyCNICView(APIView):
    """
    POST /api/face-verify/verify-cnic/
    ===================================
    Step 1 of the CNIC-based 1:1 face verification workflow.

    Receives a CNIC, validates its format, searches the NADRA dummy database,
    and returns the non-sensitive identity record if found.

    The backend also checks whether an embedding exists for this CNIC in the
    in-memory embedding store, which is required for the next step.

    Request
    -------
    { "cnic": "42101-1234567-1" }

    Response 200 (found)
    --------------------
    {
        "found": true,
        "cnic": "42101-1234567-1",
        "name": "Ahmed Ali",
        "date_of_birth": "15-05-2002",
        "has_face_embedding": true
    }

    Response 200 (not found)
    ------------------------
    { "found": false, "error": "CNIC NOT FOUND ..." }
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser]

    def post(self, request):
        cnic = (request.data.get('cnic') or '').strip()

        # ── Validate CNIC format ─────────────────────────────────────────
        if not cnic:
            return Response(
                {'found': False, 'error': 'CNIC is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not CNIC_PATTERN.match(cnic):
            return Response(
                {
                    'found': False,
                    'error': (
                        'Invalid CNIC format. '
                        'Please enter in the format: XXXXX-XXXXXXX-X '
                        '(e.g. 42101-1234567-1)'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Step 1: CNIC must match the authenticated citizen's own CNIC ──
        citizen_cnic = getattr(request.user, 'cnic', '').strip()
        if citizen_cnic and cnic != citizen_cnic:
            logger.warning(
                'CNIC mismatch: user=%s submitted CNIC=%s', citizen_cnic, cnic
            )
            return Response({
                'found': False,
                'error': (
                    'CNIC MISMATCH\n\n'
                    'The entered CNIC does not match your registered account CNIC. '
                    'You can only verify your own identity.'
                ),
            }, status=status.HTTP_403_FORBIDDEN)

        # ── Step 2: Search the identity card dataset (NADRARecord OR CSV) ────
        try:
            from nadra.models import NADRARecord
            record = NADRARecord.objects.filter(cnic=cnic, is_active=True).first()
        except Exception as exc:
            logger.error('NADRA DB lookup error for CNIC=%s: %s', cnic, exc)
            return Response(
                {'found': False, 'error': 'Database error. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # ── Step 3: Confirm a face image/embedding exists for this CNIC ──
        try:
            store = FaceVerificationService.get_store_instance()
            cnic_record = store.get_record(cnic)
            has_embedding = cnic_record is not None
        except Exception:
            has_embedding = False
            cnic_record = None

        # ── If no NADRARecord but embedding exists in store (CSV-based CNIC) ──
        # The embedding store is keyed by real CNICs from the CSV dataset.
        # Allow verification to proceed using CSV-sourced identity data.
        if not record and has_embedding:
            logger.info(
                'CNIC=%s not in NADRARecord DB but found in CSV/embedding store — proceeding',
                cnic
            )
            return Response({
                'found': True,
                'cnic': cnic,
                'name': cnic_record.get('full_name', f'Citizen ({cnic})'),
                'date_of_birth': 'N/A',
                'has_face_embedding': True,
            })

        if not record:
            logger.info('CNIC not found in identity card dataset: %s', cnic)
            return Response({
                'found': False,
                'error': (
                    'CNIC NOT IN DATASET\n\n'
                    'Your CNIC was not found in the identity card dataset. '
                    'Face verification requires a stored identity card image. '
                    'Please contact the administrator to register your identity card.'
                ),
            })

        if not has_embedding:
            logger.warning('No face embedding in dataset for CNIC=%s', cnic)
            return Response({
                'found': False,
                'error': (
                    'NO FACE IMAGE FOUND\n\n'
                    'No face image is linked to your CNIC in the identity card dataset. '
                    'Please contact the administrator to upload your identity card photo.'
                ),
            })

        # ── Return non-sensitive identity info ────────────────────────────
        dob_str = (
            record.date_of_birth.strftime('%d-%m-%Y')
            if record.date_of_birth else 'N/A'
        )

        logger.info(
            'CNIC ownership verified: citizen=%s, dataset name=%s',
            citizen_cnic, record.full_name
        )
        return Response({
            'found': True,
            'cnic': cnic,
            'name': record.full_name,
            'date_of_birth': dob_str,
            'has_face_embedding': True,
        })


class CnicBasedFaceVerifyView(APIView):
    """
    POST /api/face-verify/verify-with-cnic/
    =========================================
    Step 2 of the CNIC-based 1:1 face verification workflow.

    Receives:
      - cnic        : the previously verified CNIC
      - live_image  : live webcam capture
      - application_id (optional)

    Backend logic:
      1. Validate CNIC again (trust no frontend claim about which embedding to use)
      2. Look up NADRA record by CNIC
      3. Retrieve the specific embedding for this CNIC from the store
      4. Extract live face embedding
      5. Compare live embedding ONLY against this specific CNIC's embedding (1:1)
      6. Return match/mismatch with similarity score
      7. Save FaceVerificationReport

    This enforces 1:1 verification, NOT 1:N identification.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        cnic = (request.data.get('cnic') or '').strip()
        live_image_file = request.FILES.get('live_image')
        application_id = request.data.get('application_id')

        # ── Basic validation ─────────────────────────────────────────────
        if not cnic:
            return Response(
                {'success': False, 'error': 'CNIC is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not CNIC_PATTERN.match(cnic):
            return Response(
                {'success': False, 'error': 'Invalid CNIC format.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not live_image_file and not request.FILES.get('frame_CENTER'):
            return Response(
                {'success': False, 'error': 'live_image is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Enforce CNIC ownership: submitted CNIC must be citizen's own ──
        citizen_cnic = getattr(request.user, 'cnic', '').strip()
        if citizen_cnic and cnic != citizen_cnic:
            logger.warning(
                'Face verify CNIC mismatch: user=%s submitted CNIC=%s', citizen_cnic, cnic
            )
            return Response({
                'success': False,
                'error': 'CNIC does not match your registered account. You can only verify your own identity.',
            }, status=status.HTTP_403_FORBIDDEN)

        # ── Resolve optional application ─────────────────────────────────
        application = None
        if application_id:
            try:
                from applications.models import Application
                application = Application.objects.get(
                    id=application_id,
                    applicant=request.user,
                )
            except Application.DoesNotExist:
                return Response(
                    {
                        'success': False,
                        'error': 'Application not found or access denied.',
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

        if application and application.status != 'PENDING':
            if application.status in ['FACE_VERIFIED', 'CRIMINAL_CHECK', 'CRIMINAL_CHECKED', 'STAFF_REVIEWED', 'FORWARDED_TO_ADMIN', 'AUTHORITY_APPROVED', 'PAYMENT_PENDING', 'PAYMENT_SUBMITTED', 'PAYMENT_VERIFIED', 'PAYMENT_CONFIRMED', 'APPROVED', 'COMPLETED']:
                report = FaceVerificationReport.objects.filter(application=application, status='VERIFIED').first()
                if report:
                    report_data = FaceVerificationReportSerializer(report).data
                else:
                    report_data = {
                        'status': 'VERIFIED',
                        'is_verified': True,
                        'similarity_pct': application.face_confidence or 100.0,
                    }
                return Response({
                    'success': True,
                    'report': report_data,
                    'message': 'Face verification already completed successfully.',
                    'verification_mode': '1:1_CNIC_BASED',
                    'verified_cnic': cnic,
                })

        image_bytes, liveness, error_response = run_live_checks(request)
        if error_response is not None:
            return error_response

        # ── Run 1:1 CNIC-based verification ──────────────────────────────
        report = FaceVerificationService.verify_with_cnic(
            cnic=cnic,
            image_bytes=image_bytes,
            citizen=request.user,
            application=application,
            ip_address=_get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )

        # ── Build response message ────────────────────────────────────────
        report_data = FaceVerificationReportSerializer(report).data

        # Complete the same-CNIC criminal check before releasing the application
        # to Police Staff. A non-clean result remains blocked at staff review.
        if report.is_verified and application:
            from criminals.service import perform_criminal_check
            from notifications.service import notify_criminal_checked

            application.face_confidence = report.similarity_pct
            application.liveness_score = liveness['liveness_score']
            criminal_check = perform_criminal_check(application)
            application.status = 'CRIMINAL_CHECKED'
            application.save(update_fields=['face_confidence', 'liveness_score', 'status', 'updated_at'])
            notify_criminal_checked(
                request.user, application.tracking_id, criminal_check.result
            )
            logger.info(
                'Application %s completed face and criminal checks: %s.',
                application.tracking_id, criminal_check.result,
            )

        if report.is_verified:
            message = (
                f'✓ Face Verification Successful — '
                f'Your identity has been confirmed for CNIC {cnic} '
                f'(Similarity: {report.similarity_pct:.2f}%). '
                f'Your application has been submitted for Police Staff review.'
            )
        elif report.status == 'FAILED':
            message = (
                f'✗ Identity Verification Failed — '
                f'The captured face does not match the registered face '
                f'associated with CNIC {cnic}. '
                f'Verification Status: IDENTITY_MISMATCH'
            )
        elif report.status == 'NO_FACE':
            message = 'No face detected. Please ensure your face is clearly visible.'
        elif report.status == 'MULTIPLE_FACES':
            message = 'Multiple faces detected. Please verify alone.'
        elif report.status == 'LOW_QUALITY':
            message = 'Image quality too low. Improve lighting and try again.'
        elif report.status == 'STORE_NOT_READY':
            message = 'Verification system not ready. Contact administrator.'
        elif report.status == 'EMBEDDING_ERROR':
            message = (
                f'No face embedding found for CNIC {cnic}. '
                'Please contact the administrator to register this CNIC.'
            )
        else:
            message = 'Verification could not be completed. Please try again.'

        return Response({
            'success': report.is_verified,
            'report': report_data,
            'message': message,
            'verification_mode': '1:1_CNIC_BASED',
            'verified_cnic': cnic,
            'liveness_score': liveness['liveness_score'],
            'liveness_method': liveness['method'],
        })


class VerificationHistoryView(APIView):
    """
    GET /api/face-verify/history/?limit=10
    ========================================
    Returns the authenticated citizen's N most recent verification reports.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        limit = min(int(request.query_params.get('limit', 10)), 50)
        reports = FaceVerificationService.get_citizen_history(request.user, limit=limit)
        serializer = FaceVerificationReportSummarySerializer(reports, many=True)
        return Response({
            'count': len(serializer.data),
            'results': serializer.data,
        })


class VerificationReportDetailView(APIView):
    """
    GET /api/face-verify/report/<report_id>/
    ==========================================
    Returns the full details of a specific verification report.
    Citizens can only access their own reports.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, report_id):
        try:
            report = FaceVerificationService.get_report_by_id(report_id, request.user)
        except FaceVerificationReport.DoesNotExist:
            return Response(
                {'error': 'Report not found or access denied.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = FaceVerificationReportSerializer(report)
        return Response(serializer.data)


class EmbeddingStoreStatusView(APIView):
    """
    GET /api/face-verify/status/
    ==============================
    Returns the health and readiness of the NADRA face embedding store.
    Useful for admin dashboards and system health checks.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_status = FaceVerificationService.get_store_status()
        serializer = EmbeddingStoreStatusSerializer(data=store_status)
        serializer.is_valid()  # always valid — we control the data
        return Response(store_status)
