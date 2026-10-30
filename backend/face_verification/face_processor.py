"""
face_verification/face_processor.py
=====================================
Low-level face detection, quality assessment, and embedding extraction.

Model: InsightFace ArcFace (buffalo_l) — pre-trained, no training required.
Fallback: DeepFace (if InsightFace unavailable).

All public functions return standard Python types (not framework objects)
so they can be called from anywhere in the codebase without side effects.
"""

import io
import logging
from typing import Optional, Tuple

import cv2
import numpy as np
from PIL import Image

from .exceptions import (
    NoFaceDetectedError,
    MultipleFacesDetectedError,
    CorruptedImageError,
    PoorImageQualityError,
)

logger = logging.getLogger('face_verification')

# ─────────────────────────────────────────────────────────────────────────────
# Model loading (lazy, thread-safe via module-level singleton)
# ─────────────────────────────────────────────────────────────────────────────

_face_app = None  # InsightFace application
_MODEL_NAME = 'buffalo_l'

# Quality thresholds — tuned for live webcam captures
# Webcam feeds in varied lighting are more forgiving than studio shots.
MIN_FACE_SIZE = 40        # pixels — smaller faces are rejected (was 60)
MIN_SHARPNESS = 30.0      # Laplacian variance (was 50.0) — webcam often slightly soft
MIN_BRIGHTNESS = 30       # mean pixel value 0–255 (was 40) — lower for dim environments
MAX_BRIGHTNESS = 240      # (was 230) — allow slightly overexposed frames


def _get_face_app():
    """
    Lazy-load InsightFace model. Thread-safe by Python GIL for simple assignment.
    Returns None if InsightFace is not installed (triggers DeepFace fallback).
    """
    global _face_app
    if _face_app is not None:
        return _face_app
    try:
        import insightface
        app = insightface.app.FaceAnalysis(name=_MODEL_NAME, providers=['CPUExecutionProvider'])
        app.prepare(ctx_id=0, det_size=(640, 640))
        _face_app = app
        logger.info('InsightFace (%s) loaded successfully.', _MODEL_NAME)
    except ImportError:
        logger.warning('InsightFace not installed — falling back to DeepFace.')
        _face_app = None
    except Exception as exc:
        logger.error('Failed to load InsightFace: %s', exc)
        _face_app = None
    return _face_app


# ─────────────────────────────────────────────────────────────────────────────
# Image loading helpers
# ─────────────────────────────────────────────────────────────────────────────

def _bytes_to_bgr(image_bytes: bytes) -> np.ndarray:
    """
    Decode raw image bytes to a BGR NumPy array (OpenCV format).

    Raises
    ------
    CorruptedImageError if the bytes cannot be decoded.
    """
    try:
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError('cv2.imdecode returned None')
        return img
    except Exception as exc:
        raise CorruptedImageError(
            f'Failed to decode image bytes: {exc}'
        ) from exc


def _path_to_bgr(image_path: str) -> np.ndarray:
    """
    Load an image from disk to BGR NumPy array.

    Raises
    ------
    ImageNotFoundError or CorruptedImageError.
    """
    import os
    from .exceptions import ImageNotFoundError

    if not os.path.exists(image_path):
        raise ImageNotFoundError(f'Image file not found: {image_path}')

    try:
        img = cv2.imread(image_path, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError('cv2.imread returned None')
        return img
    except Exception as exc:
        raise CorruptedImageError(
            f'Cannot read image at {image_path}: {exc}'
        ) from exc


# ─────────────────────────────────────────────────────────────────────────────
# Quality assessment
# ─────────────────────────────────────────────────────────────────────────────

def _assess_quality(bgr_image: np.ndarray) -> Tuple[bool, str, dict]:
    """
    Assess image quality via sharpness (Laplacian) and brightness.

    Returns
    -------
    (passed: bool, reason: str, metrics: dict)
    """
    gray = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2GRAY)

    # Sharpness via Laplacian variance
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    # Brightness via mean pixel value
    brightness = float(gray.mean())

    metrics = {'sharpness': round(sharpness, 2), 'brightness': round(brightness, 2)}

    if sharpness < MIN_SHARPNESS:
        return False, (
            f'Image is too blurry (sharpness={sharpness:.1f}, '
            f'minimum required={MIN_SHARPNESS}). '
            'Please capture a clearer photo.'
        ), metrics

    if brightness < MIN_BRIGHTNESS:
        return False, (
            f'Image is too dark (brightness={brightness:.1f}). '
            'Improve lighting and try again.'
        ), metrics

    if brightness > MAX_BRIGHTNESS:
        return False, (
            f'Image is overexposed (brightness={brightness:.1f}). '
            'Reduce lighting intensity.'
        ), metrics

    return True, 'Quality OK', metrics


