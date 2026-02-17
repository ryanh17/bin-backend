import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse
import firebase_admin
from firebase_admin import firestore, auth
import pandas as pd

# ---------------------------
# Initialize Firebase Admin
# ---------------------------
firebase_admin.initialize_app()
db = firestore.client()

# ---------------------------
# Secrets from Environment
# ---------------------------
DEVICE_SECRET = os.getenv("DEVICE_SECRET")  # for ESP32 devices

# ---------------------------
# FastAPI App
# ---------------------------
app = FastAPI()

# ---------------------------
# Health Check
# ---------------------------
@app.get("/")
async def health():
    return {"status": "Backend running on Cloud Run"}

# ---------------------------
# ESP32 Routes
# ---------------------------

@app.get("/bin-info/{bin_id}")
async def get_bin_info(bin_id: str, request: Request):
    # ESP32 authenticates using DEVICE_SECRET
    auth_header = request.headers.get("Authorization")
    if auth_header != f"Bearer {DEVICE_SECRET}":
        raise HTTPException(status_code=403, detail="Unauthorized")

    bin_doc = db.collection("bins").document(bin_id).get()
    if not bin_doc.exists:
        raise HTTPException(status_code=404, detail="Bin not found")

    # Return only growthStage and colour for ESP32
    data = bin_doc.to_dict()
    return {
        "growthStage": data.get("growthStage"),
        "colour": data.get("colour")
    }

@app.post("/upload-log")
async def upload_log(request: Request):

    # ---- Simple device authentication ----
    auth_header = request.headers.get("Authorization")
    if auth_header != f"Bearer {DEVICE_SECRET}":
        raise HTTPException(status_code=403, detail="Unauthorized")

    data = await request.json()

    bin_id = data.get("binId")
    date_id = data.get("date")

    if not bin_id or not date_id:
        raise HTTPException(status_code=400, detail="Missing binId or date")

    # Remove binId + date from stored fields if you want
    log_data = {
        "ec": data.get("ec"),
        "targetEC": data.get("targetEC"),
        "dispenseInfo": data.get("dispenseInfo"),
        "premix": data.get("premix"),
        "totalVolume": data.get("totalVolume"),
        "timestamp": data.get("timestamp"),
    }

    # Overwrites same-day document
    db.collection("bins") \
      .document(bin_id) \
      .collection("logs") \
      .document(date_id) \
      .set(log_data)

    return {"status": "success"}

# ---------------------------
# Frontend Routes (Firebase Auth)
# ---------------------------

def verify_firebase_token(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=403, detail="Missing token")
    try:
        id_token = auth_header.split("Bearer ")[1]
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token["uid"]
    except Exception:
        raise HTTPException(status_code=403, detail="Invalid token")

@app.get("/download-logs")
async def download_logs(request: Request, date: str):
    uid = verify_firebase_token(request)

    bins = db.collection("bins").stream()
    rows = []

    for bin_doc in bins:
        bin_data = bin_doc.to_dict()
        device_id = bin_doc.id

        log_doc = db.collection("bins").document(device_id).collection("logs").document(date).get()
        log_data = log_doc.to_dict() if log_doc.exists else {}

        rows.append({
            "barcode": device_id,
            "growthStage": bin_data.get("growthStage"),
            "colour": bin_data.get("colour"),
            "ec": log_data.get("ec"),
            "targetEC": log_data.get("targetEC"),
            "premix": log_data.get("premix"),
            "totalVolume": log_data.get("totalVolume"),
            "timestamp": log_data.get("timestamp")
        })

    df = pd.DataFrame(rows)
    filename = f"logs_{date}.csv"
    df.to_csv(filename, index=False)

    return FileResponse(filename, filename=filename)

@app.post("/update-bin")
async def update_bin(request: Request, device_id: str, growthStage: str, colour: str):
    uid = verify_firebase_token(request)
    # Optionally: enforce per-location access here using custom claims

    try:
        db.collection("bins").document(device_id).update({
            "growthStage": growthStage,
            "colour": colour
        })
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
