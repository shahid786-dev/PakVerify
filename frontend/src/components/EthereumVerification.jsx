import { useEffect, useState } from 'react';
import { Link2, ShieldCheck, ShieldAlert, Wallet, ExternalLink, Loader2 } from 'lucide-react';
import { blockchainAPI } from '../api/apiClient';

// keccak256("verifyCertificate(bytes32)").slice(0, 4)
const VERIFY_SELECTOR = '0x850c1768';

const CHAIN_NAMES = { 1: 'Ethereum Mainnet', 11155111: 'Sepolia Testnet', 17000: 'Holesky Testnet', 137: 'Polygon', 80002: 'Polygon Amoy' };

function decodeVerifyResult(hex) {
  const data = (hex || '').replace(/^0x/, '');
  if (data.length < 64 * 4) return null;
  const word = (i) => data.slice(i * 64, (i + 1) * 64);
  return {
    anchored: BigInt('0x' + word(0)) === 1n,
    ledgerBlockHash: word(1),
    ledgerBlockIndex: Number(BigInt('0x' + word(2))),
    anchoredAt: Number(BigInt('0x' + word(3))),
  };
}

/**
 * Optional on-chain verification panel. Renders nothing unless the backend has
 * Ethereum anchoring configured or the certificate already has an anchor.
 */
export default function EthereumVerification({ certNumber }) {
  const [info, setInfo] = useState(null);
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState(null);
  const [message, setMessage] = useState('');

  useEffect(() => {
    if (!certNumber) return;
    blockchainAPI.ethereumCertificate(certNumber)
      .then((res) => setInfo(res.data))
      .catch(() => setInfo(null));
  }, [certNumber]);

  if (!info || (!info.enabled && !info.anchor)) return null;

  const anchor = info.anchor;
  const chainId = anchor?.chain_id || info.chain_id;
  const contract = anchor?.contract_address || info.contract_address;
  const certHash = (info.certificate_hash || '').toLowerCase();

  const verifyWithMetaMask = async () => {
    setMessage('');
    setResult(null);
    const eth = window.ethereum;
    if (!eth) {
      setMessage('MetaMask was not found in this browser. Install it from metamask.io and try again.');
      return;
    }
    if (!contract || !/^[0-9a-f]{64}$/.test(certHash)) {
      setMessage('This certificate has no on-chain record to check yet.');
      return;
    }
    setChecking(true);
    try {
      const targetHex = '0x' + Number(chainId).toString(16);
      const current = await eth.request({ method: 'eth_chainId' });
      if (current?.toLowerCase() !== targetHex) {
        await eth.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: targetHex }] });
      }
      const raw = await eth.request({
        method: 'eth_call',
        params: [{ to: contract, data: VERIFY_SELECTOR + certHash }, 'latest'],
      });
      const decoded = decodeVerifyResult(raw);
      if (!decoded) throw new Error('Unexpected response from the contract.');
      const matchesLedger = !anchor?.ledger_block_hash
        || decoded.ledgerBlockHash.toLowerCase() === anchor.ledger_block_hash.toLowerCase();
      setResult({ ...decoded, matchesLedger });
    } catch (err) {
      setMessage(err?.code === 4001 ? 'Request rejected in MetaMask.' : (err?.message || 'On-chain check failed.'));
    } finally {
      setChecking(false);
    }
  };

  const ok = result?.anchored && result?.matchesLedger;

  return (
    <div className="mt-6 rounded-2xl border border-indigo-200 dark:border-indigo-900 bg-indigo-50/60 dark:bg-indigo-950/30 p-5">
      <div className="flex items-center gap-2 mb-3">
        <Link2 size={18} className="text-indigo-600" />
        <h3 className="font-bold text-indigo-900 dark:text-indigo-200">On-chain verification (Ethereum)</h3>
      </div>

      <div className="text-xs space-y-1 text-gray-700 dark:text-gray-300 font-mono break-all">
        <p>Network: {CHAIN_NAMES[chainId] || `Chain ${chainId}`}</p>
        {contract && <p>Contract: {contract}</p>}
        {certHash && <p>Certificate SHA-256: {certHash}</p>}
        {anchor?.tx_hash && (
          <p>
            Transaction:{' '}
            <a href={anchor.tx_url} target="_blank" rel="noreferrer" className="text-indigo-600 underline inline-flex items-center gap-1">
              {anchor.tx_hash.slice(0, 18)}… <ExternalLink size={12} />
            </a>
          </p>
        )}
        {anchor?.status === 'FAILED' && <p className="text-amber-600">Anchoring transaction failed; the database ledger record is still valid.</p>}
        {!anchor && <p className="text-amber-600">This certificate was issued before on-chain anchoring was enabled.</p>}
      </div>

      {anchor?.tx_hash && (
        <button
          type="button"
          onClick={verifyWithMetaMask}
          disabled={checking}
          className="mt-4 inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold disabled:opacity-60"
        >
          {checking ? <Loader2 size={16} className="animate-spin" /> : <Wallet size={16} />}
          {checking ? 'Checking on-chain…' : 'Verify with MetaMask'}
        </button>
      )}

      {result && (
        <div className={`mt-3 flex items-start gap-2 text-sm ${ok ? 'text-green-700' : 'text-red-600'}`}>
          {ok ? <ShieldCheck size={18} /> : <ShieldAlert size={18} />}
          <span>
            {ok
              ? `Verified on-chain: anchored in ledger block #${result.ledgerBlockIndex} on ${new Date(result.anchoredAt * 1000).toLocaleString()}.`
              : result.anchored
                ? 'Found on-chain, but the ledger block hash does not match the PakVerify record.'
                : 'Not found on-chain yet. If the transaction was just sent, wait a minute and try again.'}
          </span>
        </div>
      )}
      {message && <p className="mt-3 text-sm text-red-600">{message}</p>}
    </div>
  );
}
