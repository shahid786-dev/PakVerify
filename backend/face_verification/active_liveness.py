"""
face_verification/active_liveness.py
=====================================
Challenge-response (active) liveness for the live face capture.

Why this exists
---------------
A single still frame cannot tell a living person from a printed photo or a
phone screen held up to the webcam: both show a face with open eyes. The old
check scored one frame only, so a photo passed.

How it works
------------
1. The server issues a short, signed challenge: CENTER first, then LEFT and
   RIGHT head turns in a random order. The challenge is bound to the user and
   expires after CHALLENGE_TTL_SECONDS. It is stateless (django.core.signing),
   so it works on serverless hosts where requests land on different instances.
2. The browser shows each instruction and captures one frame per step.
3. For every frame the face detector (SCRFD, the same model used for matching)
   returns 5 keypoints: both eyes, nose tip, both mouth corners.
4. Two geometric tests decide if the frames come from a real 3-D head:

   a) Yaw ratio: horizontal offset of the nose from the mid-point between the
      eyes, divided by the eye distance. ~0 when facing the camera, clearly
      positive / negative when the head turns.

   b) Planar parallax (the anti-photo test): eyes and mouth corners lie close
      to one plane. A flat photo or screen is entirely planar, so the
      transform that maps those 4 points from the CENTER frame to a turned
      frame also predicts the nose position exactly. On a real head the nose
      tip sticks out of that plane, so when the head turns it lands away from
      the predicted spot. The size of that miss (relative to the eye distance)
      is the parallax. Tilting, bending or moving a photo does not create it.

5. All frames must contain exactly one face and the same person (ArcFace
   similarity between the CENTER frame and each turned frame).

Limits
------
This stops printed photos, photos on a screen and uploaded still images.
A replayed video of the real person turning their head on cue could still
pass; defeating that needs depth / IR cameras or a trained anti-spoofing
model, which is listed as future work.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from typing import Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger('face_verification')

SIGNING_SALT = 'pakverify.face.active-liveness.v1'
CHALLENGE_TTL_SECONDS = 180
TURN_STEPS = ('LEFT', 'RIGHT')
ALL_STEPS = ('CENTER',) + TURN_STEPS

# Thresholds (ratios of the eye distance), tuned by simulation (see
# ActiveLivenessGeometryTests in backend/tests.py): a synthetic 3-D head turned ±25° passes ~80% of the
# time (±30°: ~95%), while a flat photo tilted up to 60° on any axis, near or
# far from the lens, with 2 px keypoint noise passes < 0.01% of attempts.
CENTER_MAX_YAW = 0.18        # CENTER frame must be roughly frontal
TURN_MIN_YAW = 0.08          # each turned frame must show a visible turn
TURN_MIN_SPREAD = 0.22       # yaw(LEFT) and yaw(RIGHT) must differ by this much
MIN_PARALLAX = 0.05          # out-of-plane nose shift needed in each turned frame
MIN_PARALLAX_SPREAD = 0.17   # parallax(LEFT) vs parallax(RIGHT); photos max out ~0.155
SAME_PERSON_MIN = 0.25       # ArcFace cosine, CENTER vs turned frame (turns lower it)
MAX_FRAME_BYTES = 5 * 1024 * 1024


class ActiveLivenessError(Exception):
    """Raised for malformed requests (bad / expired token, missing frames)."""


# ─────────────────────────────────────────────────────────────────────────────
# Challenge issue / validation
# ─────────────────────────────────────────────────────────────────────────────

def issue_challenge(user) -> dict:
    """Create a signed challenge for this user: CENTER, then LEFT/RIGHT shuffled."""
    from django.core import signing

    turns = list(TURN_STEPS)
    secrets.SystemRandom().shuffle(turns)
    steps = ['CENTER'] + turns
    payload = {
        'u': str(user.pk),
        's': steps,
        'n': secrets.token_urlsafe(12),
        't': int(time.time()),
    }
    token = signing.dumps(payload, salt=SIGNING_SALT, compress=True)
    return {
        'challenge_token': token,
        'steps': steps,
        'expires_in': CHALLENGE_TTL_SECONDS,
        'instructions': {
            'CENTER': 'Look straight at the camera',
            'LEFT': 'Slowly turn your head to your LEFT',
            'RIGHT': 'Slowly turn your head to your RIGHT',
        },
    }


def read_challenge(token: str, user) -> dict:
    """Validate signature, expiry and owner. Returns the payload."""
    from django.core import signing

    if not token:
        raise ActiveLivenessError(
            'Liveness challenge missing. Please restart the face scan and follow the head-turn steps.'
        )
    try:
        payload = signing.loads(token, salt=SIGNING_SALT, max_age=CHALLENGE_TTL_SECONDS)
    except signing.SignatureExpired:
        raise ActiveLivenessError('Liveness challenge expired. Please start the face scan again.')
    except signing.BadSignature:
        raise ActiveLivenessError('Invalid liveness challenge. Please start the face scan again.')
    if str(payload.get('u')) != str(user.pk):
        raise ActiveLivenessError('This liveness challenge belongs to another user.')
    steps = payload.get('s') or []
    if sorted(steps) != sorted(ALL_STEPS) or steps[0] != 'CENTER':
        raise ActiveLivenessError('Invalid liveness challenge.')
    return payload


def collect_frames(request, steps: Sequence[str]) -> Dict[str, bytes]:
    """Read frame_<STEP> uploads. The CENTER frame may also arrive as live_image."""
    frames: Dict[str, bytes] = {}
    for step in steps:
        f = request.FILES.get(f'frame_{step}')
        if f is None and step == 'CENTER':
            f = request.FILES.get('live_image')
        if f is None:
            raise ActiveLivenessError(
                f'Missing the "{step.lower()}" capture. Please complete every step of the face scan.'
            )
        if f.size > MAX_FRAME_BYTES:
            raise ActiveLivenessError('A captured frame is larger than 5 MB.')
        data = f.read()
        f.seek(0)
        frames[step] = data

    digests = {hashlib.sha256(b).hexdigest() for b in frames.values()}
    if len(digests) != len(frames):
        raise ActiveLivenessError(
            'The same image was submitted for different steps. Please capture each step live.'
        )
    return frames


# ─────────────────────────────────────────────────────────────────────────────
# Geometry (pure numpy, unit-testable without models)
# kps order (SCRFD / InsightFace): 0 eye (image-left), 1 eye (image-right),
#                                  2 nose tip, 3 mouth (image-left), 4 mouth (image-right)
# ─────────────────────────────────────────────────────────────────────────────

def _eye_frame(kps: np.ndarray):
    eye_mid = (kps[0] + kps[1]) / 2.0
    axis = kps[1] - kps[0]
    dist = float(np.linalg.norm(axis))
    if dist < 1e-6:
        raise ValueError('degenerate keypoints')
    return eye_mid, axis / dist, dist


def yaw_ratio(kps) -> float:
    """Nose offset along the eye axis / eye distance. Roll-invariant."""
    kps = np.asarray(kps, dtype=np.float64)
    eye_mid, ux, dist = _eye_frame(kps)
    return float(np.dot(kps[2] - eye_mid, ux) / dist)


def planar_parallax(kps_ref, kps_turn) -> float:
    """
    Fit an affine map from the reference frame's eyes + mouth corners to the
    turned frame's, predict the nose, and return the signed miss along the
    turned frame's eye axis, divided by its eye distance.
    ~0 for any flat image (photo / screen); clearly non-zero for a real head.
    """
    a = np.asarray(kps_ref, dtype=np.float64)
    b = np.asarray(kps_turn, dtype=np.float64)
    idx = [0, 1, 3, 4]
    src = np.hstack([a[idx], np.ones((4, 1))])           # 4 x 3
    coef, *_ = np.linalg.lstsq(src, b[idx], rcond=None)  # 3 x 2
    predicted_nose = np.append(a[2], 1.0) @ coef
    _, ux, dist = _eye_frame(b)
    return float(np.dot(b[2] - predicted_nose, ux) / dist)


def _cosine(e1, e2) -> float:
    e1 = np.asarray(e1, dtype=np.float64)
    e2 = np.asarray(e2, dtype=np.float64)
    return float(np.dot(e1, e2) / (np.linalg.norm(e1) * np.linalg.norm(e2) + 1e-12))


def evaluate_geometry(center_kps, left_kps, right_kps) -> dict:
    """Decide liveness from the three keypoint sets. Returns a result dict."""
    y_c = yaw_ratio(center_kps)
    y_l = yaw_ratio(left_kps)
    y_r = yaw_ratio(right_kps)
    p_l = planar_parallax(center_kps, left_kps)
    p_r = planar_parallax(center_kps, right_kps)

    metrics = {
        'yaw_center': round(y_c, 3), 'yaw_left': round(y_l, 3), 'yaw_right': round(y_r, 3),
        'parallax_left': round(p_l, 3), 'parallax_right': round(p_r, 3),
    }

    def fail(code, msg):
        return {'passed': False, 'reason': code, 'message': msg, 'metrics': metrics, 'score': 0.0}

    if abs(y_c) > CENTER_MAX_YAW:
        return fail('NOT_FRONTAL', 'For the first step, look straight at the camera.')
    if abs(y_l) < TURN_MIN_YAW or abs(y_r) < TURN_MIN_YAW:
        return fail('NO_TURN', 'Head turn not detected. Turn your head a little more when asked.')
    if np.sign(y_l) == np.sign(y_r) or abs(y_l - y_r) < TURN_MIN_SPREAD:
        return fail('TURN_NOT_BOTH_SIDES', 'Turn your head to one side and then to the other side, as shown.')
    # Real 3-D nose: the out-of-plane shift follows the turn direction.
    if (abs(p_l) < MIN_PARALLAX or abs(p_r) < MIN_PARALLAX
            or abs(p_l - p_r) < MIN_PARALLAX_SPREAD
            or np.sign(p_l) != np.sign(y_l) or np.sign(p_r) != np.sign(y_r)):
        return fail('FLAT_IMAGE', 'Spoof suspected: the face moved like a flat photo or screen, not a real head.')

    spread = abs(p_l - p_r)
    score = float(min(1.0, 0.70 + (spread - MIN_PARALLAX_SPREAD) * 1.2))
    return {'passed': True, 'reason': 'LIVE', 'message': 'Live person confirmed.', 'metrics': metrics,
            'score': round(score, 3)}


# ─────────────────────────────────────────────────────────────────────────────
# Detection
# ─────────────────────────────────────────────────────────────────────────────

def _detect_single_face(image_bytes: bytes):
    """Return (kps 5x2, embedding) for exactly one face, else raise ActiveLivenessError."""
    from .face_processor import _bytes_to_bgr, _get_face_app

    bgr = _bytes_to_bgr(image_bytes)
    app = _get_face_app()
    if app is not None:
        faces = app.get(bgr)
    else:
        from . import onnx_arcface
        if not onnx_arcface.available():
            raise RuntimeError('No keypoint face detector is available on this server.')
        faces = onnx_arcface.get_faces(bgr)

    if len(faces) == 0:
        raise ActiveLivenessError('No face found in one of the steps. Keep your face inside the oval.')
    if len(faces) > 1:
        raise ActiveLivenessError('More than one face found. Please verify alone.')
    face = faces[0]
    kps = getattr(face, 'kps', None)
    if kps is None:
        raise RuntimeError('Face detector did not return keypoints.')
    return np.asarray(kps, dtype=np.float64), np.asarray(face.embedding, dtype=np.float64)


def verify_frames(frames: Dict[str, bytes]) -> dict:
    """Run detection on each frame and evaluate. Returns a result dict."""
    detected = {}
    for step in ALL_STEPS:
        detected[step] = _detect_single_face(frames[step])

    same = {
        step: _cosine(detected['CENTER'][1], detected[step][1]) for step in TURN_STEPS
    }
    result = evaluate_geometry(detected['CENTER'][0], detected['LEFT'][0], detected['RIGHT'][0])
    result['metrics'].update({f'same_person_{k.lower()}': round(v, 3) for k, v in same.items()})
    if result['passed'] and min(same.values()) < SAME_PERSON_MIN:
        result.update(passed=False, reason='DIFFERENT_FACES', score=0.0,
                      message='The face changed between steps. Please complete the scan yourself.')
    return result


def run_active_liveness(request, user) -> dict:
    """
    Full check for a verification request. Returns
    {'verified', 'liveness_score', 'anti_spoofing', 'reason', 'message', 'metrics', 'center_bytes'}.
    Raises ActiveLivenessError for malformed requests.
    """
    payload = read_challenge(request.data.get('challenge_token', ''), user)
    frames = collect_frames(request, payload['s'])
    try:
        result = verify_frames(frames)
    except ActiveLivenessError:
        raise
    except Exception as exc:  # detector failure → treat as service error upstream
        logger.exception('Active liveness detector error: %s', exc)
        raise
    logger.info('Active liveness user=%s passed=%s reason=%s metrics=%s',
                getattr(user, 'cnic', user.pk), result['passed'], result['reason'], result['metrics'])
    return {
        'verified': result['passed'],
        'liveness_score': result['score'],
        'anti_spoofing': 'REAL' if result['passed'] else 'SPOOF_SUSPECTED',
        'reason': result['reason'],
        'message': result['message'],
        'metrics': result['metrics'],
        'center_bytes': frames['CENTER'],
    }
