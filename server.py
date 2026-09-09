from flask import Flask, jsonify
from flask_cors import CORS

from datetime import datetime, timezone, timedelta

import requests
import os
import threading
import time


# ============================================================
# CISSE PRONOS - API FOOTBALL
# Football-Data.org -> Render -> Supabase
# ============================================================

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

CORS(app)


# ============================================================
# CONFIGURATION
# ============================================================

FOOTBALL_DATA_URL = "https://api.football-data.org/v4"

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    ""
).rstrip("/")

SUPABASE_SERVICE_ROLE_KEY = os.environ.get(
    "SUPABASE_SERVICE_ROLE_KEY",
    ""
)

FOOTBALL_DATA_TOKEN = os.environ.get(
    "FOOTBALL_DATA_TOKEN",
    ""
)

# Synchronisation automatique toutes les 60 secondes
SYNC_INTERVAL = 60


# ============================================================
# CACHE
# ============================================================

matches_cache = {
    "data": [],
    "updated_at": None
}

cache_lock = threading.Lock()

sync_thread = None


# ============================================================
# OUTILS DATE / HEURE
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
# MINUTE OFFICIELLE DU MATCH
# ============================================================

def get_match_minute(match):
    """
    IMPORTANT :

    On utilise UNIQUEMENT la minute fournie par
    Football-Data.org.

    On ne calcule plus la minute avec l'heure du coup d'envoi.

    Cela évite par exemple d'afficher 84' alors que le vrai
    match est à 68'.
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
# STATUT
# ============================================================

def convert_status(status):
    """
    Convertit le statut Football-Data.org vers les statuts
    utilisés par CISSE PRONOS.
    """

    if status in ["IN_PLAY", "PAUSED"]:
        return "LIVE"

    if status == "FINISHED":
        return "FINISHED"

    if status == "POSTPONED":
        return "POSTPONED"

    if status == "CANCELLED":
        return "CANCELLED"

    if status == "SUSPENDED":
        return "SUSPENDED"

    if status == "AWARDED":
        return "FINISHED"

    return "SCHEDULED"


# ============================================================
# CONVERSION FOOTBALL-DATA.ORG
# ============================================================

def convert_match(match):

    home_team = match.get("homeTeam") or {}
    away_team = match.get("awayTeam") or {}

    score = match.get("score") or {}
    full_time = score.get("fullTime") or {}

    status_source = match.get("status")

    statut = convert_status(status_source)

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score1 = full_time.get("home")
    score2 = full_time.get("away")

    # --------------------------------------------------------
    # MINUTE OFFICIELLE
    # --------------------------------------------------------

    minute = get_match_minute(match)

    # --------------------------------------------------------
    # COMPÉTITION
    # --------------------------------------------------------

    competition = match.get("competition") or {}

    competition_code = competition.get("code")
    competition_name = competition.get("name")

    # --------------------------------------------------------
    # DATE
    # --------------------------------------------------------

    date_match = match.get("utcDate")

    # --------------------------------------------------------
    # ID
    # --------------------------------------------------------

    match_id = match.get("id")

    if match_id is None:
        return None

    # --------------------------------------------------------
    # OBJET CISSE PRONOS
    # --------------------------------------------------------

    return {
        "id": str(match_id),

        "equipe1": home_team.get("name"),
        "equipe2": away_team.get("name"),

        "date_match": date_match,

        "statut": statut,

        # Minute réelle du fournisseur
        "minute": minute,

        "score1": score1,
        "score2": score2,

        "logo1": home_team.get("crest"),
        "logo2": away_team.get("crest"),

        # Utilisé pour les statistiques de l'API.
        # Cette donnée n'est PAS envoyée dans la table matchs
        # car elle n'existe pas dans son schéma actuel.
        "competition_code": competition_code,
        "competition_name": competition_name,

        "updated_at": iso_now()
    }


# ============================================================
# RÉCUPÉRATION FOOTBALL-DATA.ORG
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

    tomorrow = today + timedelta(days=1)

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
            "Football-Data.org HTTP "
            f"{response.status_code}: "
            f"{response.text[:1000]}"
        )

    data = response.json()

    raw_matches = data.get("matches", [])

    converted_matches = []

    for raw_match in raw_matches:

        try:

            converted = convert_match(
                raw_match
            )

            if converted:
                converted_matches.append(
                    converted
                )

        except Exception as error:

            print(
                "⚠️ Match ignoré "
                f"{raw_match.get('id')}: "
                f"{error}"
            )

    return converted_matches


# ============================================================
# HEADERS SUPABASE
# ============================================================

def supabase_headers():

    if not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY n'est pas configuré."
        )

    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,

        "Authorization":
            f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",

        "Content-Type":
            "application/json",

        "Prefer":
            "resolution=merge-duplicates,return=minimal"
    }


# ============================================================
# RÉCUPÉRER created_at EXISTANTS
# ============================================================

def get_existing_created_at(match_ids):

    if not match_ids:
        return {}

    if not SUPABASE_URL:
        raise RuntimeError(
            "SUPABASE_URL n'est pas configuré."
        )

    # PostgREST :
    # id=in.(123,456,789)

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
            "Erreur lecture Supabase "
            f"{response.status_code}: "
            f"{response.text[:1000]}"
        )

    rows = response.json()

    result = {}

    for row in rows:

        row_id = row.get("id")

        if row_id is not None:

            result[str(row_id)] = row.get(
                "created_at"
            )

    return result


# ============================================================
# ENREGISTREMENT DANS SUPABASE
# ============================================================

def save_matches_to_supabase(matches):

    if not matches:
        return

    if not SUPABASE_URL:
        raise RuntimeError(
            "SUPABASE_URL n'est pas configuré."
        )

    existing_created_at = (
        get_existing_created_at(
            [
                match["id"]
                for match in matches
            ]
        )
    )

    rows = []

    for match in matches:

        match_id = str(
            match["id"]
        )

        old_created_at = (
            existing_created_at.get(
                match_id
            )
        )

        # ----------------------------------------------------
        # IMPORTANT :
        # On utilise uniquement les colonnes présentes
        # dans public.matchs.
        #
        # La colonne "Statistiques général" existante n'est
        # jamais modifiée ici.
        # ----------------------------------------------------

        row = {

            "id":
                match["id"],

            "equipe1":
                match.get("equipe1"),

            "equipe2":
                match.get("equipe2"),

            "date_match":
                match.get("date_match"),

            "statut":
                match.get("statut"),

            "minute":
                match.get("minute"),

            "score1":
                match.get("score1"),

            "score2":
                match.get("score2"),

            "logo1":
                match.get("logo1"),

            "logo2":
                match.get("logo2"),

            "updated_at":
                match.get("updated_at")
        }

        # ----------------------------------------------------
        # created_at
        # ----------------------------------------------------

        if old_created_at:

            # Conservation de la date originale
            row["created_at"] = (
                old_created_at
            )

        else:

            # Nouveau match
            row["created_at"] = iso_now()

        rows.append(row)

    # --------------------------------------------------------
    # UPSERT
    # --------------------------------------------------------

    url = (
        f"{SUPABASE_URL}/rest/v1/matchs"
    )

    response = requests.post(
        url,
        headers=supabase_headers(),
        json=rows,
        timeout=30
    )

    if response.status_code not in [
        200,
        201,
        204
    ]:

        raise RuntimeError(
            "Erreur écriture Supabase "
            f"{response.status_code}: "
            f"{response.text[:1500]}"
        )


# ============================================================
# SYNCHRONISATION UNE FOIS
# ============================================================

def sync_matches_once():

    print(
        "🔄 Synchronisation "
        "Football-Data.org..."
    )

    matches = (
        fetch_matches_from_football_data()
    )

    print(
        f"⚽ {len(matches)} matchs récupérés."
    )

    # --------------------------------------------------------
    # SUPABASE
    # --------------------------------------------------------

    if matches:

        save_matches_to_supabase(
            matches
        )

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    with cache_lock:

        matches_cache["data"] = matches

        matches_cache["updated_at"] = (
            iso_now()
        )

    # --------------------------------------------------------
    # NOMBRE DE MATCHS LIVE
    # --------------------------------------------------------

    live_matches = [
        match
        for match in matches
        if match.get("statut") == "LIVE"
    ]

    print(
        "✅ Synchronisation terminée | "
        f"LIVE: {len(live_matches)}"
    )

    # --------------------------------------------------------
    # LOG DES MATCHS LIVE
    # --------------------------------------------------------

    for match in live_matches:

        print(
            "🔴 LIVE | "
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

    # Laisse Render démarrer correctement
    time.sleep(5)

    while True:

        try:

            sync_matches_once()

        except Exception as error:

            print(
                "❌ Erreur synchronisation "
                f"automatique: {error}"
            )

        # Nouvelle synchronisation dans 60 secondes
        time.sleep(
            SYNC_INTERVAL
        )


# ============================================================
# DÉMARRER LE THREAD
# ============================================================

def start_background_sync():

    global sync_thread

    if (
        sync_thread is None
        or not sync_thread.is_alive()
    ):

        sync_thread = threading.Thread(
            target=automatic_sync_loop,
            daemon=True,
            name="cisse-pronos-sync"
        )

        sync_thread.start()

        print(
            "🚀 Synchronisation automatique "
            f"activée: toutes les "
            f"{SYNC_INTERVAL} secondes."
        )


# ============================================================
# RÉCUPÉRER LES MATCHS
# ============================================================

def get_matches_data(
    force_refresh=False
):

    with cache_lock:

        cached_matches = (
            matches_cache["data"]
        )

    # --------------------------------------------------------
    # CACHE DISPONIBLE
    # --------------------------------------------------------

    if (
        not force_refresh
        and cached_matches
    ):

        return cached_matches

    # --------------------------------------------------------
    # PAS DE CACHE
    # --------------------------------------------------------

    try:

        return sync_matches_once()

    except Exception as error:

        print(
            "❌ Impossible de récupérer "
            f"les matchs: {error}"
        )

        # On retourne le cache s'il existe
        return cached_matches


# ============================================================
# API /api/matches
# ============================================================

@app.route(
    "/api/matches",
    methods=["GET"]
)
def api_matches():

    try:

        matches = get_matches_data()

        return jsonify({

            "success":
                True,

            "source":
                "Football-Data.org",

            "real_data_only":
                True,

            "count":
                len(matches),

            "matches":
                matches
        })

    except Exception as error:

        return jsonify({

            "success":
                False,

            "error":
                str(error),

            "source":
                "Football-Data.org",

            "real_data_only":
                True
        }), 500


# ============================================================
# API /api/live
# ============================================================

@app.route(
    "/api/live",
    methods=["GET"]
)
def api_live():

    try:

        matches = get_matches_data()

        live_matches = [
            match
            for match in matches
            if match.get("statut") == "LIVE"
        ]

        return jsonify({

            "success":
                True,

            "source":
                "Football-Data.org",

            "real_data_only":
                True,

            "count":
                len(live_matches),

            "matches":
                live_matches
        })

    except Exception as error:

        return jsonify({

            "success":
                False,

            "error":
                str(error),

            "source":
                "Football-Data.org",

            "real_data_only":
                True
        }), 500


# ============================================================
# FILTRER LES MATCHS DU JOUR
# ============================================================

def filter_matches_for_today(
    matches
):

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


# ============================================================
# STATISTIQUES
# ============================================================

def calculate_statistics(
    matches
):

    total_matches = len(
        matches
    )

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

    upcoming = sum(
        1
        for match in matches
        if match.get("statut") == "SCHEDULED"
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

    suspended = sum(
        1
        for match in matches
        if match.get("statut") == "SUSPENDED"
    )

    # --------------------------------------------------------
    # COMPÉTITIONS RÉELLES
    # --------------------------------------------------------

    competitions = {}

    for match in matches:

        code = match.get(
            "competition_code"
        )

        name = match.get(
            "competition_name"
        )

        if code:

            if code not in competitions:

                competitions[code] = {
                    "name": name,
                    "total": 0,
                    "live": 0,
                    "finished": 0,
                    "upcoming": 0
                }

            competitions[code][
                "total"
            ] += 1

            if match.get("statut") == "LIVE":

                competitions[code][
                    "live"
                ] += 1

            elif match.get("statut") == "FINISHED":

                competitions[code][
                    "finished"
                ] += 1

            elif match.get("statut") == "SCHEDULED":

                competitions[code][
                    "upcoming"
                ] += 1

    # --------------------------------------------------------
    # RÉSULTAT
    # --------------------------------------------------------

    return {

        "total_matches":
            total_matches,

        "live":
            live,

        "finished":
            finished,

        "upcoming":
            upcoming,

        "cancelled":
            cancelled,

        "postponed":
            postponed,

        "suspended":
            suspended,

        "competitions":
            competitions
    }


# ============================================================
# API /api/statistics/today
# ============================================================

@app.route(
    "/api/statistics/today",
    methods=["GET"]
)
def api_statistics_today():

    try:

        matches = get_matches_data()

        today_matches = (
            filter_matches_for_today(
                matches
            )
        )

        statistics = (
            calculate_statistics(
                today_matches
            )
        )

        return jsonify({

            "success":
                True,

            "date":
                utc_now()
                .date()
                .isoformat(),

            "source":
                "Football-Data.org",

            "data_policy": {

                "real_data_only":
                    True,

                "no_fake_statistics":
                    True
            },

            "statistics":
                statistics
        })

    except Exception as error:

        return jsonify({

            "success":
                False,

            "error":
                str(error),

            "source":
                "Football-Data.org",

            "data_policy": {

                "real_data_only":
                    True,

                "no_fake_statistics":
                    True
            }

        }), 500


# ============================================================
# ROUTE PRINCIPALE
# ============================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return jsonify({

        "success":
            True,

        "message":
            "CISSE PRONOS API",

        "source":
            "Football-Data.org",

        "real_data_only":
            True,

        "automatic_sync":
            True,

        "sync_interval_seconds":
            SYNC_INTERVAL,

        "endpoints": [

            "/api/matches",

            "/api/live",

            "/api/statistics/today"
        ]
    })


# ============================================================
# DÉMARRAGE DE LA SYNCHRONISATION
# ============================================================

start_background_sync()


# ============================================================
# LANCEMENT LOCAL
# ============================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
  )
