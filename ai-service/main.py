"""
FastAPI AI Microservice — PakVerify Portal
Endpoints:
  POST /api/ai/verify/        → Face match + Liveness detection
  POST /api/ai/liveness/      → Liveness only
  GET  /health                → Health check
"""
import os

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from matching import verify_faces
from liveness import check_liveness

app = FastAPI(
    title="PakVerify AI Microservice",
    description="Biometric face verification and liveness detection for PakVerify Portal",
    version="1.0.0"
)

# Comma-separated list of allowed origins; "*" (default) keeps the old local behaviour.
_origins = [o.strip() for o in os.environ.get("AI_CORS_ORIGINS", "*").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "PakVerify AI Microservice"}


@app.post("/api/ai/verify/")
async def verify_identity(
    id_photo: UploadFile = File(..., description="Reference photo from CNIC/DB"),
    live_photo: UploadFile = File(..., description="Live webcam capture from user")
):
    """
    Runs face matching between the uploaded ID photo and the live camera capture.
    Returns confidence score and liveness score. Threshold: >= 90% to pass.
    """
    id_bytes   = await id_photo.read()
    live_bytes = await live_photo.read()

    face_result      = verify_faces(id_bytes, live_bytes)
    liveness_result  = check_liveness(live_bytes)

    confidence      = face_result["confidence"]
    liveness_score  = liveness_result["liveness_score"]
    verified        = confidence >= 90.0 and liveness_score >= 0.70

    return {
        "confidence":       round(confidence, 2),
        "liveness_score":   round(liveness_score, 3),
        "verified":         verified,
        "face_distance":    round(face_result.get("distance", 0.0), 4),
        "anti_spoofing":    liveness_result.get("anti_spoofing", "REAL"),
        "message":          "Verification successful" if verified else "Verification failed",
    }


@app.post("/api/ai/liveness/")
async def liveness_only(
    live_photo: UploadFile = File(..., description="Live webcam capture from user")
):
    """
    Runs liveness / anti-spoofing check only on the provided image.
    """
    live_bytes = await live_photo.read()
    try:
        return check_liveness(live_bytes)
    except Exception as exc:
        return {
            "liveness_score": 0.0,
            "face_detected": False,
            "anti_spoofing": "ERROR",
            "method": "service_error",
            "error": str(exc),
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
