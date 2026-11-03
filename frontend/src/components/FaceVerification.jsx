/**
 * FaceVerification.jsx
 * ====================
 * Live face verification component for the Citizen Dashboard.
 *
 * Flow:
 * 1. Citizen clicks "Start Verification"
 * 2. Webcam opens with live preview
 * 3. Citizen clicks "Start Face Scan"
 * 4. A signed head-turn challenge is fetched (GET /api/face-verify/liveness-challenge/):
 *    look straight, then turn LEFT / RIGHT in a random order. One frame per step.
 * 5. All frames go to POST /api/face-verify/verify-with-cnic/. The server checks the
 *    3-D head movement (a photo or screen cannot produce it), then matches the face.
 * 6. Result is displayed with similarity score and status
 * 7. Verification report is shown with blockchain hash
 */

import { useState, useRef, useCallback, useEffect } from 'react';
import { API, getAccessToken, getStoredAuth } from '../api/apiClient';

// Webcam capture quality (0.9 = 90% JPEG quality)
const CAPTURE_QUALITY = 0.9;

// ─── Helper: get JWT token ──────────────────────────────────────────────────
const getAuthToken = () => {
  try {
    return getAccessToken();
  } catch {
    return '';
  }
};

// ─── Status Badge Component ─────────────────────────────────────────────────
const StatusBadge = ({ status, similarityPct }) => {
  const configs = {
    VERIFIED: {
      bg: 'linear-gradient(135deg, #00b09b, #96c93d)',
      icon: '✅',
      text: `Verified (${similarityPct?.toFixed(2)}%)`,
    },
    FAILED: {
      bg: 'linear-gradient(135deg, #f5515f, #9f041b)',
      icon: '❌',
      text: 'Verification Failed',
    },
    NO_FACE: {
      bg: 'linear-gradient(135deg, #f7971e, #ffd200)',
      icon: '👤',
      text: 'No Face Detected',
    },
    MULTIPLE_FACES: {
      bg: 'linear-gradient(135deg, #f7971e, #ffd200)',
      icon: '👥',
      text: 'Multiple Faces',
    },
    LOW_QUALITY: {
      bg: 'linear-gradient(135deg, #8e9eab, #eef2f3)',
      icon: '📷',
      text: 'Low Image Quality',
    },
    STORE_NOT_READY: {
      bg: 'linear-gradient(135deg, #614385, #516395)',
      icon: '⚠️',
      text: 'System Not Ready',
    },
  };
  const cfg = configs[status] || configs.FAILED;
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: '6px',
      padding: '6px 16px',
      borderRadius: '24px',
      background: cfg.bg,
      color: '#fff',
      fontWeight: 700,
      fontSize: '14px',
      letterSpacing: '0.5px',
      boxShadow: '0 4px 15px rgba(0,0,0,0.2)',
    }}>
      {cfg.icon} {cfg.text}
    </span>
  );
};

