import os
import uvicorn
import io
from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
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


# Allow your frontend origin to access the backend
origins = [
    "https://skydoser-test.web.app",
    # you can also add localhost for testing
    "http://localhost:5000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # or ["*"] for testing only
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------
# Health Check
# ---------------------------
@app.get("/")
async def health():
    return {"status": "Backend running on Cloud Run"}

# ---------------------------
# ESP32 Routes
# ---------------------------

@app.get("/bin-info/{location_id}/{bin_id}")
async def get_bin_info(location_id: str, bin_id: str, request: Request):
    # ESP32 authenticates using DEVICE_SECRET
    auth_header = request.headers.get("Authorization")
    if auth_header != f"Bearer {DEVICE_SECRET}":
        raise HTTPException(status_code=403, detail="Unauthorized")

    bin_doc = db.collection("locations").document(location_id).collection("bins").document(bin_id).get()
    if not bin_doc.exists:
        raise HTTPException(status_code=404, detail="Bin not found")

    # Return only growthStage and colour for ESP32
    data = bin_doc.to_dict()
    return {
        "growthStage": data.get("growthStage"),
        "colour": data.get("colour")
    }

@app.post("/upload-log/{location_id}")
async def upload_log(request: Request, location_id: str):

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
    db.collection("locations") \
      .document(location_id) \
      .collection("bins") \
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

    user_doc = db.collection("users").document(uid).get()
    location_id = user_doc.to_dict().get("location")

    bins = db.collection("locations") \
            .document(location_id) \
            .collection("bins") \
            .stream()
    rows = []

    for bin_doc in bins:
        bin_data = bin_doc.to_dict()
        device_id = bin_doc.id

        log_doc = db.collection("locations").document(location_id).collection("bins").document(device_id).collection("logs").document(date).get()
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

    # Get user's location
    user_doc = db.collection("users").document(uid).get()
    if not user_doc.exists:
        raise HTTPException(status_code=403, detail="User not found")

    location_id = user_doc.to_dict().get("location")

    try:
        db.collection("locations").document(location_id).collection("bins").document(device_id).set({
            "growthStage": growthStage,
            "colour": colour
        }, merge=True)

        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/bins")
async def get_bins(request: Request):
    uid = verify_firebase_token(request)

    user_doc = db.collection("users").document(uid).get()
    location_id = user_doc.to_dict().get("location")
    if not location_id:
        raise HTTPException(status_code=403, detail="No location assigned")

    try:
        bins_ref = db.collection("locations").document(location_id).collection("bins").stream()

        bins = []
        for doc in bins_ref:
            bin_data = doc.to_dict()
            bin_data["device_id"] = doc.id
            bins.append(bin_data)

        return {"bins": bins}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.delete("/delete-bin")
async def delete_bin(request: Request, device_id: str):
    uid = verify_firebase_token(request)

    user_doc = db.collection("users").document(uid).get()
    location_id = user_doc.to_dict().get("location")
    if not location_id:
        raise HTTPException(status_code=403, detail="No location assigned")

    try:
        db.collection("locations") \
          .document(location_id) \
          .collection("bins") \
          .document(device_id) \
          .delete()

        return {"success": True}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/upload-bins-csv")
async def upload_bins_csv(request: Request, file: UploadFile = File(...)):
    uid = verify_firebase_token(request)

    user_doc = db.collection("users").document(uid).get()
    location_id = user_doc.to_dict().get("location")
    if not location_id:
        raise HTTPException(status_code=403, detail="No location assigned")

    try:
        contents = await file.read()
        df = pd.read_csv(io.BytesIO(contents))
        required_cols = ["barcode", "growthStage", "colour"]
        if not all(col in df.columns for col in required_cols):
            raise HTTPException(status_code=400, detail=f"CSV must have columns: {required_cols}")

        for _, row in df.iterrows():
            device_id = str(row["barcode"]).strip()
            growthStage = str(row["growthStage"])
            colour = str(row["colour"])

            db.collection("locations") \
              .document(location_id) \
              .collection("bins") \
              .document(device_id) \
              .set({
                  "growthStage": growthStage,
                  "colour": colour
              }, merge=True)

        return {"success": True, "message": f"{len(df)} bins added/updated"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)