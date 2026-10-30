import { useRef, useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { Download, QrCode, Shield, CheckCircle, Printer, AlertCircle, FileText } from 'lucide-react';
import DashboardLayout from '../components/DashboardLayout';
import EthereumVerification from '../components/EthereumVerification';
import { certAPI, applicationAPI } from '../api/apiClient';

export default function DigitalCertificatePage() {
  const { certNumber } = useParams();
  const certRef = useRef(null);

  const [cert, setCert] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [applications, setApplications] = useState([]);

  useEffect(() => {
    if (certNumber) {
      // ── Public QR scan verification ─────────────────────────────────────
      certAPI.verify(certNumber)
        .then(res => { setCert(res.data); setLoading(false); })
        .catch((requestError) => {
          const responseStatus = requestError.response?.status;
          setError(responseStatus === 404
            ? 'Certificate not found. Check the certificate number and try again.'
            : 'Certificate verification is temporarily unavailable. Please try again later.');
          setLoading(false);
        });
    } else {
      // ── Citizen: load completed application + certificate data ───────────
      applicationAPI.list()
        .then(res => {
          const completed = res.data.filter(a => a.status === 'COMPLETED');
          const issued = completed.filter(a => a.certificate?.certificate_number);
          setApplications(issued);
          if (issued.length > 0) {
            const app = issued[0];
            const certData = app.certificate;

            // Build cert shape that matches the public-verify response shape
            setCert({
              certificate_number: certData.certificate_number,
              applicant_name:     app.applicant?.full_name     || 'N/A',
              cnic:               app.applicant?.cnic          || 'N/A',
              province:           app.nadra_details?.province  || app.applicant_province || null,
              district:           app.nadra_details?.district  || null,
              authority:          null,   // will be computed client-side
              issue_date:         certData?.issue_date         || null,
              expiry_date:        certData?.validity_expiry     || null,
              status:             certData?.status             || app.status,
              verification_url:   certData?.verification_url  || null,
              valid:              certData?.status === 'VALID',
              app_data:           app,
            });
          } else if (completed.length > 0) {
            setError('Your application is marked completed, but its certificate record is unavailable. Please contact the issuing authority.');
          }
          setLoading(false);
        })
        .catch((requestError) => {
          setError(requestError.response
            ? 'Certificates could not be loaded. Please try again.'
            : 'The certificate service could not be reached. Please try again later.');
          setLoading(false);
        });
    }
  }, [certNumber]);

  // Compute authority name dynamically from province — NO hardcoded "Punjab Police"
  const PROVINCE_AUTHORITY = {
    'Punjab':             'Punjab Police',
    'Sindh':              'Sindh Police',
    'Khyber Pakhtunkhwa': 'KP Police',
    'KPK':                'KP Police',
    'KP':                 'KP Police',
    'Balochistan':        'Balochistan Police',
    'Azad Kashmir':       'AJK Police',
    'AJK':                'AJK Police',
    'Gilgit-Baltistan':   'GB Police',
    'GB':                 'GB Police',
    'Islamabad':          'ICT Police',
    'Federal':            'Federal Police',
  };
  const authorityName = cert?.authority
    || (cert?.province ? (PROVINCE_AUTHORITY[cert.province] || `${cert.province} Police`) : 'Pakistan Police');

  const isCertificateAuthentic = cert?.valid === true && cert?.integrity_valid !== false;
  const certificateStateLabel = cert?.integrity_valid === false
    ? 'TAMPERED'
    : cert?.expired
      ? 'EXPIRED'
      : cert?.valid
        ? 'AUTHENTIC'
        : 'INVALID';
  const certificateStateDetail = cert?.integrity_valid === false
    ? (cert?.integrity_reason || 'Certificate content does not match the original issued record.')
    : cert?.expired
      ? 'This certificate has expired.'
      : cert?.valid
        ? 'The original PDF hash matches the issued record.'
        : 'This certificate is not currently valid.';

  // QR target: use backend-provided verification_url if available,
  // otherwise fall back to window.location.origin + cert number
  const qrTarget = cert?.verification_url
    || (cert?.certificate_number
      ? `${window.location.origin}/verify/${cert.certificate_number}`
      : null);

  const print = () => window.print();

  const handleDownloadPdf = async (appId) => {
    try {
      const response = await applicationAPI.downloadCert(appId);
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `Certificate_${appId}.pdf`);
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch {
      alert('Failed to download PDF.');
    }
  };

  if (loading) {
    return (
      <DashboardLayout role={certNumber ? 'public' : 'citizen'} userName={cert?.applicant_name || 'Citizen'}>
        <div className="flex items-center justify-center py-16">
          <svg className="animate-spin h-8 w-8 text-cyan-400" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
          </svg>
        </div>
      </DashboardLayout>
    );
  }

  if (error || (!certNumber && applications.length === 0)) {
    return (
      <DashboardLayout role={certNumber ? 'public' : 'citizen'} userName="Citizen">
        <div className="glass-card p-12 text-center max-w-2xl mx-auto mt-10">
          <AlertCircle size={40} className="text-white/10 mx-auto mb-4" />
          <h2 className="text-white text-xl font-bold mb-2">No Certificates Found</h2>
          <p className="text-white/40 mb-6">{error || "You don't have any completed applications with generated certificates yet."}</p>
          {!certNumber && (
            <Link to="/citizen/request" className="btn-primary inline-flex items-center gap-2">
              <FileText size={16} /> Start a New Request
            </Link>
          )}
        </div>
      </DashboardLayout>
    );
  }

  return (
    <DashboardLayout role={certNumber ? 'public' : 'citizen'} userName={cert?.applicant_name || 'Citizen'}>
      <div className="mb-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="font-display text-2xl font-bold text-white">Digital Certificate</h1>
          <p className="text-white/50 mt-1 text-sm">Your verified police character certificate</p>
        </div>
        <div className="flex gap-3">
          <button onClick={print} className="btn-secondary flex items-center gap-2 text-sm">
            <Printer size={16}/> Print
          </button>
          {!certNumber && cert?.app_data?.id && (
            <button onClick={() => handleDownloadPdf(cert.app_data.id)} className="btn-primary flex items-center gap-2 text-sm">
              <Download size={16}/> Download PDF
            </button>
          )}
        </div>
      </div>

      {/* Authenticity banner */}
      <div className={`glass-card p-4 border flex items-center gap-3 mb-6 ${isCertificateAuthentic ? 'border-green-400/30 bg-green-400/5' : 'border-red-400/30 bg-red-400/5'}`}>
        <div className={isCertificateAuthentic ? 'text-green-400' : 'text-red-400'}>
          {isCertificateAuthentic ? <CheckCircle size={20} className="flex-shrink-0"/> : <AlertCircle size={20} className="flex-shrink-0"/>}
        </div>
        <div className="flex-1">
          <p className={`font-semibold text-sm ${isCertificateAuthentic ? 'text-green-400' : 'text-red-400'}`}>
            {isCertificateAuthentic ? '✓ AUTHENTIC CERTIFICATE' : '⚠ CERTIFICATE VERIFICATION FAILED'}
          </p>
          <p className="text-white/40 text-xs">{certificateStateDetail}</p>
        </div>
        <span className="font-mono text-cyan-400 text-xs border border-cyan-400/30 px-2 py-1 rounded-lg">
          {cert?.certificate_number || 'CERT-XXXXXX'}
        </span>
      </div>

      {(cert?.original_hash || cert?.current_hash) && (
        <div className="grid md:grid-cols-2 gap-4 mb-6">
          <div className="glass-card p-4 border border-white/10 bg-white/5">
            <p className="text-white/40 text-[11px] uppercase tracking-[0.2em] mb-2">Original Hash</p>
            <p className="font-mono text-xs text-cyan-300 break-all">{cert?.original_hash || 'N/A'}</p>
          </div>
          <div className="glass-card p-4 border border-white/10 bg-white/5">
            <p className="text-white/40 text-[11px] uppercase tracking-[0.2em] mb-2">Current PDF Hash</p>
            <p className="font-mono text-xs text-amber-300 break-all">{cert?.current_hash || 'N/A'}</p>
          </div>
        </div>
      )}

      {/* Certificate */}
      <div className="flex justify-center mb-10">
        <div ref={certRef} className="w-full max-w-2xl certificate-bg rounded-3xl overflow-hidden shadow-2xl print:shadow-none bg-white">
          {/* Header band — authority name is DYNAMIC from province */}
          <div className="bg-gradient-to-r from-[#003580] to-[#00529b] px-8 py-5 flex items-center justify-between">
            <div className="flex items-center gap-4">
              <div className="w-14 h-14 rounded-full bg-white/20 flex items-center justify-center text-2xl">🏅</div>
              <div className="text-white">
                <p className="text-xs opacity-70 uppercase tracking-wider">حکومت پاکستان</p>
                <p className="font-bold text-lg leading-tight">Government of Pakistan</p>
                {/* Dynamic authority — never hardcoded */}
                <p className="text-xs opacity-80" id="cert-authority-name">{authorityName} — AI Verification System</p>
              </div>
            </div>
            <div className="text-white text-right">
              <div className="w-12 h-12 rounded-full bg-white/20 flex items-center justify-center ml-auto">
                <Shield size={24} className="text-white"/>
              </div>
            </div>
          </div>

          {/* Certificate body */}
          <div className="px-8 py-8 bg-gradient-to-b from-[#f8f4e8] to-[#fff8e8]">
            <div className="text-center mb-8">
              <div className="inline-block border-b-4 border-[#003580] pb-2 mb-1">
                <h2 className="text-[#003580] font-bold text-2xl uppercase tracking-wider">Character Certificate</h2>
              </div>
              <p className="text-[#003580]/60 text-sm">پولیس کردار سرٹیفیکیٹ</p>
            </div>

            <div className="text-[#2c3e50] leading-relaxed mb-8">
              <p className="mb-4 text-sm text-center text-[#003580]/60 italic">This is to certify that:</p>
              <div className="bg-white/70 rounded-2xl p-6 border border-[#003580]/10 mb-6">
                <div className="grid sm:grid-cols-2 gap-4">
                  {[
                    ['Full Name',     cert?.applicant_name || 'N/A'],
                    ['CNIC Number',   cert?.cnic           || 'N/A'],
                    ['Province',      cert?.province       || '—'],
                    ['District',      cert?.district       || '—'],
                    ['Issue Date',    cert?.issue_date     || 'N/A'],
                    ['Valid Until',   cert?.expiry_date    || 'N/A'],
                    ['Status',        cert?.status         || 'N/A'],
                    ['Certificate #', cert?.certificate_number || 'N/A'],
                  ].map(([k, v]) => (
                    <div key={k}>
                      <p className="text-[#003580]/50 text-xs font-medium uppercase tracking-wide">{k}</p>
                      <p className="text-[#2c3e50] font-semibold text-sm mt-0.5">{v}</p>
                    </div>
                  ))}
                </div>
              </div>
              {/* Body text uses dynamic province — no hardcoded "Punjab" */}
              <p className="text-sm text-justify leading-relaxed text-[#4a5568]">
                has been verified through the <strong>AI-Powered Police Verification System</strong> and is found to have{' '}
                <strong className="text-green-700"> No Criminal Record </strong>
                in the{' '}
                <strong id="cert-body-authority">{authorityName}</strong>{' '}
                Database as of the date of issue. This certificate is issued for the purpose of{' '}
                <strong> Employment / Verification</strong> and is valid for a period of six (6) months from the date of issue.
              </p>
            </div>

            {/* AI Verification badge */}
            <div className={`flex items-center gap-3 border rounded-xl p-3 mb-8 ${isCertificateAuthentic ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}`}>
            <div className={isCertificateAuthentic ? 'text-green-600' : 'text-red-600'}>
              {isCertificateAuthentic ? <CheckCircle size={20} className="flex-shrink-0"/> : <AlertCircle size={20} className="flex-shrink-0"/>}
            </div>
            <div className="flex-1">
              <p className={isCertificateAuthentic ? 'text-green-800 font-semibold text-xs' : 'text-red-800 font-semibold text-xs'}>Certificate Integrity {isCertificateAuthentic ? 'Verified' : 'Failed'}</p>
              <p className={isCertificateAuthentic ? 'text-green-600 text-xs' : 'text-red-600 text-xs'}>{certificateStateDetail}</p>
            </div>
            <div className={isCertificateAuthentic ? 'text-green-600 text-xs font-mono border border-green-300 px-2 py-1 rounded-lg' : 'text-red-600 text-xs font-mono border border-red-300 px-2 py-1 rounded-lg'}>{certificateStateLabel}</div>
            </div>

            {/* Signatures + QR */}
            <div className="grid grid-cols-3 gap-4 mt-4">
              <div className="text-center">
                <div className="h-12 border-b-2 border-[#003580]/20 mb-2 flex items-end justify-center">
                  <span className="text-[#003580]/40 font-script text-2xl italic">SHO</span>
                </div>
                <p className="text-[#003580] text-xs font-bold">SHO Signature</p>
              </div>

              {/* QR Code — uses backend verification_url or falls back to window.location.origin */}
              <div className="text-center flex flex-col items-center">
                <div className="w-24 h-24 bg-white border-2 border-[#003580]/20 rounded-xl flex items-center justify-center mb-2 overflow-hidden p-1">
                  {qrTarget ? (
                    <img
                      src={`https://api.qrserver.com/v1/create-qr-code/?size=150x150&data=${encodeURIComponent(qrTarget)}`}
                      alt="Verification QR"
                      className="w-full h-full object-contain"
                    />
                  ) : (
                    <QrCode size={56} className="text-[#003580]" />
                  )}
                </div>
                <p className="text-[#003580]/50 text-xs">Scan to Verify</p>
              </div>

              <div className="text-center">
                <div className="h-12 border-b-2 border-[#003580]/20 mb-2 flex items-end justify-center">
                  <span className="text-[#003580]/40 font-script text-2xl italic">SP</span>
                </div>
                <p className="text-[#003580] text-xs font-bold">SP Headquarters</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Optional public on-chain check (shown only when Ethereum anchoring is configured) */}
      {(certNumber || cert?.certificate_number) && (
        <div className="max-w-2xl mx-auto mb-10 print:hidden">
          <EthereumVerification certNumber={certNumber || cert.certificate_number} />
        </div>
      )}
    </DashboardLayout>
  );
}
