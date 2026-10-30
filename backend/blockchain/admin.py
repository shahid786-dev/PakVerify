from django.contrib import admin
from .models import BlockchainBlock


@admin.register(BlockchainBlock)
class BlockchainBlockAdmin(admin.ModelAdmin):
    list_display  = ('block_index', 'action_type', 'record_id',
                     'performed_by', 'timestamp', 'current_hash_short')
    list_filter   = ('action_type',)
    search_fields = ('record_id', 'performed_by', 'current_hash')
    readonly_fields = (
        'block_index', 'timestamp', 'previous_hash', 'current_hash',
        'data_hash', 'action_type', 'record_id', 'performed_by',
        'nonce', 'payload_json',
    )
    ordering = ('-block_index',)

    def current_hash_short(self, obj):
        return obj.current_hash[:20] + '…'
    current_hash_short.short_description = 'Hash (short)'

    def has_add_permission(self, request):
        return False   # blocks are immutable – never add via admin

    def has_delete_permission(self, request, obj=None):
        return False   # immutable ledger


from .models import EthereumAnchor  # noqa: E402


@admin.register(EthereumAnchor)
class EthereumAnchorAdmin(admin.ModelAdmin):
    list_display = ('certificate_number', 'status', 'tx_hash', 'chain_id', 'created_at')
    list_filter = ('status', 'chain_id')
    search_fields = ('certificate_number', 'certificate_hash', 'tx_hash')
    readonly_fields = [f.name for f in EthereumAnchor._meta.fields]
