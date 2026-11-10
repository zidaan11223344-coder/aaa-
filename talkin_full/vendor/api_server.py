from flask import Flask, jsonify, request
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

app = Flask(__name__)

@app.route('/status', methods=['GET'])
def get_status():
    return jsonify({
        "status": "online",
        "bot_id": os.getenv("BOT_ID"),
        "group": os.getenv("GROUP_TO_JOIN")
    })

@app.route('/config', methods=['POST'])
def update_config():
    data = request.json
    return jsonify({"message": "Configuration updated", "received": data})

if __name__ == "__main__":
    port = int(os.getenv("API_PORT", 5000))
    debug = os.getenv("API_DEBUG", "True") == "True"
    app.run(host='0.0.0.0', port=port, debug=debug)
