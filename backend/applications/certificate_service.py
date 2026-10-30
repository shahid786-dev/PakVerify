import os
import io
import uuid
import json
import hashlib
import logging
import re
from datetime import timedelta
from urllib.parse import unquote, urlparse
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image
import qrcode
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from applications.models import Application, Certificate
from blockchain.models import BlockchainBlock
from blockchain.service import BlockchainService

logger = logging.getLogger(__name__)


# Template coordinates use pixels from the top-left of the 816x1306 artwork.
CERTIFICATE_LAYOUT = {
    'template_dpi': 100,
    'font_name': 'Helvetica',
    'font_bold_name': 'Helvetica-Bold',
    'font_size': 15,
    'minimum_font_size': 8.5,
    'fields': {
        'name': {'x': 330, 'baseline_y': 393, 'max_width': 440, 'align': 'center'},
        'father_name': {'x': 136, 'baseline_y': 443, 'max_width': 633, 'align': 'center'},
        'cnic': {'x': 219, 'baseline_y': 493, 'max_width': 309, 'align': 'center'},
        'district': {'x': 233, 'baseline_y': 542, 'max_width': 198, 'align': 'center'},
        'province': {'x': 592, 'baseline_y': 542, 'max_width': 177, 'align': 'center'},
        'certificate_no': {
            'x': 215, 'baseline_y': 810, 'max_width': 194, 'align': 'center',
        },
        'issue_date': {
            'x': 215, 'baseline_y': 860, 'max_width': 194, 'align': 'center',
        },
        'valid_until': {
            'x': 215, 'baseline_y': 910, 'max_width': 194, 'align': 'center',
        },
        'status': {
            'x': 261, 'baseline_y': 958, 'max_width': 149,
            'align': 'center', 'bold': True,
        },
    },
    'qr': {
        'frame': {'x': 33, 'y': 1028, 'size': 183},
        'x': 43,
        'y': 1038,
        'size': 163,
        'quiet_zone_modules': 4,
        'label': {'center_x': 124.5, 'baseline_y': 1250},
    },
}


def draw_fitted_text(pdf, value, field, page_height, scale, bold=False):
    """Draw one value at a fixed baseline, shrinking it to stay in its field."""
    text = str(value or '')
    if not text:
        return

    font_name = CERTIFICATE_LAYOUT['font_bold_name'] if bold else CERTIFICATE_LAYOUT['font_name']
    font_size = CERTIFICATE_LAYOUT['font_size']
    max_width = field['max_width'] * scale
    while (
        font_size > CERTIFICATE_LAYOUT['minimum_font_size']
        and pdf.stringWidth(text, font_name, font_size) > max_width
    ):
        font_size = round(font_size - 0.25, 2)

    if pdf.stringWidth(text, font_name, font_size) > max_width:
        while text and pdf.stringWidth(f'{text}...', font_name, font_size) > max_width:
            text = text[:-1]
        text = f'{text}...' if text else ''
    if not text:
        return

    x = field['x'] * scale
    if field.get('align') == 'center':
        x += (max_width - pdf.stringWidth(text, font_name, font_size)) / 2
    y = page_height - field['baseline_y'] * scale
    pdf.setFont(font_name, font_size)
    pdf.setFillColorRGB(0.0, 0.0, 0.0)
    if bold:
        pdf.setFillColorRGB(0.13, 0.55, 0.13)
    pdf.drawString(x, y, text)


def _generate_verification_qr_png(verification_url):
    qr_layout = CERTIFICATE_LAYOUT['qr']
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=qr_layout['quiet_zone_modules'],
    )
    qr.add_data(verification_url)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color='black', back_color='white').get_image().convert('RGB')
    qr_buffer = io.BytesIO()
    qr_image.save(qr_buffer, format='PNG')
    return qr_buffer.getvalue()


