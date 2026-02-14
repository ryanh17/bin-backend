from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import firebase_admin
from firebase_admin import firestore
import pandas as pd
import os
from datetime import datetime

# Initialize Firebase (works automatically in Cloud Run)
firebase_admin.initialize_app()
db = firestore.client()

app = FastAPI()

DEVICE_SECRET = "skyacres-ogmP3GCWq@"


@app.get("/")
def health():
    return {"status": "Backend running on Cloud Run"}


@app.post("/update-bin")
def update_bin(device_id: str, growthStage: str, colour: str):
    try:
        db.collection("bins").document(device_id).update({
            "growthStage": growthStage,
            "colour": colour
        })
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/download-logs")
def download_logs(date: str):
    try:
        bins = db.collection("bins").stream()

        rows = []

        for bin_doc in bins:
            bin_data = bin_doc.to_dict()
            device_id = bin_doc.id

            log_doc = (
                db.collection("bins")
                .document(device_id)
                .collection("logs")
                .document(date)
                .get()
            )

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

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    



@app.route("/log", methods=["POST"])
def log_data():
    auth_header = request.headers.get("Authorization")

    if auth_header != f"Bearer {DEVICE_SECRET}":
        return {"error": "Unauthorized"}, 403

    data = request.json

    db.collection("bins") \
      .document(data["binId"]) \
      .collection("logs") \
      .add(data)

    return {"status": "ok"}
