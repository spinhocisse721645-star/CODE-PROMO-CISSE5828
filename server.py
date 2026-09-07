from flask import Flask, jsonify
import os
import requests
from datetime import datetime, timezone, timedelta

app = Flask(__name__)

FOOTBALL_DATA_URL = "https://api.football-data.org/v4"

# ============================================================
# CACHE
# ============================================================

matches_cache = {
    "data": None,
    "timestamp": None
}

CACHE_DURATION = 60  # 60 secondes


# ============================================================
# PAGE PRINCIPALE
# ============================================================

@app.route("/")
def home():
    return jsonify({
        "message": "CISSE PRONOS API fonctionne avec Football-Data.org !"
    })


# ============================================================
# MATCHS
# ============================================================

@app.route("/api/matches")
def matches():

    token = os.getenv("FOOTBALL_DATA_TOKEN")

    if not token:
        return jsonify({
            "error": "FOOTBALL_DATA_TOKEN manquante"
        }), 500

    # --------------------------------------------------------
    # Vérification du cache
    # --------------------------------------------------------

    now = datetime.now(timezone.utc)

    if (
        matches_cache["data"] is not None
        and matches_cache["timestamp"] is not None
        and (now - matches_cache["timestamp"]).total_seconds() < CACHE_DURATION
    ):
        return jsonify(matches_cache["data"])


    # --------------------------------------------------------
    # Dates : aujourd'hui + demain
    # --------------------------------------------------------

    today = now.date()
    tomorrow = today + timedelta(days=1)

    date_from = today.strftime("%Y-%m-%d")
    date_to = tomorrow.strftime("%Y-%m-%d")


    # --------------------------------------------------------
    # Appel Football-Data.org
    # --------------------------------------------------------

    headers = {
        "X-Auth-Token": token
    }

    params = {
        "dateFrom": date_from,
        "dateTo": date_to
    }

    try:

        response = requests.get(
            f"{FOOTBALL_DATA_URL}/matches",
            headers=headers,
            params=params,
            timeout=20
        )

    except requests.RequestException as e:

        return jsonify({
            "error": "Impossible de contacter Football-Data.org",
            "details": str(e)
        }), 502


    # --------------------------------------------------------
    # Gestion des erreurs
    # --------------------------------------------------------

    if response.status_code != 200:

        return jsonify({
            "error": "Erreur Football-Data.org",
            "status": response.status_code,
            "details": response.text
        }), response.status_code


    # --------------------------------------------------------
    # Lecture de la réponse
    # --------------------------------------------------------

    data = response.json()


    # --------------------------------------------------------
    # Sauvegarde dans le cache
    # --------------------------------------------------------

    matches_cache["data"] = data
    matches_cache["timestamp"] = now


    # --------------------------------------------------------
    # Réponse
    # --------------------------------------------------------

    return jsonify(data)


# ============================================================
# LANCEMENT
# ============================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000
    )