def render_certificate_pdf(template_path, values, verification_url):
    """Compose dynamic values and a QR over the original fixed-size template."""
    with Image.open(template_path) as template:
        template_width, template_height = template.size

    scale = 72.0 / CERTIFICATE_LAYOUT['template_dpi']
    page_width = template_width * scale
    page_height = template_height * scale
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=(page_width, page_height))
    pdf.drawImage(
        template_path, 0, 0, width=page_width, height=page_height,
        preserveAspectRatio=False,
    )

    for key, field in CERTIFICATE_LAYOUT['fields'].items():
        draw_fitted_text(
            pdf, values.get(key), field, page_height, scale,
            bold=field.get('bold', False),
        )

    qr_layout = CERTIFICATE_LAYOUT['qr']
    qr_buffer = io.BytesIO(_generate_verification_qr_png(verification_url))
    qr_size = qr_layout['size'] * scale
    qr_x = qr_layout['x'] * scale
    qr_y = page_height - (qr_layout['y'] + qr_layout['size']) * scale
    pdf.drawImage(
        ImageReader(qr_buffer), qr_x, qr_y, width=qr_size, height=qr_size,
        preserveAspectRatio=True, mask='auto',
    )
    pdf.showPage()
    pdf.save()
    return output.getvalue()

# ── Province → Police Authority mapping ──────────────────────────────────────
PROVINCE_AUTHORITY_MAP = {
    'Punjab':               'Punjab Police',
    'Sindh':                'Sindh Police',
    'Khyber Pakhtunkhwa':   'KP Police',
    'KPK':                  'KP Police',
    'KP':                   'KP Police',
    'Balochistan':          'Balochistan Police',
    'Azad Kashmir':         'AJK Police',
    'AJK':                  'AJK Police',
    'Gilgit-Baltistan':     'GB Police',
    'GB':                   'GB Police',
    'Islamabad':            'ICT Police',
    'Federal':              'Federal Police',
}


def get_authority_name(province: str) -> str:
    """Return the police authority name for a given province string."""
    if not province:
        return 'Pakistan Police'
    # Try exact match first, then case-insensitive
    return PROVINCE_AUTHORITY_MAP.get(
        province,
        PROVINCE_AUTHORITY_MAP.get(province.title(), f'{province} Police')
    )


