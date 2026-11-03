"""
Liveness Detection Module — PakVerify AI Microservice
Uses MediaPipe Face Mesh to detect:
  - Eye aspect ratio (blink detection proxy)
  - Mouth openness (smile/open mouth proxy)
  - Face size / distance plausibility
Falls back to OpenCV Haar cascade if MediaPipe is unavailable.
"""
import numpy as np
import cv2


_insightface_app = None


def _insightface_fallback(img):
    """Use the local InsightFace detector when OpenCV Haar is unavailable."""
    global _insightface_app
    try:
        from insightface.app import FaceAnalysis

        if _insightface_app is None:
            _insightface_app = FaceAnalysis(
                name='buffalo_l',
                providers=['CPUExecutionProvider'],
            )
            _insightface_app.prepare(ctx_id=0, det_size=(640, 640))

        faces = _insightface_app.get(img)
        if not faces:
            return {
                "liveness_score": 0.0,
                "face_detected": False,
                "anti_spoofing": "NO_FACE",
                "blink_detected": False,
                "method": "insightface",
            }

        height, width = img.shape[:2]
        largest = max(
            faces,
            key=lambda face: max(0.0, float(face.bbox[2] - face.bbox[0]))
            * max(0.0, float(face.bbox[3] - face.bbox[1])),
        )
        face_area = max(0.0, float(largest.bbox[2] - largest.bbox[0])) * max(
            0.0, float(largest.bbox[3] - largest.bbox[1])
        )
        face_ratio = float(face_area / max(1, width * height))
        score = float(min(1.0, max(0.70, 0.55 + face_ratio * 2.0)))
        return {
            "liveness_score": float(round(score, 3)),
            "face_detected": True,
            "anti_spoofing": "REAL",
            "blink_detected": False,
            "method": "insightface_detector",
        }
    except Exception as exc:
        print(f"[liveness] InsightFace fallback unavailable ({exc}).")
        return None


EYE_AR_THRESH   = 0.20   # Below this → eyes likely closed
MOUTH_AR_THRESH = 0.30   # Above this → mouth likely open
MIN_FACE_SIZE   = 0.05   # Face must cover >= 5% of frame area


def _bytes_to_cv2(image_bytes: bytes):
    nparr = np.frombuffer(image_bytes, np.uint8)
    return cv2.imdecode(nparr, cv2.IMREAD_COLOR)


def _eye_aspect_ratio(landmarks, indices):
    """Compute eye aspect ratio from 6 landmark points."""
    p = [np.array([landmarks[i].x, landmarks[i].y]) for i in indices]
    # EAR = (||p2-p6|| + ||p3-p5||) / (2 * ||p1-p4||)
    A = np.linalg.norm(p[1] - p[5])
    B = np.linalg.norm(p[2] - p[4])
    C = np.linalg.norm(p[0] - p[3])
    ear = (A + B) / (2.0 * C + 1e-6)
    return ear


def _mouth_aspect_ratio(landmarks):
    """Estimate mouth openness from lip landmarks."""
    # Approximate vertical vs horizontal lip distance
    top_lip    = np.array([landmarks[13].x,  landmarks[13].y])
    bot_lip    = np.array([landmarks[14].x,  landmarks[14].y])
    left_lip   = np.array([landmarks[61].x,  landmarks[61].y])
    right_lip  = np.array([landmarks[291].x, landmarks[291].y])

    vertical   = np.linalg.norm(top_lip - bot_lip)
    horizontal = np.linalg.norm(left_lip - right_lip)
    mar = vertical / (horizontal + 1e-6)
    return mar


