from rest_framework import generics, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from .models import BlockchainBlock
from .serializers import BlockchainBlockSerializer, ChainVerifySerializer
from .service import BlockchainService


class BlockPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class BlockchainAuditPermission(permissions.BasePermission):
    allowed_roles = {'POLICE_STAFF', 'POLICE_AUTHORITY', 'SUPER_ADMIN'}

    def has_permission(self, request, view):
        return (
            bool(request.user and request.user.is_authenticated)
            and request.user.role in self.allowed_roles
        )


class BlockchainBlockListView(generics.ListAPIView):
    """
    GET /api/blockchain/blocks/
    List all blockchain blocks (paginated, newest first).
    Public read access so the explorer page can work without login.
    """
    serializer_class    = BlockchainBlockSerializer
    pagination_class    = BlockPagination
    permission_classes  = [BlockchainAuditPermission]
    queryset            = BlockchainBlock.objects.all().order_by('-block_index')

    def get_queryset(self):
        qs = super().get_queryset()
        action = self.request.query_params.get('action_type')
        if action:
            qs = qs.filter(action_type=action)
        return qs


class BlockchainBlockDetailView(generics.RetrieveAPIView):
    """GET /api/blockchain/blocks/<id>/"""
    serializer_class   = BlockchainBlockSerializer
    permission_classes = [BlockchainAuditPermission]
    queryset           = BlockchainBlock.objects.all()


class BlockchainVerifyView(APIView):
    """
    GET /api/blockchain/verify/
    Verify the entire chain integrity.
    """
    permission_classes = [BlockchainAuditPermission]

    def get(self, request):
        result = BlockchainService.verify_chain()
        return Response(result)


class BlockchainRecordHistoryView(APIView):
    """
    GET /api/blockchain/record/<record_id>/
    Return all blocks associated with a specific application/record.
    """
    permission_classes = [BlockchainAuditPermission]

    def get(self, request, record_id):
        blocks = BlockchainService.get_record_history(record_id)
        serializer = BlockchainBlockSerializer(blocks, many=True)
        return Response({'count': blocks.count(), 'blocks': serializer.data})


# ── Optional Ethereum anchoring (public, read-only) ─────────────────────────

class EthereumConfigView(APIView):
    """Public: tells the frontend whether on-chain verification is available."""
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        from . import ethereum
        return Response(ethereum.public_config())


class EthereumCertificateAnchorView(APIView):
    """
    Public: on-chain anchor details for a certificate number.
    Exposes only hashes and the transaction reference — no personal data.
    """
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request, certificate_number):
        from . import ethereum
        from .models import EthereumAnchor
        from applications.models import Certificate

        cert = Certificate.objects.filter(certificate_number=certificate_number).first()
        if cert is None:
            return Response({'error': 'Certificate not found.'}, status=404)

        config = ethereum.public_config()
        anchor = (
            EthereumAnchor.objects.filter(certificate_number=certificate_number, status='SUBMITTED').first()
            or EthereumAnchor.objects.filter(certificate_number=certificate_number).first()
        )
        payload = {
            **config,
            'certificate_number': certificate_number,
            'certificate_hash': cert.certificate_hash,
            'anchor': None,
            'onchain': None,
        }
        if anchor:
            payload['anchor'] = {
                'status': anchor.status,
                'tx_hash': anchor.tx_hash or None,
                'tx_url': f"{config['explorer_url'].rstrip('/')}/tx/{anchor.tx_hash}" if anchor.tx_hash else None,
                'ledger_block_index': anchor.ledger_block_index,
                'ledger_block_hash': anchor.ledger_block_hash,
                'chain_id': anchor.chain_id,
                'contract_address': anchor.contract_address,
                'created_at': anchor.created_at.isoformat(),
            }
        if request.query_params.get('check') == '1' and cert.certificate_hash:
            payload['onchain'] = ethereum.read_onchain(cert.certificate_hash)
        return Response(payload)