// ─── Similarity Meter ────────────────────────────────────────────────────────
const SimilarityMeter = ({ pct }) => {
  const color = pct >= 90 ? '#00b09b' : pct >= 70 ? '#f7971e' : '#f5515f';
  return (
    <div style={{ margin: '12px 0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
        <span style={{ fontSize: '13px', color: '#aaa' }}>Similarity Score</span>
        <span style={{ fontSize: '13px', fontWeight: 700, color }}>{pct?.toFixed(2)}%</span>
      </div>
      <div style={{
        width: '100%', height: '8px',
        background: 'rgba(255,255,255,0.1)',
        borderRadius: '4px', overflow: 'hidden',
      }}>
        <div style={{
          width: `${Math.min(pct, 100)}%`,
          height: '100%',
          background: color,
          borderRadius: '4px',
          transition: 'width 1s ease',
        }} />
      </div>
    </div>
  );
};

// ─── Main Component ──────────────────────────────────────────────────────────
const FaceVerification = ({ applicationId = null }) => {
  // ── State ────────────────────────────────────────────────────────────
  const [phase, setPhase] = useState('cnic_input');
  const [inputCnic, setInputCnic] = useState(() => {
    // Pre-fill with the authenticated citizen's own registered CNIC
    try {
      return getStoredAuth()?.user?.cnic || '';
    } catch { return ''; }
  });
  const [cnicData, setCnicData] = useState(null);
  
  const [capturedImage, setCapturedImage] = useState(null); // base64
  const [report, setReport] = useState(null);
  const [livenessScore, setLivenessScore] = useState(null);
  const [message, setMessage] = useState('');
  const [errorText, setErrorText] = useState('');
  const [cameraError, setCameraError] = useState('');
  const [countdown, setCountdown] = useState(null);
  const [storeStatus, setStoreStatus] = useState(null);
  const [scanStep, setScanStep] = useState(null);      // { name, text, index, total }
  const [scanning, setScanning] = useState(false);

  // ── Refs ─────────────────────────────────────────────────────────────
  const videoRef  = useRef(null);
  const canvasRef = useRef(null);
  const streamRef = useRef(null);

  // ── Load store status on mount ────────────────────────────────────────
  useEffect(() => {
    const token = getAuthToken();
    if (!token) return;
    API.get('/api/face-verify/status/')
      .then(res => setStoreStatus(res.data))
      .catch(() => setStoreStatus({ ready: false, error: 'Could not reach server' }));
  }, []);

  // ── CNIC Verification Helper ──────────────────────────────────────────
  const handleVerifyCnic = async () => {
    if (!inputCnic) {
      setErrorText('Please enter your CNIC.');
      setPhase('error');
      return;
    }
    setPhase('cnic_verifying');
    try {
      const response = await API.post('/api/face-verify/verify-cnic/', { cnic: inputCnic });
      
      if (response.data.found) {
        if (!response.data.has_face_embedding) {
           setErrorText('CNIC found, but no face record exists in the database. Contact admin.');
           setPhase('error');
           return;
        }
        setCnicData(response.data);
        setPhase('idle'); // Move to camera readiness
      } else {
        setErrorText(response.data.error || 'CNIC not found in NADRA database.');
        setPhase('error');
      }
    } catch (err) {
      const detail = err.response?.data?.error || 'Verification failed. Please try again.';
      setErrorText(detail);
      setPhase('error');
    }
  };

  // ── Camera helpers ────────────────────────────────────────────────────
  const startCamera = useCallback(async () => {
    setCameraError('');
    setPhase('camera');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width:  { ideal: 640 },
          height: { ideal: 480 },
          facingMode: 'user',
        },
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
    } catch (err) {
      setCameraError(
        err.name === 'NotAllowedError'
          ? 'Camera access was denied. Please allow camera access and try again.'
          : `Could not access camera: ${err.message}`
      );
      setPhase('idle');
    }
  }, []);

  const stopCamera = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop());
      streamRef.current = null;
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
  }, []);

  // Stop camera on unmount
  useEffect(() => () => stopCamera(), [stopCamera]);

  // ── Capture frame from video ──────────────────────────────────────────
  const captureFrame = useCallback(() => {
    const video  = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) return null;

    canvas.width  = video.videoWidth  || 640;
    canvas.height = video.videoHeight || 480;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', CAPTURE_QUALITY);
  }, []);

  // ── Convert base64 to Blob ────────────────────────────────────────────
  const base64ToBlob = (dataUrl) => {
    const [header, data] = dataUrl.split(',');
    const mimeMatch = header.match(/:(.*?);/);
    const mime = mimeMatch ? mimeMatch[1] : 'image/jpeg';
    const binary = atob(data);
    const arr = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) arr[i] = binary.charCodeAt(i);
    return new Blob([arr], { type: mime });
  };

  // ── Head-turn liveness scan ──────────────────────────────────────────
  const STEP_TEXT = {
    CENTER: 'Look straight at the camera',
    LEFT:   'Turn your head to your LEFT (look toward your left shoulder)',
    RIGHT:  'Turn your head to your RIGHT (look toward your right shoulder)',
  };
  const STEP_ARROW = { CENTER: '●', LEFT: '←', RIGHT: '→' };
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  const countdownFrom = async (seconds) => {
    for (let c = seconds; c > 0; c -= 1) {
      setCountdown(c);
      await sleep(1000);
    }
    setCountdown(null);
  };

  const handleCaptureAndVerify = async () => {
    if (scanning) return;
    setScanning(true);
    setErrorText('');
    let challenge;
    try {
      challenge = (await API.get('/api/face-verify/liveness-challenge/')).data;
    } catch (err) {
      setScanning(false);
      stopCamera();
      setErrorText(err.response?.data?.error || 'Could not start the liveness check. Please try again.');
      setPhase('error');
      return;
    }

    const frames = {};
    const steps = challenge.steps || ['CENTER', 'LEFT', 'RIGHT'];
    for (let i = 0; i < steps.length; i += 1) {
      const name = steps[i];
      setScanStep({ name, text: STEP_TEXT[name] || name, index: i + 1, total: steps.length });
      await countdownFrom(name === 'CENTER' ? 3 : 2);
      await sleep(name === 'CENTER' ? 0 : 400);   // let the turn finish
      const dataUrl = captureFrame();
      if (!dataUrl) {
        setScanning(false);
        setScanStep(null);
        stopCamera();
        setErrorText('Failed to capture from webcam.');
        setPhase('error');
        return;
      }
      frames[name] = dataUrl;
    }
    setScanStep(null);
    setScanning(false);
    executeVerify(frames, challenge.challenge_token);
  };

  const executeVerify = async (frames, challengeToken) => {
    setCapturedImage(frames.CENTER);
    stopCamera();
    setPhase('processing');

    try {
      const formData = new FormData();
      Object.entries(frames).forEach(([name, dataUrl]) => {
        formData.append(`frame_${name}`, base64ToBlob(dataUrl), `${name.toLowerCase()}.jpg`);
      });
      formData.append('challenge_token', challengeToken);
      formData.append('cnic', cnicData.cnic); // Passed from step 1
      if (applicationId) {
        formData.append('application_id', applicationId);
      }

      const response = await API.post('/api/face-verify/verify-with-cnic/', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });

      setReport(response.data.report);
      setLivenessScore(response.data.liveness_score ?? null);
      setMessage(response.data.message);
      setPhase('result');

    } catch (err) {
      const data = err.response?.data || {};
      const tips = {
        FLAT_IMAGE: 'A photo, printed picture or screen was detected. Only a live person can be verified.',
        NO_TURN: 'Tip: turn your head clearly, as if looking at your shoulder.',
        TURN_NOT_BOTH_SIDES: 'Tip: turn to the side shown on screen each time.',
        NOT_FRONTAL: 'Tip: for the first step, face the camera straight on.',
      };
      const detail =
        data.error || data.errors || data.detail || err.message || 'Unknown error';
      const text = typeof detail === 'object' ? JSON.stringify(detail) : detail;
      setErrorText(tips[data.liveness_reason] ? `${text}\n\n${tips[data.liveness_reason]}` : text);
      setPhase('error');
    }
  };

  const handleReset = () => {
    setCapturedImage(null);
    setReport(null);
    setLivenessScore(null);
    setMessage('');
    setErrorText('');
    setCameraError('');
    setCountdown(null);
    setScanStep(null);
    setScanning(false);
    if (cnicData) {
      setPhase('idle'); // Go back to camera ready if CNIC already verified
    } else {
      setPhase('cnic_input');
    }
  };
  
  const handleFullReset = () => {
    handleReset();
    setInputCnic('');
    setCnicData(null);
    setPhase('cnic_input');
  };

  // ─────────────────────────────────────────────────────────────────────
  // Render
  // ─────────────────────────────────────────────────────────────────────
  return (
    <div style={{
      maxWidth: '700px',
      margin: '0 auto',
      fontFamily: "'Inter', 'Segoe UI', sans-serif",
      color: '#f0f0f0',
    }}>
      {/* ── Header ─────────────────────────────────────────────────── */}
      <div style={{
        background: 'linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%)',
        borderRadius: '20px',
        padding: '28px',
        marginBottom: '20px',
        border: '1px solid rgba(255,255,255,0.08)',
        boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '8px' }}>
          <span style={{ fontSize: '32px' }}>🔐</span>
          <div>
            <h2 style={{ margin: 0, fontSize: '20px', fontWeight: 700 }}>
              AI Face Verification
            </h2>
            <p style={{ margin: 0, fontSize: '13px', color: '#8ecae6', marginTop: '2px' }}>
              ArcFace Neural Network · Cosine Similarity · Blockchain Audit
            </p>
          </div>
        </div>

        {/* Store status pill */}
        {storeStatus && (
          <div style={{
            display: 'inline-flex', alignItems: 'center', gap: '6px',
            padding: '4px 12px', borderRadius: '12px', fontSize: '12px',
            background: storeStatus.ready
              ? 'rgba(0,176,155,0.15)'
              : 'rgba(245,81,95,0.15)',
            border: `1px solid ${storeStatus.ready ? '#00b09b' : '#f5515f'}`,
            color: storeStatus.ready ? '#00b09b' : '#f5515f',
          }}>
            <span style={{
              width: '7px', height: '7px', borderRadius: '50%',
              background: storeStatus.ready ? '#00b09b' : '#f5515f',
              animation: storeStatus.ready ? 'pulse 2s infinite' : 'none',
            }} />
            {storeStatus.ready
              ? `Ready · ${storeStatus.total_records?.toLocaleString()} records · ${storeStatus.model}`
              : `Not ready: ${storeStatus.error || 'Embeddings not generated'}`
            }
          </div>
        )}
      </div>

      {/* ── Hidden canvas for frame capture ─────────────────────── */}
      <canvas ref={canvasRef} style={{ display: 'none' }} />

      {/* ════════════════════════════════════════════════════════════ */}
      {/* PHASE: cnic_input ───────────────────────────────────────── */}
      {/* ════════════════════════════════════════════════════════════ */}
      {phase === 'cnic_input' && (
        <div style={cardStyle}>
          <div style={{ textAlign: 'center', padding: '20px 0' }}>
            <div style={{ fontSize: '64px', marginBottom: '16px' }}>🪪</div>
            <h3 style={{ margin: '0 0 8px', fontSize: '18px' }}>Step 1: CNIC Verification</h3>
            <p style={{ margin: '0 0 24px', color: '#aaa', fontSize: '14px', lineHeight: 1.6 }}>
              Please enter your 13-digit CNIC number below to verify against the NADRA database.
            </p>

            <div style={{ maxWidth: '340px', margin: '0 auto 24px' }}>
              <p style={{ fontSize: '12px', color: '#8ecae6', marginBottom: '8px', textAlign: 'left' }}>
                Your registered CNIC
              </p>
              <input
                type="text"
                value={inputCnic}
                readOnly
                style={{
                  width: '100%', padding: '12px 16px', borderRadius: '10px',
                  background: 'rgba(0,176,155,0.08)',
                  border: '1px solid rgba(0,176,155,0.4)',
                  color: '#00b09b', fontSize: '16px', textAlign: 'center',
                  outline: 'none', fontFamily: 'monospace', fontWeight: 700,
                  cursor: 'default',
                }}
              />
              <p style={{ fontSize: '11px', color: '#555', marginTop: '6px', textAlign: 'center' }}>
                🔒 Verification is only allowed for your own registered CNIC
              </p>
            </div>

            <button
              id="verify-cnic-btn"
              onClick={handleVerifyCnic}
              disabled={!inputCnic || (storeStatus && !storeStatus.ready)}
              style={{
                ...btnStyle,
                background: (!inputCnic || (storeStatus && !storeStatus.ready))
                  ? 'rgba(255,255,255,0.1)'
                  : 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                cursor: (!inputCnic || (storeStatus && !storeStatus.ready)) ? 'not-allowed' : 'pointer',
                opacity: (!inputCnic || (storeStatus && !storeStatus.ready)) ? 0.5 : 1,
              }}
            >
              Verify CNIC
            </button>

            {storeStatus && !storeStatus.ready && (
              <p style={{ color: '#f5515f', fontSize: '13px', marginTop: '12px' }}>
                ⚠️ Embedding store not ready. Run:{' '}
                <code style={{ background: 'rgba(255,255,255,0.1)', padding: '2px 6px', borderRadius: '4px' }}>
                  python manage.py generate_nadra_embeddings
                </code>
              </p>
            )}
          </div>
        </div>
      )}

      {/* ════════════════════════════════════════════════════════════ */}
      {/* PHASE: cnic_verifying ───────────────────────────────────── */}
      {/* ════════════════════════════════════════════════════════════ */}
      {phase === 'cnic_verifying' && (
        <div style={{ ...cardStyle, textAlign: 'center', padding: '40px' }}>
          <div style={spinnerStyle} />
          <h3 style={{ margin: '20px 0 8px' }}>Verifying CNIC...</h3>
          <p style={{ color: '#aaa', fontSize: '13px', margin: 0 }}>
            Querying NADRA database for {inputCnic}
          </p>
        </div>
      )}

      {/* ════════════════════════════════════════════════════════════ */}
      {/* PHASE: idle ─────────────────────────────────────────────── */}
      {/* ════════════════════════════════════════════════════════════ */}
      {phase === 'idle' && (
        <div style={cardStyle}>
          <div style={{ textAlign: 'center', padding: '20px 0' }}>
            <div style={{ fontSize: '72px', marginBottom: '16px', opacity: 0.9 }}>📷</div>
            <h3 style={{ margin: '0 0 8px', fontSize: '18px' }}>Step 2: Face Verification</h3>
            
            <div style={{ 
              background: 'rgba(0,176,155,0.1)', border: '1px solid rgba(0,176,155,0.3)', 
              borderRadius: '8px', padding: '12px', margin: '0 auto 20px', maxWidth: '400px'
            }}>
              <p style={{ margin: 0, color: '#00b09b', fontSize: '13px', fontWeight: 600 }}>
                ✅ CNIC Verified: {cnicData?.name}
              </p>
            </div>
            
            <p style={{ margin: '0 0 24px', color: '#aaa', fontSize: '14px', lineHeight: 1.6 }}>
              Click the button below to open your webcam.<br />
              Ensure good lighting and position your face clearly in frame.
            </p>

            <button
              id="start-verification-btn"
              onClick={startCamera}
              style={{
                ...btnStyle,
                background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
              }}
            >
              🎥 Start Face Capture
            </button>
            
            <div style={{ marginTop: '16px' }}>
               <button onClick={handleFullReset} style={{ background: 'transparent', border: 'none', color: '#8ecae6', fontSize: '13px', cursor: 'pointer', textDecoration: 'underline' }}>
                 Not {cnicData?.name}? Enter a different CNIC
               </button>
            </div>
          </div>
        </div>
      )}

      {/* ════════════════════════════════════════════════════════════ */}
      {/* PHASE: camera ───────────────────────────────────────────── */}
      {/* ════════════════════════════════════════════════════════════ */}
      {phase === 'camera' && (
        <div style={cardStyle}>
          {cameraError ? (
            <div style={{ textAlign: 'center', color: '#f5515f', padding: '20px' }}>
              <span style={{ fontSize: '48px' }}>🚫</span>
              <p style={{ marginTop: '12px' }}>{cameraError}</p>
              <button onClick={handleReset} style={{ ...btnStyle, background: '#555' }}>
                Go Back
              </button>
            </div>
          ) : (
            <>
              {/* Video preview */}
              <div style={{ position: 'relative', borderRadius: '12px', overflow: 'hidden' }}>
                <video
                  ref={videoRef}
                  autoPlay
                  muted
                  playsInline
                  style={{
                    width: '100%', display: 'block',
                    borderRadius: '12px',
                    transform: 'scaleX(-1)', // mirror effect
                  }}
                />

                {/* Face guide overlay */}
                <div style={{
                  position: 'absolute', top: '50%', left: '50%',
                  transform: 'translate(-50%, -55%)',
                  width: '180px', height: '220px',
                  border: '3px solid rgba(102,126,234,0.8)',
                  borderRadius: '50%',
                  boxShadow: '0 0 0 9999px rgba(0,0,0,0.35)',
                  pointerEvents: 'none',
                }} />

                {/* Head-turn instruction banner */}
                {scanStep && (
                  <div style={{
                    position: 'absolute', left: 0, right: 0, top: 0,
                    padding: '10px 14px', borderRadius: '12px 12px 0 0',
                    background: 'rgba(0,27,80,0.85)', color: '#fff',
                    display: 'flex', alignItems: 'center', gap: '12px', zIndex: 2,
                  }}>
                    <span style={{ fontSize: '30px', fontWeight: 900, lineHeight: 1 }}>
                      {STEP_ARROW[scanStep.name]}
                    </span>
                    <div>
                      <div style={{ fontSize: '11px', opacity: 0.8, letterSpacing: '0.08em' }}>
                        STEP {scanStep.index} OF {scanStep.total} · LIVENESS CHECK
                      </div>
                      <div style={{ fontSize: '15px', fontWeight: 700 }}>{scanStep.text}</div>
                    </div>
                  </div>
                )}

                {/* Countdown overlay */}
                {countdown !== null && (
                  <div style={{
                    position: 'absolute', inset: 0,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    background: 'rgba(0,0,0,0.5)',
                    borderRadius: '12px',
                  }}>
                    <span style={{
                      fontSize: '96px', fontWeight: 900, color: '#fff',
                      textShadow: '0 0 30px rgba(102,126,234,0.8)',
                      animation: 'scaleIn 0.3s ease',
                    }}>
                      {countdown}
                    </span>
                  </div>
                )}
              </div>

              <p style={{ textAlign: 'center', color: '#aaa', fontSize: '13px', margin: '12px 0' }}>
                {scanStep
                  ? 'Hold the pose until the countdown ends'
                  : 'Put your face in the oval. You will be asked to look straight, then turn left and right.'}
              </p>

              <div style={{ display: 'flex', gap: '12px', justifyContent: 'center' }}>
                <button
                  id="cancel-camera-btn"
                  onClick={() => { stopCamera(); handleReset(); }}
                  disabled={scanning}
                  style={{ ...btnStyle, background: '#555', flex: 1 }}
                >
                  Cancel
                </button>
                <button
                  id="capture-verify-btn"
                  onClick={handleCaptureAndVerify}
                  disabled={scanning}
                  style={{
                    ...btnStyle,
                    background: 'linear-gradient(135deg, #00b09b, #96c93d)',
                    flex: 2,
                    opacity: scanning ? 0.6 : 1,
                  }}
                >
                  {scanning ? 'Scanning… follow the steps' : '📸 Start Face Scan'}
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {/* ════════════════════════════════════════════════════════════ */}
      {/* PHASE: processing ───────────────────────────────────────── */}
      {/* ════════════════════════════════════════════════════════════ */}
      {phase === 'processing' && (
        <div style={{ ...cardStyle, textAlign: 'center', padding: '40px' }}>
          {capturedImage && (
            <img
              src={capturedImage}
              alt="Captured frame"
              style={{
                width: '160px', height: '160px', objectFit: 'cover',
                borderRadius: '50%', marginBottom: '20px',
                border: '4px solid #667eea',
                transform: 'scaleX(-1)',
              }}
            />
          )}
          <div style={spinnerStyle} />
          <h3 style={{ margin: '20px 0 8px' }}>Analyzing Face...</h3>
          <p style={{ color: '#aaa', fontSize: '13px', margin: 0 }}>
            Generating embedding · Searching {storeStatus?.total_records?.toLocaleString()} records
          </p>
          <div style={{ marginTop: '16px' }}>
            {['Detecting face...', 'Generating ArcFace embedding...', 'Running cosine search...'].map((step, i) => (
              <div key={i} style={{
                fontSize: '12px', color: '#667eea',
                animation: `fadeIn 0.5s ease ${i * 0.4}s both`,
                marginBottom: '4px',
              }}>
                ⚡ {step}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ════════════════════════════════════════════════════════════ */}
      {/* PHASE: result ───────────────────────────────────────────── */}
      {/* ════════════════════════════════════════════════════════════ */}
      {phase === 'result' && report && (
        <div style={cardStyle}>
          {/* Status banner */}
          <div style={{
            background: report.is_verified
              ? 'linear-gradient(135deg, rgba(0,176,155,0.15), rgba(150,201,61,0.15))'
              : 'linear-gradient(135deg, rgba(245,81,95,0.15), rgba(159,4,27,0.15))',
            border: `1px solid ${report.is_verified ? '#00b09b' : '#f5515f'}`,
            borderRadius: '12px',
            padding: '20px',
            marginBottom: '20px',
            textAlign: 'center',
          }}>
            <div style={{ fontSize: '48px', marginBottom: '8px' }}>
              {report.is_verified ? '✅' : '❌'}
            </div>
            <StatusBadge status={report.status} similarityPct={report.similarity_pct} />
            <p style={{ margin: '12px 0 0', color: '#ddd', fontSize: '14px' }}>{message}</p>
          </div>

          {/* Similarity meter */}
          <SimilarityMeter pct={report.similarity_pct} />

          {/* Report details grid */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: '12px',
            marginBottom: '20px',
          }}>
            <ReportField label="Matched Citizen" value={report.matched_citizen_name || '—'} />
            <ReportField label="Father Name" value={report.matched_father_name || '—'} />
            <ReportField label="Matched CNIC" value={report.matched_cnic || '—'} />
            <ReportField label="Similarity" value={`${report.similarity_pct?.toFixed(2)}%`} highlight />
            <ReportField
              label="Liveness"
              value={livenessScore === null ? '—' : `${(livenessScore * 100).toFixed(1)}%`}
              highlight={livenessScore !== null && livenessScore >= 0.7}
            />
            <ReportField label="Confidence" value={report.confidence_level_display || '—'} />
            <ReportField label="Status" value={report.status_display} />
            <ReportField label="Model" value={report.model_used} />
            <ReportField label="Processing Time" value={`${report.processing_time_ms?.toFixed(0)} ms`} />
            <ReportField
              label="Verification Time"
              value={new Date(report.verified_at).toLocaleString()}
              wide
            />
          </div>

          {/* Blockchain audit */}
          {report.blockchain_hash && (
            <div style={{
              background: 'rgba(102,126,234,0.08)',
              border: '1px solid rgba(102,126,234,0.3)',
              borderRadius: '10px',
              padding: '14px',
              marginBottom: '16px',
            }}>
              <div style={{ fontSize: '12px', color: '#667eea', fontWeight: 600, marginBottom: '6px' }}>
                ⛓ Blockchain Audit Trail
              </div>
              <div style={{ fontSize: '11px', color: '#aaa', wordBreak: 'break-all' }}>
                <span style={{ color: '#ddd' }}>Block #{report.blockchain_block_index}</span>
                {' · '}
                <span style={{ fontFamily: 'monospace' }}>{report.blockchain_hash}</span>
              </div>
            </div>
          )}

          {/* Report ID */}
          <div style={{ fontSize: '11px', color: '#555', textAlign: 'center', marginBottom: '16px' }}>
            Report ID: {report.report_id}
          </div>

          {/* Captured preview + action buttons */}
          <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
            {capturedImage && (
              <img
                src={capturedImage}
                alt="Your capture"
                style={{
                  width: '60px', height: '60px', objectFit: 'cover',
                  borderRadius: '50%', border: '2px solid #667eea',
                  transform: 'scaleX(-1)', flexShrink: 0,
                }}
              />
            )}
            <button
              id="verify-again-btn"
              onClick={handleReset}
              style={{ ...btnStyle, background: 'linear-gradient(135deg, #667eea, #764ba2)', flex: 1 }}
            >
              🔄 Retry Face Capture
            </button>
            <button
              onClick={handleFullReset}
              style={{ ...btnStyle, background: 'rgba(255,255,255,0.1)', flex: 1 }}
            >
              Change CNIC
            </button>
          </div>
        </div>
      )}

      {/* ════════════════════════════════════════════════════════════ */}
      {/* PHASE: error ────────────────────────────────────────────── */}
      {/* ════════════════════════════════════════════════════════════ */}
      {phase === 'error' && (
        <div style={{ ...cardStyle, textAlign: 'center', padding: '32px' }}>
          <div style={{ fontSize: '56px', marginBottom: '12px' }}>⚠️</div>
          <h3 style={{ color: '#f5515f', margin: '0 0 8px' }}>Verification Error</h3>
          <p style={{ color: '#aaa', fontSize: '14px', margin: '0 0 20px', whiteSpace: 'pre-line' }}>{errorText}</p>
          <div style={{ display: 'flex', gap: '12px', justifyContent: 'center' }}>
            <button
              onClick={handleReset}
              style={{ ...btnStyle, background: 'linear-gradient(135deg, #667eea, #764ba2)' }}
            >
              Try Again
            </button>
            {cnicData && (
              <button
                onClick={handleFullReset}
                style={{ ...btnStyle, background: 'rgba(255,255,255,0.1)' }}
              >
                Start Over
              </button>
            )}
          </div>
        </div>
      )}

      {/* ── CSS Keyframes ──────────────────────────────────────────── */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
        @keyframes spin {
          0% { transform: rotate(0deg); }
          100% { transform: rotate(360deg); }
        }
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(4px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes scaleIn {
          from { transform: scale(0.5); opacity: 0; }
          to   { transform: scale(1);   opacity: 1; }
        }
      `}</style>
    </div>
  );
};

// ─── Sub-components ──────────────────────────────────────────────────────────
const ReportField = ({ label, value, highlight = false, wide = false }) => (
  <div style={{
    background: 'rgba(255,255,255,0.04)',
    borderRadius: '8px',
    padding: '10px 14px',
    gridColumn: wide ? '1 / -1' : undefined,
  }}>
    <div style={{ fontSize: '11px', color: '#777', marginBottom: '3px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
      {label}
    </div>
    <div style={{
      fontSize: '14px',
      fontWeight: highlight ? 700 : 400,
      color: highlight ? '#00b09b' : '#f0f0f0',
    }}>
      {value}
    </div>
  </div>
);

// ─── Styles ───────────────────────────────────────────────────────────────────
const cardStyle = {
  background: 'linear-gradient(135deg, #1a1a2e, #16213e)',
  borderRadius: '16px',
  padding: '24px',
  border: '1px solid rgba(255,255,255,0.08)',
  boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
  marginBottom: '16px',
};

const btnStyle = {
  padding: '12px 24px',
  border: 'none',
  borderRadius: '10px',
  color: '#fff',
  fontSize: '14px',
  fontWeight: 600,
  cursor: 'pointer',
  transition: 'all 0.2s ease',
  letterSpacing: '0.3px',
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '6px',
};

const spinnerStyle = {
  width: '56px',
  height: '56px',
  border: '4px solid rgba(102,126,234,0.2)',
  borderTop: '4px solid #667eea',
  borderRadius: '50%',
  margin: '0 auto',
  animation: 'spin 1s linear infinite',
};

export default FaceVerification;