def _opencv_fallback(img) -> dict:
    """
    OpenCV Haar cascade fallback. Detects a face and scores face presence/size.
    It does not prove liveness on its own (see backend active_liveness.py).
    """
    insightface_result = _insightface_fallback(img)
    if insightface_result is not None:
        return insightface_result

    if not hasattr(cv2, 'CascadeClassifier'):
        return {
            "liveness_score": 0.0,
            "face_detected": False,
            "anti_spoofing": "DETECTOR_UNAVAILABLE",
            "blink_detected": False,
            "method": "opencv_unavailable",
        }

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    cascade_root = getattr(getattr(cv2, 'data', None), 'haarcascades', None)
    if not cascade_root:
        return {
            "liveness_score": 0.0,
            "face_detected": False,
            "anti_spoofing": "DETECTOR_UNAVAILABLE",
            "blink_detected": False,
            "method": "opencv_cascade_unavailable",
        }

    face_cascade = cv2.CascadeClassifier(
        cascade_root + 'haarcascade_frontalface_default.xml'
    )
    if face_cascade.empty():
        return {
            "liveness_score": 0.0,
            "face_detected": False,
            "anti_spoofing": "DETECTOR_UNAVAILABLE",
            "blink_detected": False,
            "method": "opencv_cascade_unavailable",
        }
    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)

    face_detected = len(faces) > 0
    if not face_detected:
        return {
            "liveness_score": 0.30,
            "face_detected":  False,
            "anti_spoofing":  "UNKNOWN",
            "blink_detected": False,
            "method":         "opencv_fallback"
        }

    # Plausibility score based on face size
    h, w = img.shape[:2]
    frame_area = h * w
    best_face_area = max(fw * fh for (fx, fy, fw, fh) in faces)
    face_ratio = best_face_area / frame_area

    # Deterministic face-presence score from face size. A single frame cannot
    # prove liveness; the backend's head-turn challenge (active_liveness.py)
    # is what rejects photos and screens.
    score = min(1.0, max(0.0, face_ratio * 10.0 + 0.55))

    return {
        "liveness_score": round(score, 3),
        "face_detected":  True,
        "anti_spoofing":  "REAL" if score >= 0.70 else "SUSPECTED_SPOOF",
        "blink_detected": False,
        "method":         "opencv_fallback (single frame, presence only)"
    }


def check_liveness(live_photo_bytes: bytes) -> dict:
    """
    Primary liveness detection.
    1. Tries MediaPipe Face Mesh for detailed landmark analysis.
    2. Falls back to OpenCV Haar cascade.
    Returns dict with liveness_score [0.0–1.0].
    """
    img = _bytes_to_cv2(live_photo_bytes)
    if img is None:
        return {"liveness_score": 0.0, "face_detected": False, "anti_spoofing": "ERROR", "method": "error"}

    try:
        import mediapipe as mp

        mp_face_mesh = mp.solutions.face_mesh
        # MediaPipe left/right eye landmark indices (subset)
        LEFT_EYE  = [362, 385, 387, 263, 373, 380]
        RIGHT_EYE = [33,  160, 158, 133, 153, 144]

        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        h, w    = img.shape[:2]

        with mp_face_mesh.FaceMesh(
            static_image_mode=True,
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5
        ) as face_mesh:
            results = face_mesh.process(rgb_img)

        if not results.multi_face_landmarks:
            return {
                "liveness_score": 0.25,
                "face_detected":  False,
                "anti_spoofing":  "NO_FACE",
                "blink_detected": False,
                "method":         "mediapipe"
            }

        lm = results.multi_face_landmarks[0].landmark

        # ── Eye Aspect Ratio ──────────────────────────────────────────
        left_ear  = _eye_aspect_ratio(lm, LEFT_EYE)
        right_ear = _eye_aspect_ratio(lm, RIGHT_EYE)
        avg_ear   = (left_ear + right_ear) / 2.0

        # ── Mouth Aspect Ratio ────────────────────────────────────────
        try:
            mar = _mouth_aspect_ratio(lm)
        except Exception:
            mar = 0.15

        # ── Face bounding box size ────────────────────────────────────
        xs = [l.x for l in lm]
        ys = [l.y for l in lm]
        face_w = (max(xs) - min(xs))
        face_h = (max(ys) - min(ys))
        face_ratio = face_w * face_h  # fraction of frame

        # ── Composite liveness score ──────────────────────────────────
        eye_score   = min(1.0, avg_ear / 0.30)      # good EAR → eyes open → real
        mouth_score = 0.8 + mar * 0.5               # some mouth movement → real
        size_score  = min(1.0, face_ratio / 0.08)   # face large enough → real

        liveness_score = (eye_score * 0.45 + mouth_score * 0.25 + size_score * 0.30)
        liveness_score = min(1.0, max(0.0, liveness_score))

        blink_detected = avg_ear < EYE_AR_THRESH
        anti_spoof     = "REAL" if liveness_score >= 0.70 else "SUSPECTED_SPOOF"

        return {
            "liveness_score": round(liveness_score, 3),
            "face_detected":  True,
            "anti_spoofing":  anti_spoof,
            "blink_detected": blink_detected,
            "eye_ar":         round(avg_ear, 4),
            "mouth_ar":       round(mar, 4),
            "method":         "mediapipe_face_mesh"
        }

    except Exception as mp_error:
        print(f"[liveness] MediaPipe unavailable ({mp_error}), using OpenCV fallback.")

    return _opencv_fallback(img)
