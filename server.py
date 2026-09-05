from flask import Flask, jsonify
import os
import requests

app = Flask(__name__)

API_FOOTBALL_URL = "https://v3.football.api-sports.io"

@app.route("/")
def home():
    return jsonify({
        "message": "CISSE PRONOS API fonctionne !"
    })


@app.route("/api/matches")
def matches():
    api_key = os.getenv("API_FOOTBALL_KEY")

    if not api_key:
        return jsonify({
            "error": "API_FOOTBALL_KEY manquante"
        }), 500

    headers = {
        "x-apisports-key": api_key
    }

    response = requests.get(
        f"{API_FOOTBALL_URL}/fixtures",
        headers=headers,
        params={"live": "all"},
        timeout=20
    )

    return jsonify(response.json()), response.status_code


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
