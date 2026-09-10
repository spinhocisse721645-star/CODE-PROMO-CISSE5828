from flask import Flask, jsonify
from flask_cors import CORS
from datetime import datetime, timezone, timedelta
import os
import requests
import threading
import time

# ============================================================
# APPLICATION
# ============================================================

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

CORS(app)

# ============================================================
# CONFIGURATION
# ============================================================

FOOTBALL_DATA_URL = "https://api.football-data.org/v4"

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
FOOTBALL_DATA_TOKEN = os.getenv("FOOTBALL_DATA_TOKEN", "")

SUPABASE_MATCHS_URL = f"{SUPABASE_URL}/rest/v1/matchs"

# ============================================================
# CACHE
# ============================================================

matches_cache = {
    "data": None,
    "timestamp": 0
}

CACHE_DURATION = 60

# ============================================================
# LOCKS
# ============================================================

sync_lock = threading.Lock()
background_thread = None

# ============================================================
# UTILITAIRES
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


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
# MINUTE DU MATCH
#
# Football-Data.org fournit directement un champ "minute"
# (et parfois "injuryTime") dans la réponse de chaque match.
# On utilise CETTE donnée réelle en priorité.
#
# Le calcul par estimation (temps écoulé depuis le coup
# d'envoi) n'est utilisé qu'en tout dernier recours, si
# l'API ne renvoie vraiment aucune minute pour un match
# en cours.
# ============================================================

def calculate_match_minute_fallback(match):
    status = match.get("status")

    if status not in ["IN_PLAY", "PAUSED"]:
        return 0

    utc_date = match.get("utcDate")

    if not utc_date:
        return 0

    kickoff = parse_utc_date(utc_date)

    if not kickoff:
        return 0

    try:
        now = utc_now()

        elapsed_seconds = (now - kickoff).total_seconds()

        if elapsed_seconds < 0:
            return 0

        elapsed_minutes = int(elapsed_seconds // 60)

        if status == "PAUSED":
            return min(elapsed_minutes, 45)

        if elapsed_minutes < 1:
            return 1

        if elapsed_minutes > 90:
            return 90

        return elapsed_minutes

    except (ValueError, TypeError):
        return 0


def get_match_minute(match):
    status = match.get("status")

    if status in ["SCHEDULED", "TIMED", "POSTPONED", "CANCELLED", "FINISHED", "SUSPENDED", "AWARDED"]:
        return 0

    # Priorité à la vraie donnée fournie par Football-Data.org
    real_minute = match.get("minute")

    if real_minute is not None:
        try:
            return int(real_minute)
        except (ValueError, TypeError):
            pass

    # Repli uniquement si l'API n'a pas fourni de minute
    return calculate_match_minute_fallback(match)


# ============================================================
# CONVERSION FOOTBALL-DATA -> CISSE PRONOS
# ============================================================

def convert_match(match):
    home_team = match.get("homeTeam") or {}
    away_team = match.get("awayTeam") or {}

    score = match.get("score") or {}
    full_time = score.get("fullTime") or {}

    home_score = full_time.get("home")
    away_score = full_time.get("away")

    if home_score is None:
        regular_time = score.get("regularTime") or {}
        home_score = regular_time.get("home")

    if away_score is None:
        regular_time = score.get("regularTime") or {}
        away_score = regular_time.get("away")

    status = match.get("status")

    if home_score is None:
        home_score = 0

    if away_score is None:
        away_score = 0

    home_logo = home_team.get("crest")
    away_logo = away_team.get("crest")

    utc_date = match.get("utcDate")

    minute = get_match_minute(match)

    converted = {
        "id": str(match.get("id")),
        "equipe1": home_team.get("name"),
        "equipe2": away_team.get("name"),
        "date_match": utc_date,
        "statut": status,
        "minute": minute,
        "score1": home_score,
        "score2": away_score,
        "logo1": home_logo,
        "logo2": away_logo,
        "updated_at": utc_now().isoformat()
    }

    return converted


# ============================================================
# RÉCUPÉRER LES MATCHS EXISTANTS + created_at
# ============================================================

def get_existing_created_at(match_ids):
    if not SUPABASE_URL:
        print("❌ SUPABASE_URL manquant")
        return {}

    if not SUPABASE_SERVICE_ROLE_KEY:
        print("❌ SUPABASE_SERVICE_ROLE_KEY manquant")
        return {}

    if not match_ids:
        return {}

    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"
    }

    ids_string = ",".join(str(match_id) for match_id in match_ids)

    params = {
        "select": "id,created_at",
        "id": f"in.({ids_string})"
    }

    try:
        response = requests.get(
            SUPABASE_MATCHS_URL,
            headers=headers,
            params=params,
            timeout=20
        )

        if response.status_code != 200:
            print("❌ Erreur récupération created_at:", response.status_code, response.text)
            return {}

        rows = response.json()

        result = {}

        for row in rows:
            match_id = str(row.get("id"))
            result[match_id] = row.get("created_at")

        return result

    except requests.RequestException as error:
        print("❌ Erreur réseau Supabase:", error)
        return {}


