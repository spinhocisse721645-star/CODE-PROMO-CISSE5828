from flask import Flask, jsonify
from flask_cors import CORS
from datetime import datetime, timezone
import requests
import os
import threading
import time

app = Flask(__name__)
CORS(app)

# ============================================================
# CONFIGURATION
# ============================================================

FOOTBALL_DATA_URL = "https://api.football-data.org/v4"
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
FOOTBALL_DATA_TOKEN = os.environ.get("FOOTBALL_DATA_TOKEN", "")

SYNC_INTERVAL = 60

# Cache mémoire
matches_cache = {
    "data": [],
    "updated_at": None
}

cache_lock = threading.Lock()
sync_thread = None


# ============================================================
# OUTILS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat()


def parse_utc_date(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        return None


# ============================================================
# MINUTE OFFICIELLE
# ============================================================

def get_match_minute(match):
    """
    IMPORTANT :
    On utilise uniquement la minute fournie par
    Football-Data.org.

    Aucun calcul à partir de utcDate.
    Aucune minute inventée.
    """

    value = match.get("minute")

    if value is None:
        return None

    try:
        minute = int(value)

        if minute < 0:
            return None

        if minute > 90:
            return 90

        return minute

    except (TypeError, ValueError):
        return None


# ============================================================
# CONVERSION FOOTBALL-DATA -> CISSE PRONOS
# ============================================================

def convert_match(match):
    home_team = match.get("homeTeam") or {}
    away_team = match.get("awayTeam") or {}

    score = match.get("score") or {}
    full_time = score.get("fullTime") or {}

    status = match.get("status")

    score_home = full_time.get("home")
    score_away = full_time.get("away")

    # --------------------------------------------------------
    # STATUT CISSE PRONOS
    # --------------------------------------------------------

    if status in ["IN_PLAY", "PAUSED"]:
        statut = "LIVE"

    elif status == "FINISHED":
        statut = "FINISHED"

    elif status == "POSTPONED":
        statut = "POSTPONED"

    elif status == "CANCELLED":
        statut = "CANCELLED"

    elif status == "SUSPENDED":
        statut = "SUSPENDED"

    elif status == "AWARDED":
        statut = "FINISHED"

    else:
        statut = "SCHEDULED"

    # --------------------------------------------------------
    # MINUTE OFFICIELLE
    # --------------------------------------------------------

    minute = get_match_minute(match)

    # --------------------------------------------------------
    # DATE
    # --------------------------------------------------------

    utc_date = match.get("utcDate")

    # --------------------------------------------------------
    # IDENTIFIANT
    # --------------------------------------------------------

    match_id = str(match.get("id"))

    return {
        "id": match_id,

        "equipe1": home_team.get("name"),
        "equipe2": away_team.get("name"),

        "date_match": utc_date,

        "statut": statut,

        # Minute fournie directement par Football-Data.org
        "minute": minute,

        "score1": score_home,
        "score2": score_away,

        "logo1": home_team.get("crest"),
        "logo2": away_team.get("crest"),

        "updated_at": iso_now()
    }


# ============================================================
# FOOTBALL-DATA.ORG
# ============================================================

def fetch_matches_from_football_data():
    if not FOOTBALL_DATA_TOKEN:
        raise RuntimeError(
            "FOOTBALL_DATA_TOKEN n'est pas configuré dans Render."
        )

    headers = {
        "X-Auth-Token": FOOTBALL_DATA_TOKEN
    }

    today = utc_now().date()
    tomorrow = today.fromordinal(today.toordinal() + 1)

    params = {
        "dateFrom": today.isoformat(),
        "dateTo": tomorrow.isoformat()
    }

    response = requests.get(
        f"{FOOTBALL_DATA_URL}/matches",
        headers=headers,
        params=params,
        timeout=30
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Football-Data.org HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json()

    matches = data.get("matches", [])

    converted = []

    for match in matches:
        try:
            converted.append(
                convert_match(match)
            )
        except Exception as e:
            print(
                f"⚠️ Match ignoré {match.get('id')}: {e}"
            )

    return converted


# ============================================================
# SUPABASE
# ============================================================

def supabase_headers():
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }


def get_existing_created_at(match_ids):
    if not match_ids:
        return {}

    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "SUPABASE_URL ou SUPABASE_SERVICE_ROLE_KEY manquant."
        )

    # Construction du filtre PostgREST :
    # id=in.(1,2,3)
    id_list = ",".join(
        str(match_id)
        for match_id in match_ids
    )

    url = (
        f"{SUPABASE_URL}/rest/v1/matchs"
        f"?select=id,created_at"
        f"&id=in.({id_list})"
    )

    response = requests.get(
        url,
        headers=supabase_headers(),
        timeout=30
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Erreur lecture Supabase "
            f"{response.status_code}: {response.text[:500]}"
        )

    rows = response.json()

    result = {}

    for row in rows:
        if row.get("id") is not None:
            result[str(row["id"])] = row.get("created_at")

    return result


