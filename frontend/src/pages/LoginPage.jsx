import { useState, useRef, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useDispatch } from 'react-redux';
import { Shield, Eye, EyeOff, Lock, User, ChevronRight, AlertCircle } from 'lucide-react';
import { authAPI } from '../api/apiClient';
import { loginSuccess } from '../store/authSlice';

const PAGE_CONFIG = {
  citizen: { title: 'Citizen Portal', subtitle: 'Sign in to access citizen services', theme: 'text-cyan-400', border: 'focus:border-cyan-400', glow: 'bg-cyan-500/5', shadow: 'shadow-glow-cyan', grad: 'from-cyan-400 to-blue-600', icon: User },
  staff:   { title: 'Police Staff Portal', subtitle: 'Secure access for desk officers', theme: 'text-blue-400', border: 'focus:border-blue-400', glow: 'bg-blue-500/10', shadow: 'shadow-glow-blue', grad: 'from-blue-500 to-indigo-600', icon: Shield },
  admin:   { title: 'Authority & Admin', subtitle: 'Super Admin and Senior Officers', theme: 'text-purple-400', border: 'focus:border-purple-400', glow: 'bg-purple-500/10', shadow: 'shadow-glow-purple', grad: 'from-purple-500 to-pink-600', icon: Lock },
};

