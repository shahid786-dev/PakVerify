from django.urls import path
from .views import (
    RegisterView, LoginView, FaceLoginView, FaceLoginChallengeView, VerifyOtpView, PasswordResetView,
    PasswordResetRequestView, PasswordResetConfirmView,
    AdminUserListView, AdminUserDetailView
)

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('login/', LoginView.as_view(), name='login'),
    path('login/face/challenge/', FaceLoginChallengeView.as_view(), name='login-face-challenge'),
    path('login/face/', FaceLoginView.as_view(), name='login-face'),
    path('verify-otp/', VerifyOtpView.as_view(), name='verify-otp'),
    path('reset-password/', PasswordResetView.as_view(), name='reset-password'),
    path('request-reset/', PasswordResetRequestView.as_view(), name='request-reset'),
    path('confirm-reset/', PasswordResetConfirmView.as_view(), name='confirm-reset'),
    
    # Admin management
    path('admin/users/', AdminUserListView.as_view(), name='admin-users-list'),
    path('admin/users/<int:pk>/', AdminUserDetailView.as_view(), name='admin-user-detail'),
]

