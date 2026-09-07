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
# SUPABASE
# ============================================================

def save_matches_to_supabase(data):

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not supabase_key:
        return {
            "success": False,
            "error": "Variables Supabase manquantes"
        }

    matches = data.get("matches", [])

    if not matches:
        return {
            "success": True,
            "saved": 0
        }

    rows = []

    now = datetime.now(timezone.utc).isoformat()

    for match in matches:

        home_team = match.get("homeTeam", {})
        away_team = match.get("awayTeam", {})
        score = match.get("score", {})
        full_time = score.get("fullTime", {})

        home_name = home_team.get("name")
        away_name = away_team.get("name")

        # On ignore les matchs dont les équipes sont inconnues
        if not home_name or not away_name:
            continue

        home_score = full_time.get("home")
        away_score = full_time.get("away")

        if home_score is None:
            home_score = 0

        if away_score is None:
            away_score = 0

        status = match.get("status", "SCHEDULED")

        row = {
            "id": str(match.get("id")),
            "equipe1": home_name,
            "equipe2": away_name,
            "date_match": match.get("utcDate"),
            "statut": status,
            "minute": 0,
            "score1": home_score,
            "score2": away_score,
            "created_at": now,
            "updated_at": now
        }

        rows.append(row)

    if not rows:
        return {
            "success": True,
            "saved": 0
        }

    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }

    try:

        response = requests.post(
            f"{supabase_url}/rest/v1/matches",
            headers=headers,
            json=rows,
            timeout=20
        )

    except requests.RequestException as e:

        return {
            "success": False,
            "error": "Impossible de contacter Supabase",
            "details": str(e)
        }

    if response.status_code not in [200, 201, 204]:

        return {
            "success": False,
            "error": "Erreur Supabase",
            "status": response.status_code,
            "details": response.text
        }

    return {
        "success": True,
        "saved": len(rows)
    }


# ============================================================
# PAGE PRINCIPALE
# ============================================================

@app.route("/")
def home():

    return jsonify({
        "message": "CISSE PRONOS API fonctionne avec Football-Data.org et Supabase !"
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
    # ENREGISTREMENT SUPABASE
    # --------------------------------------------------------

    supabase_result = save_matches_to_supabase(data)

    # --------------------------------------------------------
    # Sauvegarde dans le cache
    # --------------------------------------------------------

    matches_cache["data"] = data
    matches_cache["timestamp"] = now

    # --------------------------------------------------------
    # Réponse
    # --------------------------------------------------------

    return jsonify({
        "source": "Football-Data.org",
        "supabase": supabase_result,
        "data": data
    })


# ============================================================
# MATCHS LIVE
# ============================================================

@app.route("/api/live")
def live_matches():

    token = os.getenv("FOOTBALL_DATA_TOKEN")

    if not token:

        return jsonify({
            "error": "FOOTBALL_DATA_TOKEN manquante"
        }), 500

    # --------------------------------------------------------
    # Appel Football-Data.org
    # --------------------------------------------------------

    headers = {
        "X-Auth-Token": token
    }

    params = {
        "status": "LIVE"
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
    # Lecture LIVE
    # --------------------------------------------------------

    data = response.json()

    # --------------------------------------------------------
    # ENREGISTREMENT SUPABASE
    # --------------------------------------------------------

    supabase_result = save_matches_to_supabase(data)

    # --------------------------------------------------------
    # Réponse LIVE
    # --------------------------------------------------------

    return jsonify({
        "source": "Football-Data.org",
        "supabase": supabase_result,
        "data": data
    })


# ============================================================
# LANCEMENT
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000
        )