# ============================================================
# SAUVEGARDE DES MATCHS DANS SUPABASE
# ============================================================

def save_matches_to_supabase(matches):
    if not SUPABASE_URL:
        print("❌ SUPABASE_URL manquant")
        return False

    if not SUPABASE_SERVICE_ROLE_KEY:
        print("❌ SUPABASE_SERVICE_ROLE_KEY manquant")
        return False

    if not matches:
        print("ℹ️ Aucun match à sauvegarder")
        return True

    match_ids = [
        str(match.get("id"))
        for match in matches
        if match.get("id") is not None
    ]

    existing_created_at = get_existing_created_at(match_ids)

    rows = []

    now = utc_now().isoformat()

    for match in matches:
        match_id = str(match.get("id"))

        row = {
            "id": match_id,
            "equipe1": match.get("equipe1"),
            "equipe2": match.get("equipe2"),
            "date_match": match.get("date_match"),
            "statut": match.get("statut"),
            "minute": match.get("minute"),
            "score1": match.get("score1"),
            "score2": match.get("score2"),
            "logo1": match.get("logo1"),
            "logo2": match.get("logo2"),
            "updated_at": now
        }

        if match_id in existing_created_at:
            row["created_at"] = existing_created_at[match_id]
        else:
            row["created_at"] = now

        rows.append(row)

    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }

    try:
        response = requests.post(
            SUPABASE_MATCHS_URL,
            headers=headers,
            json=rows,
            timeout=30
        )

        if response.status_code not in [200, 201, 204]:
            print("❌ Erreur Supabase upsert:", response.status_code)
            print(response.text)
            return False

        print(f"✅ {len(rows)} matchs synchronisés avec Supabase")
        return True

    except requests.RequestException as error:
        print("❌ Erreur réseau pendant la sauvegarde Supabase:", error)
        return False


# ============================================================
# FOOTBALL-DATA.ORG
# ============================================================

def fetch_matches_from_football_data():
    if not FOOTBALL_DATA_TOKEN:
        print("❌ FOOTBALL_DATA_TOKEN manquant")
        return []

    today = utc_now().date()
    tomorrow = today + timedelta(days=1)

    date_from = today.isoformat()
    date_to = tomorrow.isoformat()

    headers = {
        "X-Auth-Token": FOOTBALL_DATA_TOKEN
    }

    params = {
        "dateFrom": date_from,
        "dateTo": date_to
    }

    url = f"{FOOTBALL_DATA_URL}/matches"

    try:
        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=30
        )

        if response.status_code == 429:
            print("⚠️ Football-Data.org : limite de requêtes atteinte.")
            return []

        if response.status_code != 200:
            print("❌ Football-Data.org:", response.status_code)
            print(response.text)
            return []

        data = response.json()
        matches = data.get("matches", [])

        converted_matches = []

        for match in matches:
            try:
                converted = convert_match(match)
                converted_matches.append(converted)
            except Exception as error:
                print("⚠️ Impossible de convertir un match:", error)

        print(f"⚽ Football-Data.org : {len(converted_matches)} matchs récupérés")

        return converted_matches

    except requests.RequestException as error:
        print("❌ Erreur réseau Football-Data.org:", error)
        return []

    except Exception as error:
        print("❌ Erreur inattendue Football-Data.org:", error)
        return []


