"""
Lightweight InsightFace "buffalo_l" pipeline using only onnxruntime + OpenCV.

The full ``insightface`` package (with scipy, scikit-image, albumentations…)
is far too large for Vercel Functions (500 MB limit). This module runs the
same two ONNX models InsightFace uses for verification:

  * det_10g.onnx   – SCRFD face detector (boxes + 5 landmarks)
  * w600k_r50.onnx – ArcFace R50 recogniser (512-d embedding)

Detection decoding, 5-point alignment (Umeyama similarity transform to the
ArcFace 112x112 template) and recogniser preprocessing follow InsightFace's
own implementation, so embeddings are compatible with the existing
``nadra_embeddings.pkl`` store (generated with InsightFace buffalo_l).

Models are loaded from FACE_MODEL_DIR (default: ~/.insightface/models/buffalo_l
if present, else /tmp/pakverify_models) and downloaded on first use from
FACE_MODEL_BASE_URL when missing.
"""

import logging
import os
import threading
import urllib.request

import cv2
import numpy as np

logger = logging.getLogger('face_verification')

DET_MODEL = 'det_10g.onnx'
REC_MODEL = 'w600k_r50.onnx'
DEFAULT_BASE_URL = 'https://huggingface.co/public-data/insightface/resolve/main/models/buffalo_l'

ARCFACE_DST = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
     [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)

_lock = threading.Lock()
_models = None


def _model_dir() -> str:
    configured = os.environ.get('FACE_MODEL_DIR')
    if configured:
        return configured
    home = os.path.expanduser('~/.insightface/models/buffalo_l')
    if os.path.exists(os.path.join(home, REC_MODEL)):
        return home
    return '/tmp/pakverify_models'


def _ensure_file(directory: str, name: str) -> str:
    path = os.path.join(directory, name)
    if os.path.exists(path) and os.path.getsize(path) > 1024:
        return path
    os.makedirs(directory, exist_ok=True)
    base = os.environ.get('FACE_MODEL_BASE_URL', DEFAULT_BASE_URL).rstrip('/')
    url = f'{base}/{name}'
    tmp = path + '.part'
    logger.info('Downloading face model %s', url)
    with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, 'wb') as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    os.replace(tmp, path)
    return path


def available() -> bool:
    try:
        import onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


class _SCRFD:
    def __init__(self, path, session_options):
        import onnxruntime as ort
        self.session = ort.InferenceSession(path, sess_options=session_options, providers=['CPUExecutionProvider'])
        self.input_name = self.session.get_inputs()[0].name
        self.output_names = [o.name for o in self.session.get_outputs()]
        n = len(self.output_names)
        # det_10g: 9 outputs -> 3 strides with keypoints
        self.fmc = 3
        self.strides = [8, 16, 32]
        self.num_anchors = 2
        self.use_kps = n == 9
        self.input_size = (640, 640)
        self._anchor_cache = {}

    def _anchors(self, h, w, stride):
        key = (h, w, stride)
        if key not in self._anchor_cache:
            centers = np.stack(np.mgrid[:h, :w][::-1], axis=-1).astype(np.float32)
            centers = (centers * stride).reshape((-1, 2))
            centers = np.stack([centers] * self.num_anchors, axis=1).reshape((-1, 2))
            self._anchor_cache[key] = centers
        return self._anchor_cache[key]

    def detect(self, img, thresh=0.5, nms_thresh=0.4):
        iw, ih = self.input_size
        im_ratio = img.shape[0] / img.shape[1]
        if im_ratio > ih / iw:
            new_h, new_w = ih, int(ih / im_ratio)
        else:
            new_w, new_h = iw, int(iw * im_ratio)
        det_scale = new_h / img.shape[0]
        resized = cv2.resize(img, (new_w, new_h))
        det_img = np.zeros((ih, iw, 3), dtype=np.uint8)
        det_img[:new_h, :new_w, :] = resized

        blob = cv2.dnn.blobFromImage(det_img, 1.0 / 128, (iw, ih), (127.5, 127.5, 127.5), swapRB=True)
        outs = self.session.run(self.output_names, {self.input_name: blob})

        scores_list, boxes_list, kps_list = [], [], []
        for idx, stride in enumerate(self.strides):
            scores = outs[idx].reshape(-1)
            bbox_preds = outs[idx + self.fmc].reshape(-1, 4) * stride
            kps_preds = outs[idx + self.fmc * 2].reshape(-1, 10) * stride
            h, w = ih // stride, iw // stride
            centers = self._anchors(h, w, stride)
            pos = np.where(scores >= thresh)[0]
            boxes = np.stack([centers[:, 0] - bbox_preds[:, 0], centers[:, 1] - bbox_preds[:, 1],
                              centers[:, 0] + bbox_preds[:, 2], centers[:, 1] + bbox_preds[:, 3]], axis=-1)
            kps = np.empty_like(kps_preds)
            kps[:, 0::2] = centers[:, 0:1] + kps_preds[:, 0::2]
            kps[:, 1::2] = centers[:, 1:2] + kps_preds[:, 1::2]
            scores_list.append(scores[pos])
            boxes_list.append(boxes[pos])
            kps_list.append(kps[pos].reshape(-1, 5, 2))

        scores = np.concatenate(scores_list)
        if scores.size == 0:
            return np.zeros((0, 5), dtype=np.float32), np.zeros((0, 5, 2), dtype=np.float32)
        order = scores.argsort()[::-1]
        boxes = np.vstack(boxes_list)[order] / det_scale
        kpss = np.concatenate(kps_list)[order] / det_scale
        scores = scores[order]
        dets = np.hstack([boxes, scores[:, None]]).astype(np.float32)
        keep = self._nms(dets, nms_thresh)
        return dets[keep], kpss[keep]

    @staticmethod
    def _nms(dets, thresh):
        x1, y1, x2, y2, s = dets.T
        areas = (x2 - x1 + 1) * (y2 - y1 + 1)
        order = s.argsort()[::-1]
        keep = []
        while order.size > 0:
            i = order[0]
            keep.append(i)
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            w = np.maximum(0.0, xx2 - xx1 + 1)
            h = np.maximum(0.0, yy2 - yy1 + 1)
            ovr = (w * h) / (areas[i] + areas[order[1:]] - w * h)
            order = order[np.where(ovr <= thresh)[0] + 1]
        return keep