class CertificateService:
    """
    ONE authoritative certificate-generation service.
    All certificate creation MUST go through this class.
    """

    @staticmethod
    def compute_file_sha256(file_field):
        """Return the SHA-256 hash of a file-like object or FileField, or None if unreadable."""
        if not file_field:
            return None

        try:
            if hasattr(file_field, 'open'):
                try:
                    file_field.open('rb')
                    content = file_field.read()
                    file_field.seek(0)
                    return hashlib.sha256(content).hexdigest()
                finally:
                    try:
                        file_field.close()
                    except Exception:
                        pass

            if hasattr(file_field, 'path') and os.path.exists(file_field.path):
                with open(file_field.path, 'rb') as fh:
                    return hashlib.sha256(fh.read()).hexdigest()

            if isinstance(file_field, (bytes, bytearray)):
                return hashlib.sha256(file_field).hexdigest()

            if isinstance(file_field, str):
                return hashlib.sha256(file_field.encode('utf-8')).hexdigest()
        except Exception:
            logger.exception('Could not read certificate PDF bytes for SHA-256 calculation.')
            return None

        return None

    @staticmethod
    def get_trusted_certificate_hash(cert):
        """Return a usable issuance hash after validating its certificate ledger record."""
        if not cert:
            return None

        application = getattr(cert, 'application', None)
        if not application:
            return None

        block = BlockchainBlock.objects.filter(
            record_id=str(application.id),
            action_type='CERTIFICATE_ISSUE',
        ).order_by('-block_index').first()
        if not block:
            return None

        try:
            payload = json.loads(block.payload_json or '{}')
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get('certificate_number') != cert.certificate_number:
            return None

        ledger_hash = (payload.get('certificate_hash') or '').strip()
        record_hash = (getattr(cert, 'certificate_hash', '') or '').strip()
        if (
            not ledger_hash
            or not re.fullmatch(r'[0-9a-fA-F]{64}', ledger_hash)
            or (record_hash and record_hash.lower() != ledger_hash.lower())
            or (
                cert.blockchain_transaction_hash
                and cert.blockchain_transaction_hash != block.current_hash
            )
        ):
            return None
        return record_hash or ledger_hash

    @staticmethod
    def get_certificate_issuance_event(cert):
        if not cert or not getattr(cert, 'application_id', None):
            return None

        block = BlockchainBlock.objects.filter(
            record_id=str(cert.application_id),
            action_type='CERTIFICATE_ISSUE',
        ).order_by('-block_index').first()
        if block is None:
            return None

        try:
            payload = json.loads(block.payload_json or '{}')
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        return {
            'block_index': block.block_index,
            'timestamp': block.timestamp.isoformat(),
            'action_type': block.action_type,
            'record_id': block.record_id,
            'current_hash': block.current_hash,
            'certificate_number': payload.get('certificate_number'),
            'certificate_hash': payload.get('certificate_hash'),
        }

    @staticmethod
    def normalize_certificate_reference(reference):
        """Accept a certificate number, QR UUID, or verification URL encoded in its QR."""
        if not isinstance(reference, str):
            return None

        reference = reference.strip()
        if not reference or len(reference) > 512:
            return None

        if '://' in reference or reference.startswith('//'):
            reference = urlparse(
                reference if not reference.startswith('//') else f'https:{reference}'
            ).path

        path_parts = [unquote(part) for part in reference.strip('/').split('/') if part]
        verify_indexes = [
            index for index, part in enumerate(path_parts)
            if part.lower() == 'verify'
        ]
        if verify_indexes:
            path_parts = path_parts[verify_indexes[-1] + 1:]
            if path_parts and path_parts[0].lower() == 'certificate':
                path_parts = path_parts[1:]
            if len(path_parts) != 1:
                return None
            reference = path_parts[0]
        elif len(path_parts) == 1:
            reference = path_parts[0]
        else:
            return None

        if re.fullmatch(r'CERT-\d{4}-\d{6}', reference, flags=re.IGNORECASE):
            return reference.upper()
        if re.fullmatch(r'[A-Za-z0-9._-]{1,100}', reference):
            return reference
        return None

    @classmethod
    def evaluate_uploaded_pdf(cls, cert, uploaded_file):
        """Validate uploaded PDF bytes and compare them with the trusted issuance hash."""
        max_file_size = 10 * 1024 * 1024
        if not uploaded_file:
            return {
                'state': 'INVALID_FILE',
                'status': 'INVALID_FILE',
                'valid': False,
                'integrity_valid': None,
                'message': 'A valid PDF file is required.',
                'original_hash': None,
                'uploaded_hash': None,
            }

        try:
            declared_size = getattr(uploaded_file, 'size', None)
            if declared_size is not None and declared_size > max_file_size:
                return {
                    'state': 'INVALID_FILE',
                    'status': 'INVALID_FILE',
                    'valid': False,
                    'integrity_valid': None,
                    'message': 'The uploaded PDF exceeds the 10MB size limit.',
                    'original_hash': None,
                    'uploaded_hash': None,
                }
            uploaded_file.seek(0)
            raw = uploaded_file.read(max_file_size + 1)
            uploaded_file.seek(0)
        except (OSError, ValueError, AttributeError):
            return {
                'state': 'INVALID_FILE',
                'status': 'INVALID_FILE',
                'valid': False,
                'integrity_valid': None,
                'message': 'The uploaded PDF could not be read.',
                'original_hash': None,
                'uploaded_hash': None,
            }

        if len(raw) > max_file_size:
            return {
                'state': 'INVALID_FILE',
                'status': 'INVALID_FILE',
                'valid': False,
                'integrity_valid': None,
                'message': 'The uploaded PDF exceeds the 10MB size limit.',
                'original_hash': None,
                'uploaded_hash': None,
            }

        if not raw.startswith(b'%PDF-'):
            return {
                'state': 'INVALID_FILE',
                'status': 'INVALID_FILE',
                'valid': False,
                'integrity_valid': None,
                'message': 'The uploaded document is not a valid PDF file.',
                'original_hash': None,
                'uploaded_hash': None,
            }

        uploaded_hash = hashlib.sha256(raw).hexdigest()
        if cert is None:
            return {
                'state': 'NO_CERTIFICATE_RECORD',
                'status': 'NO_CERTIFICATE_RECORD',
                'valid': False,
                'integrity_valid': None,
                'pdf_integrity': 'NOT_DETERMINED',
                'hash_match': 'NOT_DETERMINED',
                'ledger_valid': None,
                'ledger_status': 'NOT_CHECKED',
                'ledger_issues': [],
                'issuance_event': None,
                'diagnostic': 'No certificate record matches the supplied reference.',
                'message': 'No certificate record matches the supplied reference.',
                'original_hash': None,
                'uploaded_hash': uploaded_hash,
            }

        original_hash = cls.get_trusted_certificate_hash(cert)
        hash_match = (
            'NOT_DETERMINED' if not original_hash
            else 'MATCH' if uploaded_hash == original_hash
            else 'MISMATCH'
        )
        issuance_event = cls.get_certificate_issuance_event(cert)
        hash_diagnostic = None
        if not original_hash:
            hash_diagnostic = (
                'No certificate issuance event exists for this certificate.'
                if issuance_event is None
                else 'The issuance event does not contain a consistent trusted certificate hash.'
            )

        try:
            chain_status = BlockchainService.verify_chain()
            if not isinstance(chain_status, dict):
                chain_status = {'valid': False, 'issues': ['Ledger validator returned an invalid response.']}
            ledger_valid = chain_status.get('valid') is True
            ledger_issues = chain_status.get('issues') or []
            if not ledger_valid:
                logger.error(
                    'Certificate verification refused because the blockchain ledger is invalid: %s',
                    ledger_issues,
                )
                return {
                    'state': 'VERIFICATION_UNAVAILABLE',
                    'status': 'VERIFICATION_UNAVAILABLE',
                    'valid': False,
                    'integrity_valid': None,
                    'pdf_integrity': 'UNVERIFIED',
                    'hash_match': hash_match,
                    'ledger_valid': False,
                    'ledger_status': 'INVALID',
                    'ledger_issues': ledger_issues,
                    'issuance_event': issuance_event,
                    'diagnostic': ' '.join(filter(None, (
                        hash_diagnostic,
                        'The issuance-record hash was compared, but ledger integrity is invalid, '
                        'so PDF integrity cannot be confirmed.',
                    ))),
                    'message': 'Ledger integrity could not be validated; PDF integrity cannot be confirmed.',
                    'original_hash': original_hash,
                    'uploaded_hash': uploaded_hash,
                }
        except Exception:
            logger.exception('Could not validate the ledger during certificate verification.')
            return {
                'state': 'VERIFICATION_UNAVAILABLE',
                'status': 'VERIFICATION_UNAVAILABLE',
                'valid': False,
                'integrity_valid': None,
                'pdf_integrity': 'UNVERIFIED',
                'hash_match': hash_match,
                'ledger_valid': None,
                'ledger_status': 'UNAVAILABLE',
                'ledger_issues': [],
                'issuance_event': issuance_event,
                'diagnostic': ' '.join(filter(None, (
                    hash_diagnostic,
                    'The ledger validator raised an error; PDF integrity cannot be confirmed.',
                ))),
                'message': 'The ledger is unavailable; PDF integrity cannot be confirmed.',
                'original_hash': original_hash,
                'uploaded_hash': uploaded_hash,
            }

        if not original_hash:
            return {
                'state': 'VERIFICATION_UNAVAILABLE',
                'status': 'VERIFICATION_UNAVAILABLE',
                'valid': False,
                'integrity_valid': None,
                'pdf_integrity': 'NOT_DETERMINED',
                'hash_match': 'NOT_DETERMINED',
                'ledger_valid': True,
                'ledger_status': 'VALID',
                'ledger_issues': [],
                'issuance_event': issuance_event,
                'diagnostic': hash_diagnostic,
                'message': hash_diagnostic,
                'original_hash': None,
                'uploaded_hash': uploaded_hash,
            }

        if uploaded_hash == original_hash:
            return {
                'state': 'INTEGRITY_VERIFIED',
                'status': 'INTEGRITY_VERIFIED',
                'valid': True,
                'integrity_valid': True,
                'pdf_integrity': 'UNMODIFIED',
                'hash_match': 'MATCH',
                'ledger_valid': True,
                'ledger_status': 'VALID',
                'ledger_issues': [],
                'issuance_event': issuance_event,
                'diagnostic': None,
                'message': 'Certificate integrity verified.',
                'original_hash': original_hash,
                'uploaded_hash': uploaded_hash,
            }

        return {
            'state': 'HASH_MISMATCH',
            'status': 'HASH_MISMATCH',
            'valid': False,
            'integrity_valid': False,
            'pdf_integrity': 'TAMPERED',
            'hash_match': 'MISMATCH',
            'ledger_valid': True,
            'ledger_status': 'VALID',
            'ledger_issues': [],
            'issuance_event': issuance_event,
            'diagnostic': None,
            'message': 'Document hash mismatch detected. Further investigation is required.',
            'original_hash': original_hash,
            'uploaded_hash': uploaded_hash,
        }

    @classmethod
    def verify_certificate_integrity(cls, cert):
        """Verify file integrity using the original stored certificate hash."""
        original_hash = cls.get_trusted_certificate_hash(cert)
        if not original_hash:
            return {
                'integrity_valid': False,
                'certificate_status': 'INVALID',
                'reason': 'Original certificate hash is missing.',
                'original_hash': None,
                'current_hash': None,
            }

        pdf_file = getattr(cert, 'pdf_file', None)
        if not pdf_file or not getattr(pdf_file, 'name', None):
            return {
                'integrity_valid': False,
                'certificate_status': 'INVALID',
                'reason': 'Certificate PDF file is missing.',
                'original_hash': original_hash,
                'current_hash': None,
            }

        current_hash = cls.compute_file_sha256(pdf_file)
        if current_hash is None:
            return {
                'integrity_valid': False,
                'certificate_status': 'INVALID',
                'reason': 'Certificate PDF file could not be read.',
                'original_hash': original_hash,
                'current_hash': None,
            }

        return {
            'integrity_valid': current_hash == original_hash,
            'certificate_status': 'VALID' if current_hash == original_hash else 'TAMPERED',
            'reason': 'Certificate hash matches the original record.' if current_hash == original_hash else 'Certificate PDF hash does not match the original issued hash.',
            'original_hash': original_hash,
            'current_hash': current_hash,
        }

    @staticmethod
    def generate_certificate(application):
        """
        Generate a police verification certificate for the given application.

        Idempotent for complete records. Existing incomplete PDF/hash artifacts
        are preserved and require safe recovery instead of being overwritten.

        Valid pre-generation statuses: PAYMENT_VERIFIED, PAYMENT_CONFIRMED.
        """
        stored_pdf_name = None
        try:
            with transaction.atomic():
                locked_application = Application.objects.select_for_update().get(pk=application.pk)
                existing_cert = Certificate.objects.filter(application=locked_application).first()
                eligible_statuses = ('PAYMENT_VERIFIED', 'PAYMENT_CONFIRMED', 'COMPLETED')
                if locked_application.status not in eligible_statuses:
                    raise ValueError(
                        f"Certificate cannot be generated for application in status "
                        f"'{locked_application.status}'. Required: one of {eligible_statuses}."
                    )
                if existing_cert and existing_cert.status == 'REVOKED':
                    raise ValueError('A revoked certificate cannot be regenerated.')

                try:
                    chain_status = BlockchainService.verify_chain()
                except Exception as exc:
                    logger.exception('Could not validate the ledger before certificate issuance.')
                    raise RuntimeError(
                        'Certificate issuance is blocked because ledger integrity is unavailable.'
                    ) from exc
                if not isinstance(chain_status, dict) or chain_status.get('valid') is not True:
                    issues = (
                        chain_status.get('issues', [])
                        if isinstance(chain_status, dict)
                        else ['Ledger validator returned an invalid response.']
                    )
                    logger.error(
                        'Certificate issuance blocked by invalid ledger: %s',
                        issues,
                    )
                    raise RuntimeError(
                        'Certificate issuance is blocked because the existing ledger is invalid. '
                        'Preserve the ledger and complete safe recovery before issuing new certificates.'
                    )

                if (
                    existing_cert
                    and existing_cert.pdf_file
                    and existing_cert.certificate_hash
                    and CertificateService.compute_file_sha256(existing_cert.pdf_file)
                    == existing_cert.certificate_hash
                ):
                    if not CertificateService.get_trusted_certificate_hash(existing_cert):
                        raise RuntimeError(
                            'The existing certificate has no consistent issuance event. '
                            'Historical issuance must be safely recovered; a new event will not be fabricated.'
                        )
                    if locked_application.status != 'COMPLETED':
                        locked_application.status = 'COMPLETED'
                        locked_application.save(update_fields=['status', 'updated_at'])
                    application.status = 'COMPLETED'
                    return existing_cert
                if existing_cert and (existing_cert.pdf_file or existing_cert.certificate_hash):
                    raise RuntimeError(
                        'The existing certificate has incomplete or inconsistent issuance data. '
                        'Its historical PDF and hash are preserved for safe recovery.'
                    )

                if locked_application.status != 'PAYMENT_CONFIRMED':
                    locked_application.status = 'PAYMENT_CONFIRMED'
                    locked_application.save(update_fields=['status', 'updated_at'])

                citizen = locked_application.applicant
                province = (
                    locked_application.applicant_province
                    or getattr(citizen, 'province', None)
                    or ''
                )
                district = getattr(citizen, 'district', None) or ''
                sig_uuid = str(uuid.uuid4())

                cert = existing_cert or Certificate(
                    application=locked_application,
                    validity_expiry=timezone.now().date() + timedelta(
                        days=getattr(settings, 'CERTIFICATE_VALIDITY_DAYS', 180)
                    ),
                )
                cert.qr_code_hash = cert.qr_code_hash or sig_uuid
                cert.digital_signature = cert.digital_signature or f"PKV-SIG-{sig_uuid[:18].upper()}"
                cert.status = (
                    'EXPIRED'
                    if cert.validity_expiry < timezone.localdate()
                    else 'VALID'
                )
                cert.blockchain_transaction_hash = None
                cert.save()

                base_url = getattr(
                    settings, 'CERTIFICATE_VERIFY_BASE_URL',
                    getattr(settings, 'FRONTEND_URL', 'http://localhost:5173')
                ).rstrip('/')
                verification_url = f"{base_url}/verify/{cert.certificate_number}"
                cert.verification_url = verification_url

                template_path = os.path.join(
                    settings.BASE_DIR.parent, 'certificates', 'templates', 'Certificate_Template.jfif'
                )
                if not os.path.exists(template_path):
                    # Copy bundled inside backend/ (used when only backend/ is deployed, e.g. Vercel).
                    template_path = os.path.join(
                        settings.BASE_DIR, 'certificate_assets', 'Certificate_Template.jfif'
                    )
                if not os.path.exists(template_path):
                    template_path = os.path.join(
                        settings.BASE_DIR.parent, 'src', 'assets', 'Police verification Certificate.png'
                    )
                if not os.path.exists(template_path):
                    raise FileNotFoundError(
                        f"Certificate template not found. Tried:\n"
                        f"  {os.path.join(settings.BASE_DIR.parent, 'certificates', 'templates', 'Certificate_Template.jfif')}\n"
                        f"  {os.path.join(settings.BASE_DIR.parent, 'src', 'assets', 'Police verification Certificate.png')}"
                    )

                pdf_bytes = render_certificate_pdf(
                    template_path,
                    {
                        'name': citizen.full_name,
                        'father_name': citizen.father_name,
                        'cnic': citizen.cnic,
                        'district': district,
                        'province': province,
                        'certificate_no': cert.certificate_number,
                        'issue_date': timezone.localdate(),
                        'valid_until': cert.validity_expiry,
                        'status': 'VERIFIED',
                    },
                    verification_url,
                )
                cert.pdf_file.save(
                    f"PakVerify_Cert_{cert.certificate_number}.pdf",
                    ContentFile(pdf_bytes),
                    save=False,
                )
                stored_pdf_name = cert.pdf_file.name
                cert_hash = CertificateService.compute_file_sha256(cert.pdf_file)
                if cert_hash is None:
                    raise OSError('The stored certificate PDF could not be read for SHA-256 calculation.')
                if cert_hash != hashlib.sha256(pdf_bytes).hexdigest():
                    raise OSError('Stored certificate PDF does not match the generated PDF bytes.')
                cert.certificate_hash = cert_hash
                cert.save()

                bc_block = BlockchainService.add_block(
                    'CERTIFICATE_ISSUE', str(locked_application.id), citizen.cnic,
                    {
                        'certificate_number': cert.certificate_number,
                        'tracking_id': locked_application.tracking_id,
                        'certificate_hash': cert_hash,
                    }
                )
                if bc_block is None:
                    raise RuntimeError('Certificate issuance audit record could not be created.')
                cert.blockchain_transaction_hash = bc_block.current_hash
                cert.save(update_fields=['blockchain_transaction_hash', 'updated_at'])

                # Optional public anchor on Ethereum (no-op unless ETH_* settings are configured).
                from blockchain import ethereum as eth_anchor
                if eth_anchor.is_enabled():
                    _cert_number, _cert_hash, _block = cert.certificate_number, cert_hash, bc_block
                    transaction.on_commit(
                        lambda: eth_anchor.anchor_certificate(_cert_number, _cert_hash, _block)
                    )

                locked_application.status = 'COMPLETED'
                locked_application.save(update_fields=['status', 'updated_at'])

        except Exception:
            if stored_pdf_name:
                try:
                    Certificate.pdf_file.field.storage.delete(stored_pdf_name)
                except Exception:
                    logger.exception('Could not remove uncommitted certificate PDF %s.', stored_pdf_name)
            raise

        application.status = 'COMPLETED'
        return cert