# ─────────────────────────────────────────────────────────────────────────────
# InsightFace-based extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_insightface(bgr_image: np.ndarray) -> np.ndarray:
    """
    Detect face and extract 512-dim ArcFace embedding via InsightFace.

    Returns
    -------
    embedding : np.ndarray, shape (512,), dtype float32

    Raises
    ------
    NoFaceDetectedError, MultipleFacesDetectedError, PoorImageQualityError
    """
    app = _get_face_app()
    if app is None:
        raise ImportError('InsightFace not available')

    # InsightFace expects BGR uint8 — which is what we already have
    faces = app.get(bgr_image)

    if len(faces) == 0:
        raise NoFaceDetectedError()

    if len(faces) > 1:
        raise MultipleFacesDetectedError(
            f'{len(faces)} faces detected in the image. Only one face is allowed.'
        )

    face = faces[0]

    # Validate face bounding box size
    x1, y1, x2, y2 = face.bbox.astype(int)
    face_w, face_h = x2 - x1, y2 - y1
    if face_w < MIN_FACE_SIZE or face_h < MIN_FACE_SIZE:
        raise PoorImageQualityError(
            f'Detected face is too small ({face_w}×{face_h}px). '
            f'Move closer to the camera (minimum {MIN_FACE_SIZE}px).'
        )

    return face.embedding  # shape (512,)


# ─────────────────────────────────────────────────────────────────────────────
# Lightweight ONNX ArcFace extraction (same buffalo_l models, no insightface pkg)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_onnx_arcface(bgr_image: np.ndarray) -> np.ndarray:
    """
    Same detector + ArcFace model as InsightFace buffalo_l, run directly with
    onnxruntime (used where the full insightface package cannot be installed,
    e.g. Vercel). Raises ImportError when onnxruntime is not installed.
    """
    from . import onnx_arcface
    if not onnx_arcface.available():
        raise ImportError('onnxruntime not available')

    faces = onnx_arcface.get_faces(bgr_image)
    if len(faces) == 0:
        raise NoFaceDetectedError()
    if len(faces) > 1:
        raise MultipleFacesDetectedError(
            f'{len(faces)} faces detected in the image. Only one face is allowed.'
        )

    x1, y1, x2, y2 = faces[0].bbox.astype(int)
    face_w, face_h = x2 - x1, y2 - y1
    if face_w < MIN_FACE_SIZE or face_h < MIN_FACE_SIZE:
        raise PoorImageQualityError(
            f'Detected face is too small ({face_w}×{face_h}px). '
            f'Move closer to the camera (minimum {MIN_FACE_SIZE}px).'
        )
    return faces[0].embedding


def _extract_arcface_any(bgr_image: np.ndarray):
    """InsightFace package → ONNX ArcFace → DeepFace, returning (embedding, model_used)."""
    try:
        return _extract_insightface(bgr_image), 'InsightFace-ArcFace'
    except ImportError:
        pass
    try:
        return _extract_onnx_arcface(bgr_image), 'InsightFace-ArcFace (ONNX Runtime)'
    except ImportError:
        pass
    return _extract_deepface(bgr_image), 'DeepFace-Facenet512'