def save_matches_to_supabase(matches):
    if not matches:
        return

    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "SUPABASE_URL ou SUPABASE_SERVICE_ROLE_KEY manquant."
        )

    existing_created_at = get_existing_created_at(
        [match["id"] for match in matches]
    )

    rows = []

    for match in matches:

        match_id = str(match["id"])

        old_created_at = existing_created_at.get(match_id)

        row = {
            "id": match["id"],
            "equipe1": match["equipe1"],
            "equipe2": match["equipe2"],
            "date_match": match["date_match"],
            "statut": match["statut"],
            "minute": match["minute"],
            "score1": match["score1"],
            "score2": match["score2"],
            "logo1": match["logo1"],
            "logo2": match["logo2"],
            "updated_at": match["updated_at"]
        }

        # Ne jamais modifier created_at pour un match existant.
        if old_created_at:
            row["created_at"] = old_created_at
        else:
            row["created_at"] = iso_now()

        rows.append(row)

    url = f"{SUPABASE_URL}/rest/v1/matchs"

    response = requests.post(
        url,
        headers=supabase_headers(),
        json=rows,
        timeout=30
    )

    if response.status_code not in [200, 201, 204]:
        raise RuntimeError(
            f"Erreur écriture Supabase "
            f"{response.status_code}: {response.text[:1000]}"
        )


# ============================================================
# SYNCHRONISATION
# ============================================================

def sync_matches_once():
    print("🔄 Synchronisation Football-Data.org...")

    matches = fetch_matches_from_football_data()

    print(
        f"⚽ {len(matches)} matchs récupérés."
    )

    if matches:
        save_matches_to_supabase(matches)

    with cache_lock:
        matches_cache["data"] = matches
        matches_cache["updated_at"] = iso_now()

    live_count = sum(
        1
        for match in matches
        if match.get("statut") == "LIVE"
    )

    print(
        f"✅ Synchronisation terminée | "
        f"LIVE: {live_count}"
    )

    # Affichage utile pour vérifier les minutes réelles
    for match in matches:
        if match.get("statut") == "LIVE":
            print(
                f"🔴 LIVE | "
                f"{match.get('equipe1')} "
                f"{match.get('score1')} - "
                f"{match.get('score2')} "
                f"{match.get('equipe2')} | "
                f"minute={match.get('minute')}"
            )

    return matches


# ============================================================
# SYNCHRONISATION AUTOMATIQUE
# ============================================================

def automatic_sync_loop():
    # Petite attente pour laisser Flask démarrer
    time.sleep(5)

    while True:
        try:
            sync_matches_once()

        except Exception as e:
            print(
                f"❌ Erreur synchronisation automatique: {e}"
            )

        time.sleep(SYNC_INTERVAL)


def start_background_sync():
    global sync_thread

    if (
        sync_thread is None
        or not sync_thread.is_alive()
    ):
        sync_thread = threading.Thread(
            target=automatic_sync_loop,
            daemon=True,
            name="matches-sync"
        )

        sync_thread.start()

        print(
            "🚀 Synchronisation automatique activée "
            f"(toutes les {SYNC_INTERVAL} secondes)"
        )


# ============================================================
# RÉCUPÉRATION CACHE
# ============================================================

