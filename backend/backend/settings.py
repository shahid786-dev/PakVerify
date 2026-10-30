import os
from pathlib import Path
from datetime import timedelta

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'dev-only-change-this-secret-before-deployment-0123456789abcdef',
)

DEBUG = os.environ.get('DJANGO_DEBUG', 'false').lower() == 'true'
LOCAL_DEVELOPMENT = os.environ.get('DJANGO_ENV', 'local').lower() != 'production'
OTP_DEV_BYPASS_ENABLED = (
    os.environ.get('OTP_DEV_BYPASS', str(LOCAL_DEVELOPMENT)).lower() == 'true'
)

ALLOWED_HOSTS = [
    host for host in os.environ.get(
        'DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1'
    ).split(',') if host
]

# ── Vercel deployment support ────────────────────────────────────────────────
# Vercel sets VERCEL=1 inside its build and runtime. Nothing below changes
# local or Docker behaviour.
ON_VERCEL = bool(os.environ.get('VERCEL'))
if ON_VERCEL and '.vercel.app' not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append('.vercel.app')

CSRF_TRUSTED_ORIGINS = [
    origin for origin in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',') if origin
]
# Vercel terminates TLS before the request reaches Django.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third party apps
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    'drf_spectacular',
    
    # Internal apps
    'users.apps.UsersConfig',
    'applications.apps.ApplicationsConfig',
    'blockchain.apps.BlockchainConfig',
    'nadra.apps.NadraConfig',
    'notifications.apps.NotificationsConfig',
    'criminals.apps.CriminalsConfig',
    'chatbot.apps.ChatbotConfig',
    'audit.apps.AuditConfig',
    'face_verification.apps.FaceVerificationConfig',  # AI Face Verification Module
    'incidents.apps.IncidentsConfig',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware', # CORS must be near the top
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # serves admin/DRF static files in production
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Custom Audit Logging Middleware
    'audit.middleware.AuditLoggingMiddleware',
]

ROOT_URLCONF = 'backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'backend.wsgi.application'

# Database Configuration: SQLite dev fallback, PostgreSQL prod
DB_TYPE = os.environ.get('DB_TYPE', 'sqlite')

DATABASE_URL = os.environ.get('DATABASE_URL', '')

if DATABASE_URL:
    # Neon / any hosted PostgreSQL: a single connection URL.
    import dj_database_url
    DATABASES = {
        'default': dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=int(os.environ.get('DB_CONN_MAX_AGE', '0')),
            ssl_require=os.environ.get('DB_SSL_REQUIRE', 'true').lower() == 'true',
        )
    }
elif DB_TYPE == 'postgresql' or os.environ.get('POSTGRES_DB'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('POSTGRES_DB', 'pakverify_db'),
            'USER': os.environ.get('POSTGRES_USER', 'postgres'),
            'PASSWORD': os.environ.get('POSTGRES_PASSWORD', 'postgres_secure_pass'),
            'HOST': os.environ.get('POSTGRES_HOST', 'localhost'),
            'PORT': os.environ.get('POSTGRES_PORT', '5432'),
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {
            'min_length': 6,
        }
    },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Karachi'
USE_I18N = True
USE_TZ = True

# Static & Media Files
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Serve static files from the installed apps without a collectstatic step.
WHITENOISE_USE_FINDERS = True

# File storage: local disk by default; Vercel Blob when a token is configured.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
if os.environ.get('BLOB_READ_WRITE_TOKEN'):
    STORAGES['default'] = {'BACKEND': 'backend.storage_backends.VercelBlobStorage'}
elif ON_VERCEL:
    # Read-only filesystem fallback: files only live for the function's lifetime.
    MEDIA_ROOT = Path('/tmp/media')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Custom Auth User Model
AUTH_USER_MODEL = 'users.User'

# REST Framework configurations
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

# SimpleJWT settings
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': False,
    'BLACKLIST_AFTER_ROTATION': False,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'VERIFYING_KEY': None,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

# CORS configuration
CORS_ALLOWED_ORIGINS = [
    origin for origin in os.environ.get(
        'CORS_ALLOWED_ORIGINS',
        'http://localhost:5173,http://127.0.0.1:5173,http://localhost,http://127.0.0.1',
    ).split(',') if origin
]
CORS_ALLOW_CREDENTIALS = True

# AI Service URL configuration
AI_SERVICE_URL = os.environ.get('AI_SERVICE_URL', 'http://localhost:8001')
AI_SERVICE_TIMEOUT_SECONDS = float(os.environ.get('AI_SERVICE_TIMEOUT_SECONDS', '10'))

# API Documentation
SPECTACULAR_SETTINGS = {
    'TITLE': 'PakVerify Portal API',
    'DESCRIPTION': 'API documentation for the AI-Based Police and Citizen Interaction Portal',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}

# ── Face Verification Settings ───────────────────────────────────────────────
# Similarity threshold for face match (cosine similarity, 0.0–1.0)
FACE_VERIFICATION_THRESHOLD = float(os.environ.get('FACE_VERIFICATION_THRESHOLD', '0.70'))

# ── Logging Configuration ─────────────────────────────────────────────────
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'face_verification': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# ── Optional Ethereum anchoring (MetaMask / Sepolia) ─────────────────────
# When all three are set, certificate-issuance ledger blocks are also anchored
# on an EVM chain through the PakVerifyRegistry contract (contracts/).
# Leave them empty to keep the existing database-only ledger behaviour.
ETH_RPC_URL = os.environ.get('ETH_RPC_URL', '')
ETH_CONTRACT_ADDRESS = os.environ.get('ETH_CONTRACT_ADDRESS', '')
ETH_PRIVATE_KEY = os.environ.get('ETH_PRIVATE_KEY', '')
ETH_CHAIN_ID = int(os.environ.get('ETH_CHAIN_ID', '11155111'))  # Sepolia
ETH_EXPLORER_URL = os.environ.get('ETH_EXPLORER_URL', 'https://sepolia.etherscan.io')

# ── Certificate Generation Settings ──────────────────────────────────────
CERTIFICATE_VALIDITY_DAYS = 180

# Base URL used in QR code verification links.
# Set CERTIFICATE_VERIFY_BASE_URL in the OS environment for production deployments.
# Example:  CERTIFICATE_VERIFY_BASE_URL=https://pakverify.example.com
CERTIFICATE_VERIFY_BASE_URL = os.environ.get(
    'CERTIFICATE_VERIFY_BASE_URL',
    os.environ.get('FRONTEND_URL', 'http://localhost:5173'),
)
# Legacy alias — some older code may still read FRONTEND_URL
FRONTEND_URL = CERTIFICATE_VERIFY_BASE_URL
