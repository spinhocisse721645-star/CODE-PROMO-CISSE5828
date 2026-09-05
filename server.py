from flask import Flask, jsonify
import os
import requests

app = Flask(__name__)

APIFY_URL = "https://api.apify.com/v2/acts/dataizi-srl~flashscore-data-extractor/run-sync-get-dataset-items"


@app.route("/")
def home():
    return jsonify({
        "message": "CISSE PRONOS API fonctionne avec Apify !"
    })


@app.route("/api/matches")
def matches():
    token = os.getenv("APIFY_API_TOKEN")

    if not token:
        return jsonify({
            "error": "APIFY_API_TOKEN manquante"
        }), 500

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    data = {
        "mode": "score_mode",
        "sports": ["football"],
        "matchStatuses": ["all"],
        "leagues": [],
        "dayOffsets": ["0"],
        "maxItems": 500
    }

    response = requests.post(
        APIFY_URL,
        headers=headers,
        json=data,
        timeout=300
    )

    if response.status_code != 200:
        return jsonify({
            "error": "Erreur Apify",
            "details": response.text
        }), response.status_code

    return jsonify(response.json())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