# ============================================================
# SYNCHRONISATION COMPLÈTE
# ============================================================

def sync_matches_once():
    global matches_cache

    if not sync_lock.acquire(blocking=False):
        print("⏳ Synchronisation déjà en cours...")
        return False

    try:
        print("🔄 Début synchronisation automatique...")

        matches = fetch_matches_from_football_data()

        if not matches:
            print("ℹ️ Aucun match récupéré.")
            return False

        saved = save_matches_to_supabase(matches)

        if not saved:
            print("❌ Synchronisation Supabase échouée.")
            return False

        matches_cache = {
            "data": matches,
            "timestamp": time.time()
        }

        print("✅ Synchronisation terminée.")
        return True

    except Exception as error:
        print("❌ Erreur synchronisation:", error)
        return False

    finally:
        sync_lock.release()


# ============================================================
# CACHE
# ============================================================

def get_matches_data(force_refresh=False):
    global matches_cache

    current_time = time.time()

    cache_is_valid = (
        matches_cache["data"] is not None
        and current_time - matches_cache["timestamp"] < CACHE_DURATION
    )

    if not force_refresh and cache_is_valid:
        return matches_cache["data"]

    matches = fetch_matches_from_football_data()

    if matches:
        save_matches_to_supabase(matches)

        matches_cache = {
            "data": matches,
            "timestamp": time.time()
        }

        return matches

    if matches_cache["data"] is not None:
        print("⚠️ Utilisation du dernier cache disponible.")
        return matches_cache["data"]

    return []


# ============================================================
# THREAD DE SYNCHRONISATION AUTOMATIQUE
# ============================================================

def automatic_sync_loop():
    print("🚀 Synchronisation automatique activée.")

    time.sleep(5)

    while True:
        try:
            sync_matches_once()
        except Exception as error:
            print("❌ Erreur thread automatique:", error)

        time.sleep(60)


def start_background_sync():
    global background_thread

    if background_thread is not None and background_thread.is_alive():
        return

    background_thread = threading.Thread(
        target=automatic_sync_loop,
        daemon=True,
        name="cisse-pronos-sync"
    )

    background_thread.start()

    print("🟢 Thread de synchronisation démarré.")


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
            "count": len(matches),
            "matches": matches
        })

    except Exception as error:
        print("❌ /api/matches:", error)

        return jsonify({
            "success": False,
            "error": str(error),
            "matches": []
        }), 500


# ============================================================
# API LIVE
# ============================================================

@app.route("/api/live", methods=["GET"])
def api_live():
    try:
        matches = get_matches_data(force_refresh=True)

        live_matches = [
            match for match in matches
            if match.get("statut") in ["IN_PLAY", "PAUSED"]
        ]

        return jsonify({
            "success": True,
            "source": "Football-Data.org",
            "count": len(live_matches),
            "matches": live_matches
        })

    except Exception as error:
        print("❌ /api/live:", error)

        return jsonify({
            "success": False,
            "error": str(error),
            "matches": []
        }), 500


# ============================================================
# STATISTIQUES GÉNÉRALES
# ============================================================