# ─────────────────────────────────────────────────────────────────────────────
# DeepFace fallback extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_deepface(bgr_image: np.ndarray) -> np.ndarray:
    """
    Fallback: Use DeepFace (Facenet512 model) to extract embeddings.

    Returns
    -------
    embedding : np.ndarray, shape (512,), dtype float32
    """
    try:
        from deepface import DeepFace
    except ImportError:
        raise ImportError(
            'Neither InsightFace nor DeepFace is installed. '
            'Run: pip install insightface  OR  pip install deepface tf-keras'
        )

    # Convert BGR → RGB for DeepFace
    rgb = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB)

    try:
        result = DeepFace.represent(
            img_path=rgb,
            model_name='Facenet512',
            enforce_detection=True,
            detector_backend='opencv',
        )
    except ValueError as exc:
        err_msg = str(exc).lower()
        if 'face could not be detected' in err_msg or 'no face' in err_msg:
            raise NoFaceDetectedError() from exc
        raise CorruptedImageError(str(exc)) from exc
    except Exception as exc:
        raise CorruptedImageError(f'DeepFace error: {exc}') from exc

    if not result:
        raise NoFaceDetectedError()

    if len(result) > 1:
        raise MultipleFacesDetectedError(
            f'{len(result)} faces detected. Only one face allowed.'
        )

    embedding = np.array(result[0]['embedding'], dtype=np.float32)
    return embedding


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def extract_embedding_from_bytes(
    image_bytes: bytes,
    check_quality: bool = True,
) -> Tuple[np.ndarray, dict]:
    """
    Full pipeline: decode → quality check → detect face → extract embedding.

    Parameters
    ----------
    image_bytes : raw bytes from a camera capture or uploaded file.
    check_quality : if True, apply sharpness/brightness gate.

    Returns
    -------
    (embedding: np.ndarray, metadata: dict)
        metadata contains quality_metrics, model_used, face_count.

    Raises
    ------
    CorruptedImageError, PoorImageQualityError,
    NoFaceDetectedError, MultipleFacesDetectedError
    """
    bgr = _bytes_to_bgr(image_bytes)

    quality_metrics = {}
    if check_quality:
        ok, reason, quality_metrics = _assess_quality(bgr)
        if not ok:
            raise PoorImageQualityError(reason)

    # InsightFace package → lightweight ONNX ArcFace → DeepFace
    embedding, model_used = _extract_arcface_any(bgr)

    metadata = {
        'model_used': model_used,
        'embedding_dim': len(embedding),
        'quality_metrics': quality_metrics,
    }
    logger.debug('Embedding extracted using %s, dim=%d', model_used, len(embedding))
    return embedding, metadata


def extract_embedding_from_path(
    image_path: str,
    check_quality: bool = False,
) -> Tuple[np.ndarray, dict]:
    """
    Extract embedding from an image file on disk.
    Quality check is OFF by default for dataset images (they may be ID-card crops).

    Returns
    -------
    (embedding: np.ndarray, metadata: dict)
    """
    bgr = _path_to_bgr(image_path)

    quality_metrics = {}
    if check_quality:
        ok, reason, quality_metrics = _assess_quality(bgr)
        if not ok:
            raise PoorImageQualityError(reason)

    embedding, model_used = _extract_arcface_any(bgr)

    metadata = {
        'model_used': model_used,
        'embedding_dim': len(embedding),
        'quality_metrics': quality_metrics,
    }
    return embedding, metadata


def get_model_name() -> str:
    """Return the name of the currently active face model."""
    app = _get_face_app()
    if app is not None:
        return f'InsightFace-{_MODEL_NAME}'
    from . import onnx_arcface
    if onnx_arcface.available():
        return f'InsightFace-{_MODEL_NAME} (ONNX Runtime)'
    try:
        from deepface import DeepFace  # noqa: F401
        return 'DeepFace-Facenet512'
    except ImportError:
        return 'Unknown (no model installed)'
