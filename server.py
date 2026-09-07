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
# CALCUL DE LA MINUTE DU MATCH
# ============================================================

def calculate_match_minute(match):
    """
    Calcule la minute du match à partir de l'heure officielle
    du coup d'envoi fournie par Football-Data.org.

    IMPORTANT :
    Football-Data.org ne fournit pas toujours un champ "minute"
    officiel dans la réponse gratuite.

    Pour un match IN_PLAY, on calcule donc le temps écoulé
    depuis le coup d'envoi.

    On ne fabrique aucune statistique.
    """

    status = match.get("status")

    # --------------------------------------------------------
    # Match pas encore commencé
    # --------------------------------------------------------

    if status in ["SCHEDULED", "TIMED", "POSTPONED", "CANCELLED"]:
        return 0

    # --------------------------------------------------------
    # Match terminé
    # --------------------------------------------------------
    # On ne met pas artificiellement 90.
    # La minute affichée est uniquement utile pour le LIVE.
    # --------------------------------------------------------

    if status in ["FINISHED", "SUSPENDED", "AWARDED"]:
        return 0

    # --------------------------------------------------------
    # Match en direct / pause
    # --------------------------------------------------------

    if status not in ["IN_PLAY", "PAUSED"]:
        return 0

    utc_date = match.get("utcDate")

    if not utc_date:
        return 0

    try:

        # Exemple :
        # 2026-09-07T23:00:00Z
        kickoff = datetime.fromisoformat(
            utc_date.replace("Z", "+00:00")
        )

        now = datetime.now(timezone.utc)

        elapsed_seconds = (now - kickoff).total_seconds()

        # Protection si l'heure reçue est légèrement dans le futur
        if elapsed_seconds < 0:
            return 0

        elapsed_minutes = int(elapsed_seconds // 60)

        # ----------------------------------------------------
        # Match en pause
        # ----------------------------------------------------
        # Pour une pause de mi-temps, on affiche 45.
        # On ne dépasse pas 45 ici.
        # ----------------------------------------------------

        if status == "PAUSED":
            return min(elapsed_minutes, 45)

        # ----------------------------------------------------
        # IN_PLAY
        # ----------------------------------------------------
        # Match normal : 1 à 90.
        # On ne prétend pas connaître les arrêts de jeu exacts.
        # ----------------------------------------------------

        if elapsed_minutes < 1:
            return 1

        if elapsed_minutes > 90:
            return 90

        return elapsed_minutes

    except (ValueError, TypeError):
        return 0


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

        # ----------------------------------------------------
        # On ignore les matchs dont les équipes sont inconnues
        # ----------------------------------------------------

        if not home_name or not away_name:
            continue

        # ----------------------------------------------------
        # SCORES
        # ----------------------------------------------------

        home_score = full_time.get("home")
        away_score = full_time.get("away")

        # Pour un match pas encore commencé,
        # Football-Data.org peut renvoyer null.
        #
        # On conserve 0 comme valeur d'affichage du score.
        # Cela ne crée pas de statistique.
        #

        if home_score is None:
            home_score = 0

        if away_score is None:
            away_score = 0

        # ----------------------------------------------------
        # LOGOS
        # ----------------------------------------------------

        home_logo = home_team.get("crest")
        away_logo = away_team.get("crest")

        # ----------------------------------------------------
        # STATUT
        # ----------------------------------------------------

        status = match.get("status", "SCHEDULED")

        # ----------------------------------------------------
        # MINUTE
        # ----------------------------------------------------

        minute = calculate_match_minute(match)

        # ----------------------------------------------------
        # IDENTIFIANT
        # ----------------------------------------------------

        match_id = match.get("id")

        if match_id is None:
            continue

        # ----------------------------------------------------
        # LIGNE SUPABASE
        # ----------------------------------------------------

        row = {
            "id": str(match_id),
            "equipe1": home_name,
            "equipe2": away_name,
            "logo1": home_logo,
            "logo2": away_logo,
            "date_match": match.get("utcDate"),
            "statut": status,
            "minute": minute,
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

    # ========================================================
    # SUPABASE HEADERS
    # ========================================================

    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }

    # ========================================================
    # ENVOI SUPABASE
    # ========================================================

    try:

        response = requests.post(
            f"{supabase_url}/rest/v1/matchs",
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

    # ========================================================
    # VERIFICATION REPONSE SUPABASE
    # ========================================================

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