def calculate_statistics(matches):
    total = len(matches)

    upcoming = 0
    live = 0
    finished = 0
    cancelled = 0

    total_goals = 0
    matches_with_scores = 0

    home_wins = 0
    draws = 0
    away_wins = 0

    over_05 = 0
    over_15 = 0
    over_25 = 0
    over_35 = 0
    over_45 = 0

    btts_yes = 0

    clean_sheet_home = 0
    clean_sheet_away = 0

    zero_zero = 0

    for match in matches:
        status = match.get("statut")
        score1 = match.get("score1")
        score2 = match.get("score2")

        if status in ["IN_PLAY", "PAUSED"]:
            live += 1
        elif status == "FINISHED":
            finished += 1
        elif status == "CANCELLED":
            cancelled += 1
        elif status in ["SCHEDULED", "TIMED"]:
            upcoming += 1

        if status != "FINISHED":
            continue

        if score1 is None or score2 is None:
            continue

        try:
            score1 = int(score1)
            score2 = int(score2)
        except (ValueError, TypeError):
            continue

        matches_with_scores += 1

        goals = score1 + score2
        total_goals += goals

        if score1 > score2:
            home_wins += 1
        elif score1 == score2:
            draws += 1
        else:
            away_wins += 1

        if goals > 0:
            over_05 += 1
        if goals > 1:
            over_15 += 1
        if goals > 2:
            over_25 += 1
        if goals > 3:
            over_35 += 1
        if goals > 4:
            over_45 += 1

        if score1 > 0 and score2 > 0:
            btts_yes += 1

        if score2 == 0:
            clean_sheet_home += 1
        if score1 == 0:
            clean_sheet_away += 1

        if score1 == 0 and score2 == 0:
            zero_zero += 1

    average_goals = 0

    if matches_with_scores > 0:
        average_goals = round(total_goals / matches_with_scores, 2)

    return {
        "overview": {
            "total_matches": total,
            "upcoming": upcoming,
            "live": live,
            "finished": finished,
            "cancelled": cancelled
        },
        "goals": {
            "total_goals": total_goals,
            "matches_with_scores": matches_with_scores,
            "average_goals": average_goals
        },
        "results": {
            "home_wins": home_wins,
            "draws": draws,
            "away_wins": away_wins
        },
        "over": {
            "over_0_5": over_05,
            "over_1_5": over_15,
            "over_2_5": over_25,
            "over_3_5": over_35,
            "over_4_5": over_45
        },
        "btts": {
            "yes": btts_yes,
            "no": (matches_with_scores - btts_yes) if matches_with_scores > 0 else 0
        },
        "clean_sheets": {
            "home": clean_sheet_home,
            "away": clean_sheet_away
        },
        "zero_zero": zero_zero,
        "data_policy": {
            "real_data_only": True,
            "source": "Football-Data.org"
        }
    }


def filter_matches_for_today(matches):
    today = utc_now().date()
    result = []

    for match in matches:
        date_match = parse_utc_date(match.get("date_match"))

        if not date_match:
            continue

        if date_match.date() == today:
            result.append(match)

    return result


@app.route("/api/statistics/today", methods=["GET"])
def api_statistics_today():
    try:
        matches = get_matches_data()

        today_matches = filter_matches_for_today(matches)

        statistics = calculate_statistics(today_matches)

        statistics["competitions"] = {}

        return jsonify({
            "success": True,
            "date": utc_now().date().isoformat(),
            "source": "Football-Data.org",
            "data_policy": {
                "real_data_only": True
            },
            "statistics": statistics
        })

    except Exception as error:
        print("❌ /api/statistics/today:", error)

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500


# ============================================================
# ROUTE PRINCIPALE
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "success": True,
        "message": "CISSE PRONOS API",
        "status": "online",
        "source": "Football-Data.org",
        "automatic_sync": True,
        "sync_interval_seconds": 60
    })


# ============================================================
# DÉMARRAGE
# ============================================================

start_background_sync()


# ============================================================
# MODE LOCAL
# ============================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000))
)
