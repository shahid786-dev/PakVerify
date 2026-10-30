"""
Optional Ethereum anchoring for issued certificates.

The PakVerify ledger itself stays in PostgreSQL. When ETH_RPC_URL,
ETH_CONTRACT_ADDRESS and ETH_PRIVATE_KEY are configured, each certificate
issuance is additionally written to the PakVerifyRegistry smart contract
(contracts/PakVerifyRegistry.sol), so the certificate can be verified
independently on-chain, e.g. from the browser with MetaMask.

Anchoring never blocks or breaks certificate issuance: every failure is logged
and recorded as a FAILED EthereumAnchor row.
"""

import logging
import re

from django.conf import settings

logger = logging.getLogger(__name__)

REGISTRY_ABI = [
    {
        'type': 'function', 'name': 'owner', 'stateMutability': 'view',
        'inputs': [], 'outputs': [{'name': '', 'type': 'address'}],
    },
    {
        'type': 'function', 'name': 'anchorCertificate', 'stateMutability': 'nonpayable',
        'inputs': [
            {'name': 'certificateHash', 'type': 'bytes32'},
            {'name': 'ledgerBlockHash', 'type': 'bytes32'},
            {'name': 'ledgerBlockIndex', 'type': 'uint32'},
            {'name': 'certificateNumber', 'type': 'string'},
        ],
        'outputs': [],
    },
    {
        'type': 'function', 'name': 'verifyCertificate', 'stateMutability': 'view',
        'inputs': [{'name': 'certificateHash', 'type': 'bytes32'}],
        'outputs': [
            {'name': 'anchored', 'type': 'bool'},
            {'name': 'ledgerBlockHash', 'type': 'bytes32'},
            {'name': 'ledgerBlockIndex', 'type': 'uint32'},
            {'name': 'anchoredAt', 'type': 'uint64'},
        ],
    },
]

_HEX64 = re.compile(r'^[0-9a-fA-F]{64}$')


def is_enabled() -> bool:
    return bool(
        getattr(settings, 'ETH_RPC_URL', '')
        and getattr(settings, 'ETH_CONTRACT_ADDRESS', '')
        and getattr(settings, 'ETH_PRIVATE_KEY', '')
    )


def public_config() -> dict:
    """Non-secret settings the frontend needs to verify with MetaMask."""
    return {
        'enabled': is_enabled(),
        'chain_id': settings.ETH_CHAIN_ID,
        'contract_address': settings.ETH_CONTRACT_ADDRESS or None,
        'explorer_url': settings.ETH_EXPLORER_URL,
    }


def signer_status() -> dict:
    """
    Public diagnostics: the server wallet ADDRESS derived from ETH_PRIVATE_KEY
    (never the key) and whether it is the contract owner allowed to anchor.
    """
    status = {'signer_address': None, 'contract_owner': None, 'signer_is_owner': None}
    if not is_enabled():
        return status
    try:
        w3, contract = _web3()
        signer = w3.eth.account.from_key(settings.ETH_PRIVATE_KEY).address
        owner = contract.functions.owner().call()
        status.update(signer_address=signer, contract_owner=owner,
                      signer_is_owner=signer.lower() == str(owner).lower())
    except Exception as exc:
        status['error'] = type(exc).__name__
    return status


def _web3():
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(settings.ETH_RPC_URL, request_kwargs={'timeout': 8}))
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(settings.ETH_CONTRACT_ADDRESS), abi=REGISTRY_ABI,
    )
    return w3, contract


def anchor_certificate(certificate_number: str, certificate_hash: str, ledger_block):
    """Send an anchorCertificate transaction. Returns the EthereumAnchor row or None."""
    if not is_enabled():
        return None

    from .models import EthereumAnchor

    record = EthereumAnchor(
        certificate_number=certificate_number,
        certificate_hash=(certificate_hash or '').lower(),
        ledger_block_index=ledger_block.block_index,
        ledger_block_hash=ledger_block.current_hash,
        chain_id=settings.ETH_CHAIN_ID,
        contract_address=settings.ETH_CONTRACT_ADDRESS,
    )
    try:
        if not _HEX64.match(certificate_hash or '') or not _HEX64.match(ledger_block.current_hash or ''):
            raise ValueError('Certificate and ledger hashes must be 64-character hex strings.')

        w3, contract = _web3()
        account = w3.eth.account.from_key(settings.ETH_PRIVATE_KEY)
        tx = contract.functions.anchorCertificate(
            bytes.fromhex(certificate_hash),
            bytes.fromhex(ledger_block.current_hash),
            int(ledger_block.block_index),
            certificate_number,
        ).build_transaction({
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address, 'pending'),
            'chainId': settings.ETH_CHAIN_ID,
        })
        signed = account.sign_transaction(tx)
        raw = getattr(signed, 'raw_transaction', None) or getattr(signed, 'rawTransaction')
        tx_hash = w3.eth.send_raw_transaction(raw)
        record.tx_hash = '0x' + bytes(tx_hash).hex().removeprefix('0x')
        record.status = 'SUBMITTED'
        logger.info('Certificate %s anchored on-chain: %s', certificate_number, record.tx_hash)
    except Exception as exc:  # never break certificate issuance
        record.status = 'FAILED'
        record.error = str(exc)[:2000]
        logger.warning('Ethereum anchoring failed for %s: %s', certificate_number, exc)

    try:
        record.save()
    except Exception:
        logger.exception('Could not store EthereumAnchor for %s', certificate_number)
        return None
    return record


def read_onchain(certificate_hash: str):
    """Read verifyCertificate() from the contract. Returns a dict or None."""
    if not (settings.ETH_RPC_URL and settings.ETH_CONTRACT_ADDRESS) or not _HEX64.match(certificate_hash or ''):
        return None
    try:
        _, contract = _web3()
        anchored, block_hash, block_index, anchored_at = contract.functions.verifyCertificate(
            bytes.fromhex(certificate_hash)
        ).call()
        return {
            'anchored': bool(anchored),
            'ledger_block_hash': bytes(block_hash).hex(),
            'ledger_block_index': int(block_index),
            'anchored_at': int(anchored_at),
        }
    except Exception as exc:
        logger.warning('On-chain read failed: %s', exc)
        return None