def _umeyama(src, dst):
    """Similarity transform (rotation, uniform scale, translation) — as skimage.SimilarityTransform."""
    num, dim = src.shape
    src_mean, dst_mean = src.mean(axis=0), dst.mean(axis=0)
    src_d, dst_d = src - src_mean, dst - dst_mean
    A = dst_d.T @ src_d / num
    d = np.ones((dim,), dtype=np.float64)
    if np.linalg.det(A) < 0:
        d[dim - 1] = -1
    T = np.eye(dim + 1, dtype=np.float64)
    U, S, V = np.linalg.svd(A)
    rank = np.linalg.matrix_rank(A)
    if rank == 0:
        return np.nan * T
    if rank == dim - 1:
        if np.linalg.det(U) * np.linalg.det(V) > 0:
            T[:dim, :dim] = U @ V
        else:
            s = d[dim - 1]
            d[dim - 1] = -1
            T[:dim, :dim] = U @ np.diag(d) @ V
            d[dim - 1] = s
    else:
        T[:dim, :dim] = U @ np.diag(d) @ V
    scale = 1.0 / src_d.var(axis=0).sum() * (S @ d)
    T[:dim, dim] = dst_mean - scale * (T[:dim, :dim] @ src_mean.T)
    T[:dim, :dim] *= scale
    return T


def _norm_crop(img, kps, size=112):
    M = _umeyama(kps.astype(np.float64), ARCFACE_DST.astype(np.float64))[0:2, :]
    return cv2.warpAffine(img, M, (size, size), borderValue=0.0)


class _ArcFace:
    def __init__(self, path, session_options):
        import onnxruntime as ort
        self.session = ort.InferenceSession(path, sess_options=session_options, providers=['CPUExecutionProvider'])
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name

    def embed(self, aligned_bgr):
        blob = cv2.dnn.blobFromImages([aligned_bgr], 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)
        return self.session.run([self.output_name], {self.input_name: blob})[0][0]


def _load():
    global _models
    if _models is not None:
        return _models
    with _lock:
        if _models is None:
            import onnxruntime as ort
            directory = _model_dir()
            det_path = _ensure_file(directory, DET_MODEL)
            rec_path = _ensure_file(directory, REC_MODEL)
            opts = ort.SessionOptions()
            opts.log_severity_level = 3
            _models = (_SCRFD(det_path, opts), _ArcFace(rec_path, opts))
            logger.info('ONNX ArcFace (buffalo_l) loaded from %s', directory)
    return _models


class Face:
    def __init__(self, bbox, kps, det_score, embedding):
        self.bbox, self.kps, self.det_score, self.embedding = bbox, kps, det_score, embedding


def get_faces(bgr_image):
    """Return detected faces (with 512-d raw ArcFace embeddings), like insightface FaceAnalysis.get()."""
    detector, recogniser = _load()
    dets, kpss = detector.detect(bgr_image)
    faces = []
    for det, kps in zip(dets, kpss):
        aligned = _norm_crop(bgr_image, kps)
        faces.append(Face(det[:4], kps, float(det[4]), recogniser.embed(aligned).astype(np.float32)))
    return faces