export default function LoginPage({ roleType = 'citizen' }) {
  const navigate  = useNavigate();
  const dispatch  = useDispatch();
  const [cnic, setCnic]       = useState('');
  const [password, setPassword] = useState('');
  const [showPwd, setShowPwd] = useState(false);
  const [otpMode, setOtpMode] = useState(false);
  const [otp, setOtp]         = useState(['','','','','','']);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState('');

  // Face Login State
  const [faceMode, setFaceMode] = useState(false);
  const [cameraActive, setCameraActive] = useState(false);
  const [scanStep, setScanStep] = useState(null);   // { name, text, index, total }
  const [countdown, setCountdown] = useState(null);

  const config = PAGE_CONFIG[roleType] || PAGE_CONFIG.citizen;
  const RoleIcon = config.icon;

  const videoRef = useRef(null);
  const streamRef = useRef(null);

  const formatCnic = (val) => {
    const d = val.replace(/\D/g,'').slice(0,13);
    if (d.length > 12) return `${d.slice(0,5)}-${d.slice(5,12)}-${d.slice(12)}`;
    if (d.length > 5)  return `${d.slice(0,5)}-${d.slice(5)}`;
    return d;
  };

  const startCamera = async () => {
    if (!cnic.trim()) { setError('Please enter your CNIC first.'); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' } });
      streamRef.current = stream;
      setCameraActive(true);
      setError('');
    } catch {
      setError('Camera access denied or unavailable.');
    }
  };

  useEffect(() => {
    if (cameraActive && videoRef.current && streamRef.current) {
      videoRef.current.srcObject = streamRef.current;
    }
  }, [cameraActive]);

  const stopCamera = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop());
      streamRef.current = null;
    }
    setCameraActive(false);
  };

  // Head-turn liveness scan: look straight, then LEFT / RIGHT in the order the
  // server chose. A photo or screen cannot produce the 3-D head movement.
  const STEP_TEXT = {
    CENTER: 'Look straight at the camera',
    LEFT:   'Turn your head to your LEFT',
    RIGHT:  'Turn your head to your RIGHT',
  };
  const STEP_ARROW = { CENTER: '●', LEFT: '←', RIGHT: '→' };
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  const grabFrame = () => {
    const video = videoRef.current;
    if (!video) return null;
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
    return new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.9));
  };

  const handleFaceLogin = async () => {
    if (!videoRef.current || loading) return;
    setError('');
    setLoading(true);

    let challenge;
    try {
      challenge = (await authAPI.loginFaceChallenge(cnic.trim())).data;
    } catch (err) {
      setError(err.response?.data?.error || 'Could not start the face scan.');
      setLoading(false);
      return;
    }

    const fd = new FormData();
    fd.append('cnic', cnic.trim());
    fd.append('challenge_token', challenge.challenge_token);
    const steps = challenge.steps || ['CENTER', 'LEFT', 'RIGHT'];
    for (let i = 0; i < steps.length; i += 1) {
      const name = steps[i];
      setScanStep({ name, text: STEP_TEXT[name] || name, index: i + 1, total: steps.length });
      for (let c = name === 'CENTER' ? 3 : 2; c > 0; c -= 1) {
        setCountdown(c);
        await sleep(1000);
      }
      setCountdown(null);
      if (name !== 'CENTER') await sleep(400);
      const blob = await grabFrame();
      if (!blob) {
        setScanStep(null);
        setLoading(false);
        setError('Could not capture from the camera.');
        return;
      }
      fd.append(`frame_${name}`, blob, `${name.toLowerCase()}.jpg`);
    }
    setScanStep(null);
    stopCamera();

    try {
      const res = await authAPI.loginFace(fd);
      const { access, role, full_name } = res.data;
      dispatch(loginSuccess({ access, user: { full_name, cnic: cnic.trim() }, role }));
      if (role === 'CITIZEN')              navigate('/citizen/dashboard');
      else if (role === 'POLICE_STAFF')    navigate('/staff/dashboard');
      else if (role === 'POLICE_AUTHORITY')navigate('/authority/dashboard');
      else navigate('/admin/dashboard');
    } catch (err) {
      setError(err.response?.data?.error || 'Face verification failed.');
    } finally {
      setLoading(false);
    }
  };

  // cleanup camera
  useEffect(() => () => stopCamera(), []);

  const handleLogin = async (e) => {
    e.preventDefault();
    setError('');
    const cleanCnic = cnic.trim();
    const cleanPassword = password.trim();
    if (!cleanCnic || !cleanPassword) { setError('Please fill in all fields.'); return; }
    setLoading(true);
    try {
      const res = await authAPI.login({ cnic: cleanCnic, password: cleanPassword });
      console.log('OTP (dev):', res.data.otp_code);
      setOtpMode(true);
    } catch (err) {
      const msg = err.response?.data?.error || 'Login failed. Please check your credentials.';
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const handleOtp = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const otpCode = otp.join('');
      const res = await authAPI.verifyOtp({ cnic, otp_code: otpCode });
      const { access, role, full_name } = res.data;
      dispatch(loginSuccess({ access, user: { full_name, cnic }, role }));
      if (role === 'CITIZEN')              navigate('/citizen/dashboard');
      else if (role === 'POLICE_STAFF')    navigate('/staff/dashboard');
      else if (role === 'POLICE_AUTHORITY')navigate('/authority/dashboard');
      else navigate('/admin/dashboard');
    } catch {
      setError('Invalid or expired OTP. Try 123456 for testing.');
    } finally {
      setLoading(false);
    }
  };

  const handleOtpChange = (val, idx) => {
    const arr = [...otp];
    arr[idx] = val.slice(-1);
    setOtp(arr);
    if (val && idx < 5) document.getElementById(`otp-${idx+1}`)?.focus();
  };

  return (
    <div className="page-bg grid-overlay min-h-screen flex items-center justify-center px-4">
      <div className={`absolute top-1/3 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] ${config.glow} rounded-full blur-3xl pointer-events-none`} />
      <div className="w-full max-w-md relative z-10">
        <div className="text-center mb-8">
          <Link to="/" className="inline-flex flex-col items-center gap-2">
            <div className={`w-14 h-14 rounded-2xl bg-gradient-to-br ${config.grad} flex items-center justify-center ${config.shadow} animate-glow`}>
              <RoleIcon size={30} className="text-white" />
            </div>
            <h1 className="font-display text-2xl font-bold text-white">PakVerify</h1>
            <p className="text-white/40 text-sm">AI Police Verification System</p>
          </Link>
        </div>

        <div className="glass-card gradient-border p-8">
          {!otpMode && !faceMode ? (
            <>
              <h2 className="text-white font-display font-bold text-xl mb-1 text-center">{config.title}</h2>
              <p className="text-white/40 text-sm text-center mb-6">{config.subtitle}</p>
              {error && (
                <div className="flex items-center gap-2 px-4 py-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-sm mb-4">
                  <AlertCircle size={16} />{error}
                </div>
              )}
              <form onSubmit={handleLogin} className="flex flex-col gap-4">
                <div>
                  <label className="label-text">CNIC / Email</label>
                  <input className="input-field font-mono" placeholder="CNIC (35202-XXXXXXX-X) or Email"
                    value={cnic} onChange={e => setCnic(e.target.value.includes('@') ? e.target.value : formatCnic(e.target.value))} />
                </div>
                <div>
                  <label className="label-text">Password</label>
                  <div className="relative">
                    <input type={showPwd ? 'text' : 'password'} className="input-field pr-12"
                      placeholder="Enter your password" value={password} onChange={e => setPassword(e.target.value)} />
                    <button type="button" onClick={() => setShowPwd(!showPwd)}
                      className="absolute right-4 top-1/2 -translate-y-1/2 text-white/40 hover:text-white">
                      {showPwd ? <EyeOff size={18} /> : <Eye size={18} />}
                    </button>
                  </div>
                </div>
                <div className="flex justify-end">
                   <Link to="/forgot-password" className={`${config.theme} text-sm hover:underline`}>Forgot Password?</Link>
                </div>
                <button type="submit" disabled={loading}
                  className={`btn-primary flex items-center justify-center gap-2 w-full mt-2 !bg-gradient-to-r ${config.grad} border-none`}>
                  {loading
                    ? <svg className="animate-spin h-5 w-5 text-white" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/></svg>
                    : <>Sign In <ChevronRight size={18} /></>}
                </button>
                {roleType === 'citizen' && (
                  <button type="button" onClick={() => setFaceMode(true)}
                    className="w-full mt-2 py-3 rounded-xl border border-cyan-500/30 text-cyan-400 font-bold flex items-center justify-center gap-2 hover:bg-cyan-500/10 transition">
                    <User size={18} /> Login with Face ID
                  </button>
                )}
              </form>
              {roleType === 'citizen' && (
                <div className="mt-6 text-center text-sm text-white/40">
                  Don't have an account?{' '}
                  <Link to="/register" className={`${config.theme} font-semibold hover:underline`}>Register Now</Link>
                </div>
              )}
            </>
          ) : faceMode ? (
            <>
              <div className="text-center mb-6">
                <div className={`w-14 h-14 rounded-2xl bg-gradient-to-br from-teal-500 to-emerald-600 flex items-center justify-center mx-auto mb-3 shadow-glow-emerald`}>
                  <User size={28} className="text-white" />
                </div>
                <h2 className="text-white font-display font-bold text-xl">Face ID Login</h2>
                <p className="text-white/40 text-sm mt-1">Look straight, then turn your head left and right when asked</p>
              </div>
              
              {error && (
                <div className="flex items-center gap-2 px-4 py-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-sm mb-4">
                  <AlertCircle size={16} />{error}
                </div>
              )}

              <div className="flex flex-col gap-4">
                <div>
                  <label className="label-text">Enter your CNIC</label>
                  <input className="input-field font-mono" placeholder="CNIC (35202-XXXXXXX-X)"
                    value={cnic} onChange={e => setCnic(formatCnic(e.target.value))} />
                </div>
                
                {cameraActive ? (
                  <div className="relative rounded-xl overflow-hidden border-2 border-emerald-500/50">
                    <video ref={videoRef} autoPlay muted playsInline className="w-full transform -scale-x-100" />
                    {scanStep && (
                      <div className="absolute top-0 inset-x-0 flex items-center gap-3 px-4 py-2 bg-slate-900/85 text-white">
                        <span className="text-2xl font-black leading-none">{STEP_ARROW[scanStep.name]}</span>
                        <div>
                          <div className="text-[10px] tracking-widest text-white/60">STEP {scanStep.index} OF {scanStep.total} · LIVENESS</div>
                          <div className="text-sm font-bold">{scanStep.text}</div>
                        </div>
                      </div>
                    )}
                    {countdown !== null && (
                      <div className="absolute inset-0 flex items-center justify-center bg-black/30 pointer-events-none">
                        <span className="text-7xl font-black text-white drop-shadow-lg">{countdown}</span>
                      </div>
                    )}
                    {!scanStep && (
                      <button type="button" onClick={handleFaceLogin} disabled={loading}
                        className="absolute bottom-4 left-1/2 -translate-x-1/2 px-6 py-2 bg-emerald-500 hover:bg-emerald-400 text-slate-900 font-bold rounded-full shadow-lg">
                        {loading ? 'Verifying...' : 'Start Face Scan'}
                      </button>
                    )}
                  </div>
                ) : (
                  <button type="button" onClick={startCamera}
                    className="w-full py-12 rounded-xl border-2 border-dashed border-emerald-500/30 text-emerald-400 flex flex-col items-center justify-center gap-3 hover:bg-emerald-500/10 transition">
                    <User size={32} />
                    <span>Open Camera</span>
                  </button>
                )}

                <button type="button" onClick={() => { setFaceMode(false); stopCamera(); setError(''); }} className="text-white/40 text-sm hover:text-white transition mt-2">
                  ← Use Password Instead
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="text-center mb-6">
                <div className={`w-14 h-14 rounded-2xl bg-gradient-to-br ${config.grad} flex items-center justify-center mx-auto mb-3 ${config.shadow}`}>
                  <RoleIcon size={28} className="text-white" />
                </div>
                <h2 className="text-white font-display font-bold text-xl">OTP Verification</h2>
                <p className="text-white/40 text-sm mt-1">Enter the 6-digit code sent to your phone</p>
                <p className="text-cyan-400/60 text-xs mt-1">(Dev mode: use 123456 to bypass)</p>
              </div>
              {error && (
                <div className="flex items-center gap-2 px-4 py-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-sm mb-4">
                  <AlertCircle size={16} />{error}
                </div>
              )}
              <form onSubmit={handleOtp} className="flex flex-col gap-6">
                <div className="flex gap-2 justify-center">
                  {otp.map((v, i) => (
                    <input key={i} id={`otp-${i}`} maxLength={1} value={v}
                      onChange={e => handleOtpChange(e.target.value, i)}
                      className={`w-12 h-12 rounded-xl bg-white/5 border border-white/10 text-white text-center text-xl font-bold focus:outline-none ${config.border} transition font-mono`} />
                  ))}
                </div>
                <button type="submit" disabled={loading}
                  className={`btn-primary flex items-center justify-center gap-2 w-full !bg-gradient-to-r ${config.grad} border-none`}>
                  {loading
                    ? <svg className="animate-spin h-5 w-5 text-white" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/></svg>
                    : 'Verify & Sign In'}
                </button>
                <button type="button" onClick={() => setOtpMode(false)} className="text-white/40 text-sm hover:text-white transition">← Back to Login</button>
              </form>
            </>
          )}
        </div>
        <p className="text-center text-white/20 text-xs mt-6">🔒 Secured by 256-bit encryption · Government of Pakistan</p>
      </div>
    </div>
  );
}
