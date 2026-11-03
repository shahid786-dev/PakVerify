"""
face_verification/urls.py
==========================
URL routing for the face verification module.
All routes are authenticated (enforced in views via IsAuthenticated).
"""

from django.urls import path
from .views import (
    LiveFaceVerifyView,
    VerifyCNICView,
    CnicBasedFaceVerifyView,
    LivenessChallengeView,
    VerificationHistoryView,
    VerificationReportDetailView,
    EmbeddingStoreStatusView,
)

app_name = 'face_verification'

urlpatterns = [
    # POST — run live verification (1:N — original endpoint)
    path(
        'verify/',
        LiveFaceVerifyView.as_view(),
        name='live-verify',
    ),
    # POST — Step 1: validate CNIC against NADRA database
    path(
        'verify-cnic/',
        VerifyCNICView.as_view(),
        name='verify-cnic',
    ),
    # GET — signed head-turn challenge for active liveness (call before Step 2)
    path(
        'liveness-challenge/',
        LivenessChallengeView.as_view(),
        name='liveness-challenge',
    ),
    # POST — Step 2: 1:1 face verification for specific CNIC
    path(
        'verify-with-cnic/',
        CnicBasedFaceVerifyView.as_view(),
        name='verify-with-cnic',
    ),
    # GET — citizen's verification history
    path(
        'history/',
        VerificationHistoryView.as_view(),
        name='history',
    ),
    # GET — full report details
    path(
        'report/<uuid:report_id>/',
        VerificationReportDetailView.as_view(),
        name='report-detail',
    ),
    # GET — embedding store health
    path(
        'status/',
        EmbeddingStoreStatusView.as_view(),
        name='store-status',
    ),
]