def get_matches_data(force_refresh=False):

    with cache_lock:
        cached_matches = matches_cache["data"]
        cached_time = matches_cache["updated_at"]

    if (
        not force_refresh
        and cached_matches
    ):
        return cached_matches

    try:
        matches = sync_matches_once()
        return matches

    except Exception as e:
        print(
            f"❌ Impossible de synchroniser: {e}"
        )

        # Si le cache existe, on le conserve
        return cached_matches


# ============================================================
# API MATCHS
# ============================================================

@app.route("/api/matches", methods=["GET"])
def api_matches():

    try:
        matches = get_matches_data()

        return jsonify({
            "success": True,
            "source": "Football-Data.org",
            "real_data_only": True,
            "count": len(matches),
            "matches": matches
        })

    except Exception as e:

        return jsonify({
            "success": False,
            "error": str(e),
            "source": "Football-Data.org",
            "real_data_only": True
        }), 500


# ============================================================
# API LIVE
# ============================================================

@app.route("/api/live", methods=["GET"])
def api_live():

    try:
        matches = get_matches_data()

        live_matches = [
            match
            for match in matches
            if match.get("statut") == "LIVE"
        ]

        return jsonify({
            "success": True,
            "source": "Football-Data.org",
            "real_data_only": True,
            "count": len(live_matches),
            "matches": live_matches
        })

    except Exception as e:

        return jsonify({
            "success": False,
            "error": str(e),
            "source": "Football-Data.org",
            "real_data_only": True
        }), 500


# ============================================================
# STATISTIQUES DU JOUR
# ============================================================

def calculate_statistics(matches):

    total = len(matches)

    live = sum(
        1
        for match in matches
        if match.get("statut") == "LIVE"
    )

    finished = sum(
        1
        for match in matches
        if match.get("statut") == "FINISHED"
    )

    cancelled = sum(
        1
        for match in matches
        if match.get("statut") == "CANCELLED"
    )

    postponed = sum(
        1
        for match in matches
        if match.get("statut") == "POSTPONED"
    )

    upcoming = sum(
        1
        for match in matches
        if match.get("statut") == "SCHEDULED"
    )

    competitions = {}

    for match in matches:

        # On ne fabrique aucune statistique.
        # On utilise uniquement les informations reçues.
        competition = match.get("competition")

        if competition:
            competitions[competition] = (
                competitions.get(competition, 0) + 1
            )

    return {
        "total_matches": total,
        "live": live,
        "finished": finished,
        "upcoming": upcoming,
        "cancelled": cancelled,
        "postponed": postponed,
        "competitions": competitions
    }


def filter_matches_for_today(matches):

    today = utc_now().date()

    result = []

    for match in matches:

        date_value = parse_utc_date(
            match.get("date_match")
        )

        if not date_value:
            continue

        if date_value.date() == today:
            result.append(match)

    return result


@app.route("/api/statistics/today", methods=["GET"])
def api_statistics_today():

    try:

        matches = get_matches_data()

        today_matches = filter_matches_for_today(
            matches
        )

        statistics = calculate_statistics(
            today_matches
        )

        return jsonify({
            "success": True,
            "date": utc_now().date().isoformat(),
            "source": "Football-Data.org",
            "data_policy": {
                "real_data_only": True,
                "no_fake_statistics": True
            },
            "statistics": statistics
        })

    except Exception as e:

        return jsonify({
            "success": False,
            "error": str(e),
            "source": "Football-Data.org",
            "data_policy": {
                "real_data_only": True,
                "no_fake_statistics": True
            }
        }), 500


# ============================================================
# ROUTE PRINCIPALE
# ============================================================

@app.route("/", methods=["GET"])
def home():

    return jsonify({
        "success": True,
        "message": "CISSE PRONOS API",
        "source": "Football-Data.org",
        "real_data_only": True,
        "endpoints": [
            "/api/matches",
            "/api/live",
            "/api/statistics/today"
        ]
    })


# ============================================================
# DÉMARRAGE SYNCHRONISATION
# ============================================================

start_background_sync()


# ============================================================
# LANCEMENT LOCAL
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get("PORT", 5000)
        )
  )
