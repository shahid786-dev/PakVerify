from django.db import models
from django.utils import timezone


class BlockchainBlock(models.Model):
    """
    Immutable blockchain block storing a hash-chained record of every
    critical action performed in the system.  No passwords or raw PII
    are stored – only hashes and descriptive metadata.
    """

    ACTION_TYPES = (
        ('CITIZEN_REGISTER',    'Citizen Registered'),
        ('PROFILE_UPDATE',      'Profile Updated'),
        ('APPLICATION_SUBMIT',  'Application Submitted'),
        ('DOCUMENT_UPLOAD',     'Document Uploaded'),
        ('AI_FACE_VERIFY',      'AI Face Verification'),
        ('NADRA_VERIFY',        'NADRA Verification'),
        ('CRIMINAL_CHECK',      'Criminal Record Check'),
        ('STAFF_REVIEW',        'Staff Review / Remark'),
        ('AUTHORITY_APPROVE',   'Authority Approved'),
        ('AUTHORITY_REJECT',    'Authority Rejected'),
        ('CHALLAN_GENERATE',    'Challan Generated'),
        ('STAFF_FORWARD',       'Staff Forwarded'),
        ('STAFF_CONFIRM',        'Staff Confirmed'),
        ('PAYMENT_SUBMIT',       'Payment Submitted'),
        ('PAYMENT_VERIFY',       'Payment Verified'),
        ('PAYMENT_CONFIRM',      'Payment Confirmed'),
        ('CERTIFICATE_ISSUE',   'Certificate Issued'),
        ('CERTIFICATE_DOWNLOAD','Certificate Downloaded'),
        ('RECORD_MODIFY',       'Sensitive Record Modified'),
        ('GENESIS',             'Genesis Block'),
    )

    block_index    = models.IntegerField(unique=True, db_index=True)
    timestamp      = models.DateTimeField(default=timezone.now)
    previous_hash  = models.CharField(max_length=64)
    current_hash   = models.CharField(max_length=64, unique=True, db_index=True)
    data_hash      = models.CharField(max_length=64)   # SHA-256 of payload_json
    action_type    = models.CharField(max_length=40, choices=ACTION_TYPES)
    record_id      = models.CharField(max_length=100, blank=True)  # e.g. application pk
    performed_by   = models.CharField(max_length=100)              # CNIC or 'SYSTEM'
    nonce          = models.IntegerField(default=0)
    payload_json   = models.TextField()  # JSON – NO passwords or sensitive PII

    class Meta:
        ordering = ['block_index']
        verbose_name = 'Blockchain Block'
        verbose_name_plural = 'Blockchain Blocks'

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError('Blockchain blocks are immutable.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Blockchain blocks are immutable.')

    def __str__(self):
        return f"Block #{self.block_index} [{self.action_type}] — {self.current_hash[:16]}…"


class EthereumAnchor(models.Model):
    """
    Optional on-chain anchor of a certificate-issuance ledger block.

    Created only when ETH_RPC_URL / ETH_CONTRACT_ADDRESS / ETH_PRIVATE_KEY are
    configured. Stores no PII — only hashes and the transaction reference.
    """

    STATUS_CHOICES = (
        ('SUBMITTED', 'Submitted'),
        ('FAILED', 'Failed'),
    )

    certificate_number = models.CharField(max_length=30, db_index=True)
    certificate_hash   = models.CharField(max_length=64, db_index=True)
    ledger_block_index = models.IntegerField()
    ledger_block_hash  = models.CharField(max_length=64)
    chain_id           = models.IntegerField()
    contract_address   = models.CharField(max_length=42)
    tx_hash            = models.CharField(max_length=66, blank=True)
    status             = models.CharField(max_length=12, choices=STATUS_CHOICES)
    error              = models.TextField(blank=True)
    created_at         = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.certificate_number} → {self.tx_hash or self.status}"
