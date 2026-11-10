import firebase_admin
from firebase_admin import credentials, firestore
import os
from dotenv import load_dotenv

load_dotenv()

def init_firebase():
    cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH")
    if os.path.exists(cred_path):
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
        return firestore.client()
    else:
        print("[FIREBASE] Credentials file not found. Firebase features disabled.")
        return None

if __name__ == "__main__":
    db = init_firebase()
    if db:
        print("[FIREBASE] Initialized successfully.")
