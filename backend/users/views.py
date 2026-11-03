from rest_framework import status, permissions, generics
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db.models import Q
from rest_framework_simplejwt.tokens import RefreshToken
import random
import datetime

from .serializers import (
    UserSerializer, UserRegistrationSerializer, LoginSerializer, OtpVerificationSerializer,
    PasswordResetRequestSerializer, PasswordResetConfirmSerializer
)


User = get_user_model()

def get_tokens_for_user(user):
    refresh = RefreshToken.for_user(user)
    # Add custom claims
    refresh['role'] = user.role
    refresh['full_name'] = user.full_name
    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
        'role': user.role,
        'full_name': user.full_name,
        'cnic': user.cnic
    }

from .otp_service import get_otp_service

class RegisterView(generics.CreateAPIView):
    serializer_class = UserRegistrationSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        
        otp_service = get_otp_service()
        otp_res = otp_service.generate_and_send(user, purpose="registration")
        
        return Response({
            'message': 'Citizen registered successfully. ' + otp_res['message'],
            'cnic': user.cnic,
            'otp_code': otp_res.get('otp_code')
        }, status=status.HTTP_201_CREATED)

class LoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        cnic_or_email = serializer.validated_data['cnic']
        password = serializer.validated_data['password']
        
        # Support login by email or CNIC
        try:
            user = User.objects.get(Q(cnic=cnic_or_email) | Q(email=cnic_or_email))
        except User.DoesNotExist:
            print(f"DEBUG_LOGIN: User not found for '{cnic_or_email}'")
            return Response({'error': 'Invalid credentials'}, status=status.HTTP_401_UNAUTHORIZED)
            
        # Check lockout
        if user.check_lockout():
            print(f"DEBUG_LOGIN: User '{cnic_or_email}' is locked out")
            return Response({
                'error': 'Account locked due to too many failed attempts. Try again later.'
            }, status=status.HTTP_403_FORBIDDEN)
            
        if not user.check_password(password):
            print(f"DEBUG_LOGIN: Password mismatch for user '{cnic_or_email}'")
            user.increment_failed_attempts()
            return Response({'error': 'Invalid credentials'}, status=status.HTTP_401_UNAUTHORIZED)
            
        print(f"DEBUG_LOGIN: Login successful for user '{cnic_or_email}'")
        # Success: reset failed attempts, generate OTP
        user.failed_login_attempts = 0
        user.save()
        
        otp_service = get_otp_service()
        otp_res = otp_service.generate_and_send(user, purpose="login")
        
        return Response({
            'message': 'OTP sent successfully. ' + otp_res['message'],
            'cnic': user.cnic,
            'otp_code': otp_res.get('otp_code')
        }, status=status.HTTP_200_OK)

class VerifyOtpView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = OtpVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        cnic = serializer.validated_data['cnic']
        otp_code = serializer.validated_data['otp_code']
        
        # Support lookup by CNIC or email so staff can verify OTP with either
        try:
            user = User.objects.get(Q(cnic=cnic) | Q(email=cnic))
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
            
        # Validate OTP using service abstraction
        otp_service = get_otp_service()
        is_valid, msg = otp_service.verify(user, otp_code)
        if not is_valid:
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
        
        tokens = get_tokens_for_user(user)
        return Response(tokens, status=status.HTTP_200_OK)

