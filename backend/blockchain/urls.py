from django.urls import path
from .views import (
    BlockchainBlockListView, BlockchainBlockDetailView,
    BlockchainVerifyView, BlockchainRecordHistoryView,
    EthereumConfigView, EthereumCertificateAnchorView,
)

urlpatterns = [
    path('blocks/',               BlockchainBlockListView.as_view(),   name='blockchain-block-list'),
    path('blocks/<int:pk>/',      BlockchainBlockDetailView.as_view(), name='blockchain-block-detail'),
    path('verify/',               BlockchainVerifyView.as_view(),       name='blockchain-verify'),
    path('record/<str:record_id>/', BlockchainRecordHistoryView.as_view(), name='blockchain-record-history'),
    path('ethereum/config/',      EthereumConfigView.as_view(),         name='ethereum-config'),
    path('ethereum/certificate/<str:certificate_number>/',
         EthereumCertificateAnchorView.as_view(), name='ethereum-certificate-anchor'),
]