class FaceLoginChallengeView(APIView):
    """
    POST /api/auth/login/face/challenge/  {cnic}
    Issues the head-turn liveness challenge for face login, bound to the
    account with this CNIC. The login request must return it with one frame
    per step, so a photo held to the camera cannot log in.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        cnic = (request.data.get('cnic') or '').strip()
        if not cnic:
            return Response({'error': 'CNIC is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            user = User.objects.get(cnic=cnic)
        except User.DoesNotExist:
            return Response({'error': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)
        if user.check_lockout():
            return Response({'error': 'Account locked due to too many failed attempts.'}, status=status.HTTP_403_FORBIDDEN)
        from face_verification.active_liveness import issue_challenge
        return Response(issue_challenge(user))


class FaceLoginView(APIView):
    permission_classes = [permissions.AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        cnic = (request.data.get('cnic') or '').strip()
        live_image_file = request.FILES.get('live_image') or request.FILES.get('frame_CENTER')

        if not cnic or not live_image_file:
            return Response({'error': 'CNIC and live_image are required for face login.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(cnic=cnic)
        except User.DoesNotExist:
            return Response({'error': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)

        if user.check_lockout():
            return Response({'error': 'Account locked due to too many failed attempts.'}, status=status.HTTP_403_FORBIDDEN)

        from face_verification.service import FaceVerificationService
        from face_verification.views import run_live_checks

        # Head-turn liveness first: a photo or screen must never log anyone in.
        image_bytes, liveness, error_response = run_live_checks(request, user=user)
        if error_response is not None:
            if error_response.status_code == status.HTTP_400_BAD_REQUEST \
                    and error_response.data.get('liveness_reason') != 'INVALID_CHALLENGE':
                user.increment_failed_attempts()
            payload = dict(error_response.data)
            payload.pop('success', None)
            return Response(payload, status=error_response.status_code)

        report = FaceVerificationService.verify_with_cnic(
            cnic=cnic,
            image_bytes=image_bytes,
            citizen=user,
            application=None,
            ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )

        if report.is_verified:
            user.failed_login_attempts = 0
            user.save()
            tokens = get_tokens_for_user(user)
            return Response(tokens, status=status.HTTP_200_OK)
        else:
            user.increment_failed_attempts()
            return Response({'error': 'Face verification failed.'}, status=status.HTTP_401_UNAUTHORIZED)

class PasswordResetRequestView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        cnic = serializer.validated_data['cnic']
        email = serializer.validated_data['email']
        
        try:
            user = User.objects.get(Q(cnic=cnic) & Q(email=email))
        except User.DoesNotExist:
            return Response({'error': 'No matching registered Citizen found with given CNIC and Email.'}, status=status.HTTP_404_NOT_FOUND)
            
        otp_service = get_otp_service()
        otp_res = otp_service.generate_and_send(user, purpose="password_reset")
        
        return Response({
            'message': 'Password reset OTP sent. ' + otp_res['message'],
            'cnic': user.cnic,
            'otp_code': otp_res.get('otp_code')
        }, status=status.HTTP_200_OK)

class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        cnic = serializer.validated_data['cnic']
        otp_code = serializer.validated_data['otp_code']
        new_password = serializer.validated_data['new_password']
        
        try:
            user = User.objects.get(cnic=cnic)
        except User.DoesNotExist:
            return Response({'error': 'Citizen record not found.'}, status=status.HTTP_404_NOT_FOUND)
            
        otp_service = get_otp_service()
        is_valid, msg = otp_service.verify(user, otp_code)
        if not is_valid:
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
                
        user.set_password(new_password)
        user.failed_login_attempts = 0
        user.is_locked = False
        user.lock_time = None
        user.save()
        
        return Response({
            'message': 'Password reset successful! You can now log in with your new password.'
        }, status=status.HTTP_200_OK)

# Alias for backwards compatibility
PasswordResetView = PasswordResetRequestView


# Super Admin Views to manage Citizens & Staff
class AdminUserListView(generics.ListCreateAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def create(self, request, *args, **kwargs):
        if request.user.role != 'SUPER_ADMIN':
            return Response({'error': 'Only Super Admins can manage users.'}, status=status.HTTP_403_FORBIDDEN)
        return super().create(request, *args, **kwargs)

    def get_queryset(self):
        # Only admins can view all users
        if self.request.user.role != 'SUPER_ADMIN':
            return User.objects.none()
            
        queryset = User.objects.all()
        role = self.request.query_params.get('role')
        if role:
            queryset = queryset.filter(role=role)
        return queryset

    def perform_create(self, serializer):
        # Allow admin to create staff/authority accounts
        password = self.request.data.get('password', 'Pass123')
        user = serializer.save()
        user.set_password(password)
        user.save()

class AdminUserDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def check_object_permissions(self, request, obj):
        super().check_object_permissions(request, obj)
        if request.user.role != 'SUPER_ADMIN':
            self.permission_denied(request, message="Only Super Admins can manage users.")
