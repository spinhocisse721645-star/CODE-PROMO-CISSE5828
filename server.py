from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime, timezone, timedelta
import os
import requests
import threading
import time
import unicodedata


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
# CACHE MATCHS LIVE
# ============================================================

matches_cache = {
    "data": None,
    "timestamp": 0
}

CACHE_DURATION = 60


# ============================================================
# CACHE ADMIN
# ============================================================

admin_cache = {
    "competitions": {
        "data": None,
        "timestamp": 0
    },
    "teams": {},
    "team_matches": {}
}

ADMIN_COMPETITIONS_CACHE_DURATION = 600
ADMIN_TEAMS_CACHE_DURATION = 600
ADMIN_TEAM_MATCHES_CACHE_DURATION = 120

admin_cache_lock = threading.Lock()


# ============================================================
# LOCKS
# ============================================================

sync_lock = threading.Lock()
background_thread = None


# ============================================================
# PAYS AUTORISÉS POUR CISSE PRONOS
#
# IMPORTANT :
# Aucun autre pays ne doit être ajouté automatiquement.
# La Côte d'Ivoire n'est PAS dans cette liste.
# ============================================================

ADMIN_COUNTRIES = [
    {"key": "England", "name": "Angleterre", "flag": "🇬🇧"},
    {"key": "Spain", "name": "Espagne", "flag": "🇪🇸"},
    {"key": "Italy", "name": "Italie", "flag": "🇮🇹"},
    {"key": "France", "name": "France", "flag": "🇫🇷"},
    {"key": "Germany", "name": "Allemagne", "flag": "🇩🇪"},
    {"key": "Netherlands", "name": "Pays-Bas", "flag": "🇳🇱"},
    {"key": "Portugal", "name": "Portugal", "flag": "🇵🇹"},
    {"key": "Poland", "name": "Pologne", "flag": "🇵🇱"},
    {"key": "Sweden", "name": "Suède", "flag": "🇸🇪"},
    {"key": "Norway", "name": "Norvège", "flag": "🇳🇴"},
    {"key": "Turkey", "name": "Turquie", "flag": "🇹🇷"},
    {"key": "Switzerland", "name": "Suisse", "flag": "🇨🇭"},
    {"key": "Belgium", "name": "Belgique", "flag": "🇧🇪"},
    {"key": "Austria", "name": "Autriche", "flag": "🇦🇹"},
    {"key": "Denmark", "name": "Danemark", "flag": "🇩🇰"},
    {"key": "Saudi Arabia", "name": "Arabie saoudite", "flag": "🇸🇦"},
    {"key": "Czech Republic", "name": "République tchèque", "flag": "🇨🇿"},
    {"key": "Scotland", "name": "Écosse", "flag": "🏴"},
    {"key": "Greece", "name": "Grèce", "flag": "🇬🇷"},
    {"key": "Estonia", "name": "Estonie", "flag": "🇪🇪"},
    {"key": "Latvia", "name": "Lettonie", "flag": "🇱🇻"},
    {"key": "Russia", "name": "Russie", "flag": "🇷🇺"},
    {"key": "Croatia", "name": "Croatie", "flag": "🇭🇷"},
    {"key": "Finland", "name": "Finlande", "flag": "🇫🇮"},
    {"key": "Hungary", "name": "Hongrie", "flag": "🇭🇺"},
    {"key": "Cyprus", "name": "Chypre", "flag": "🇨🇾"},
    {"key": "Serbia", "name": "Serbie", "flag": "🇷🇸"},
    {"key": "Slovakia", "name": "Slovaquie", "flag": "🇸🇰"},
    {"key": "Slovenia", "name": "Slovénie", "flag": "🇸🇮"},
    {"key": "United States", "name": "États-Unis", "flag": "🇺🇸"}
]


# ============================================================
# COMPÉTITIONS EUROPÉENNES
# ============================================================

EUROPE_COMPETITIONS = {
    "uefa champions league": {
        "name": "Champions League",
        "flag": "🏆"
    },
    "uefa europa league": {
        "name": "Europa League",
        "flag": "🟠"
    },
    "uefa conference league": {
        "name": "Conference League",
        "flag": "🔵"
    }
}


# ============================================================
# ALIAS DES PAYS FOURNISSEUR
# ============================================================

ADMIN_COUNTRY_ALIASES = {
    "England": [
        "england"
    ],
    "Spain": [
        "spain"
    ],
    "Italy": [
        "italy"
    ],
    "France": [
        "france"
    ],
    "Germany": [
        "germany"
    ],
    "Netherlands": [
        "netherlands"
    ],
    "Portugal": [
        "portugal"
    ],
    "Poland": [
        "poland"
    ],
    "Sweden": [
        "sweden"
    ],
    "Norway": [
        "norway"
    ],
    "Turkey": [
        "turkey",
        "turkiye"
    ],
    "Switzerland": [
        "switzerland"
    ],
    "Belgium": [
        "belgium"
    ],
    "Austria": [
        "austria"
    ],
    "Denmark": [
        "denmark"
    ],
    "Saudi Arabia": [
        "saudi arabia"
    ],
    "Czech Republic": [
        "czech republic",
        "czechia"
    ],
    "Scotland": [
        "scotland"
    ],
    "Greece": [
        "greece"
    ],
    "Estonia": [
        "estonia"
    ],
    "Latvia": [
        "latvia"
    ],
    "Russia": [
        "russia",
        "russian federation"
    ],
    "Croatia": [
        "croatia"
    ],
    "Finland": [
        "finland"
    ],
    "Hungary": [
        "hungary"
    ],
    "Cyprus": [
        "cyprus"
    ],
    "Serbia": [
        "serbia"
    ],
    "Slovakia": [
        "slovakia"
    ],
    "Slovenia": [
        "slovenia"
    ],
    "United States": [
        "united states",
        "united states of america"
    ]
}


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


def safe_int(value):
    """
    Convertit une valeur en entier si possible.
    Retourne None si la valeur n'est pas disponible.
    """
    if value is None:
        return None

    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def normalize_text(value):
    """
    Normalise un texte pour comparer les noms de pays
    et de compétitions sans problème d'accents/majuscules.
    """

    value = str(value or "").strip().lower()

    value = unicodedata.normalize(
        "NFD",
        value
    )

    value = "".join(
        char
        for char in value
        if unicodedata.category(char) != "Mn"
    )

    return value


# ============================================================
# REQUÊTE FOOTBALL-DATA.ORG
# ============================================================

def football_data_request(
    path,
    params=None,
    timeout=30
):
    """
    Fonction centrale pour les requêtes Football-Data.org.

    Le token reste uniquement côté Render.
    """

    if not FOOTBALL_DATA_TOKEN:
        return None, {
            "status": 500,
            "message": "FOOTBALL_DATA_TOKEN manquant"
        }

    url = f"{FOOTBALL_DATA_URL}/{path.lstrip('/')}"

    headers = {
        "X-Auth-Token": FOOTBALL_DATA_TOKEN
    }

    try:

        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=timeout
        )

        if response.status_code == 200:

            try:
                return response.json(), None

            except ValueError:
                return None, {
                    "status": 502,
                    "message": "Réponse Football-Data.org invalide"
                }

        if response.status_code == 429:

            print(
                "⚠️ Football-Data.org : limite de requêtes atteinte."
            )

            return None, {
                "status": 429,
                "message": "Limite de requêtes Football-Data.org atteinte"
            }

        print(
            "❌ Football-Data.org:",
            response.status_code
        )

        print(response.text[:1000])

        return None, {
            "status": response.status_code,
            "message": response.text
        }

    except requests.RequestException as error:

        print(
            "❌ Erreur réseau Football-Data.org:",
            error
        )

        return None, {
            "status": 503,
            "message": "Erreur réseau Football-Data.org"
        }


# ============================================================
# MINUTE DU MATCH
#
# IMPORTANT :
#
# Football-Data.org (plan gratuit) ne fournit pas de minute
# réelle dans ses réponses. On utilise donc une estimation
# basée sur le temps écoulé depuis le coup d'envoi (utcDate),
# tout en donnant la priorité à une vraie valeur si jamais
# l'API venait à en fournir une un jour.
# ============================================================

def calculate_match_minute_fallback(match):
    """
    Estimation basée sur le temps écoulé depuis le coup d'envoi.
    Utilisée car Football-Data.org (plan gratuit) ne fournit
    pas de minute réelle.
    """

    status = match.get("status")

    if status not in ["IN_PLAY", "PAUSED"]:
        return None

    utc_date = match.get("utcDate")

    if not utc_date:
        return None

    kickoff = parse_utc_date(utc_date)

    if not kickoff:
        return None

    try:
        now = utc_now()

        elapsed_seconds = (now - kickoff).total_seconds()

        if elapsed_seconds < 0:
            return None

        elapsed_minutes = int(elapsed_seconds // 60)

        if status == "PAUSED":
            return min(elapsed_minutes, 45)

        if elapsed_minutes < 1:
            return 1

        if elapsed_minutes > 90:
            return 90

        return elapsed_minutes

    except (ValueError, TypeError):
        return None


def get_match_minute(match):

    status = match.get("status")

    if status not in [
        "IN_PLAY",
        "PAUSED"
    ]:
        return None

    # Priorité à la vraie donnée si jamais l'API la fournit un jour
    real_minute = match.get("minute")

    if real_minute is not None:
        try:
            return int(real_minute)
        except (ValueError, TypeError):
            pass

    # Sinon, estimation basée sur le temps écoulé
    return calculate_match_minute_fallback(match)


# ============================================================
# TEMPS ADDITIONNEL
# ============================================================

def get_injury_time(match):

    injury_time = match.get("injuryTime")

    if injury_time is None:
        return None

    try:
        return int(injury_time)

    except (ValueError, TypeError):
        return None


# ============================================================
# COMPÉTITION
# ============================================================

def get_competition_data(match):

    competition = match.get("competition") or {}

    if not isinstance(competition, dict):
        competition = {}

    # Football-Data peut placer area dans competition
    # ou directement dans le match.
    area = (
        competition.get("area")
        or match.get("area")
        or {}
    )

    if not isinstance(area, dict):
        area = {}

    return {
        "id": competition.get("id"),
        "name": competition.get("name"),
        "code": competition.get("code"),
        "type": competition.get("type"),
        "emblem": competition.get("emblem"),
        "country": area.get("name")
    }


# ============================================================
# CONVERSION FOOTBALL-DATA -> CISSE PRONOS
# ============================================================

def convert_match(match):

    home_team = match.get("homeTeam") or {}
    away_team = match.get("awayTeam") or {}

    if not isinstance(home_team, dict):
        home_team = {}

    if not isinstance(away_team, dict):
        away_team = {}

    score = match.get("score") or {}

    if not isinstance(score, dict):
        score = {}

    full_time = score.get("fullTime") or {}

    if not isinstance(full_time, dict):
        full_time = {}

    regular_time = score.get("regularTime") or {}

    if not isinstance(regular_time, dict):
        regular_time = {}

    home_score = full_time.get("home")
    away_score = full_time.get("away")

    if home_score is None:
        home_score = regular_time.get("home")

    if away_score is None:
        away_score = regular_time.get("away")

    home_score = safe_int(home_score)
    away_score = safe_int(away_score)

    home_name = home_team.get("name")
    away_name = away_team.get("name")

    home_short_name = home_team.get("shortName")
    away_short_name = away_team.get("shortName")

    home_tla = home_team.get("tla")
    away_tla = away_team.get("tla")

    home_logo = home_team.get("crest")
    away_logo = away_team.get("crest")

    utc_date = match.get("utcDate")
    status = match.get("status")

    minute = get_match_minute(match)
    injury_time = get_injury_time(match)

    competition = get_competition_data(match)

    converted = {
        "id": str(match.get("id")),
        "equipe1": home_name,
        "equipe2": away_name,
        "shortName1": home_short_name,
        "shortName2": away_short_name,
        "tla1": home_tla,
        "tla2": away_tla,
        "date_match": utc_date,
        "statut": status,
        "minute": minute,
        "injuryTime": injury_time,
        "score1": home_score,
        "score2": away_score,
        "logo1": home_logo,
        "logo2": away_logo,
        "competition": competition,
        "season": match.get("season"),
        "matchday": match.get("matchday"),
        "stage": match.get("stage"),
        "group": match.get("group"),
        "venue": match.get("venue"),
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

    ids_string = ",".join(
        str(match_id)
        for match_id in match_ids
    )

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

            print(
                "❌ Erreur récupération created_at:",
                response.status_code,
                response.text
            )

            return {}

        rows = response.json()

        result = {}

        for row in rows:

            match_id = str(
                row.get("id")
            )

            result[match_id] = row.get(
                "created_at"
            )

        return result

    except requests.RequestException as error:

        print(
            "❌ Erreur réseau Supabase:",
            error
        )

        return {}


# ============================================================
# SAUVEGARDE MATCHS LIVE DANS SUPABASE
#
# IMPORTANT :
#
# On conserve uniquement les colonnes existantes.
# "Statistiques général" n'est pas touché.
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

    existing_created_at = get_existing_created_at(
        match_ids
    )

    rows = []

    now = utc_now().isoformat()

    for match in matches:

        match_id = str(
            match.get("id")
        )

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

            row["created_at"] = (
                existing_created_at[match_id]
            )

        else:

            row["created_at"] = now

        rows.append(row)

    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": (
            f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"
        ),
        "Content-Type": "application/json",
        "Prefer": (
            "resolution=merge-duplicates,"
            "return=minimal"
        )
    }

    try:

        response = requests.post(
            SUPABASE_MATCHS_URL,
            headers=headers,
            json=rows,
            timeout=30
        )

        if response.status_code not in [
            200,
            201,
            204
        ]:

            print(
                "❌ Erreur Supabase upsert:",
                response.status_code
            )

            print(response.text)

            return False

        print(
            f"✅ {len(rows)} matchs synchronisés avec Supabase"
        )

        return True

    except requests.RequestException as error:

        print(
            "❌ Erreur réseau pendant la sauvegarde Supabase:",
            error
        )

        return False


# ============================================================
# FOOTBALL-DATA.ORG — MATCHS DU JOUR + DEMAIN
# ============================================================

def fetch_matches_from_football_data():

    if not FOOTBALL_DATA_TOKEN:
        print("❌ FOOTBALL_DATA_TOKEN manquant")
        return []

    today = utc_now().date()

    tomorrow = today + timedelta(
        days=1
    )

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

            print(
                "⚠️ Football-Data.org : "
                "limite de requêtes atteinte."
            )

            return []

        if response.status_code != 200:

            print(
                "❌ Football-Data.org:",
                response.status_code
            )

            print(response.text)

            return []

        data = response.json()

        matches = data.get(
            "matches",
            []
        )

        if not isinstance(
            matches,
            list
        ):

            print(
                "⚠️ Réponse Football-Data.org invalide."
            )

            return []

        converted_matches = []

        for match in matches:

            try:

                converted = convert_match(
                    match
                )

                converted_matches.append(
                    converted
                )

            except Exception as error:

                print(
                    "⚠️ Impossible de convertir un match:",
                    error
                )

        print(
            f"⚽ Football-Data.org : "
            f"{len(converted_matches)} matchs récupérés"
        )

        live_count = 0

        for match in converted_matches:

            if match.get("statut") in [
                "IN_PLAY",
                "PAUSED"
            ]:

                live_count += 1

        if live_count > 0:

            print(
                f"🔴 Matchs LIVE détectés : "
                f"{live_count}"
            )

        return converted_matches

    except requests.RequestException as error:

        print(
            "❌ Erreur réseau Football-Data.org:",
            error
        )

        return []

    except Exception as error:

        print(
            "❌ Erreur inattendue Football-Data.org:",
            error
        )

        return []


# ============================================================
# SYNCHRONISATION COMPLÈTE
# ============================================================

def sync_matches_once():

    global matches_cache

    if not sync_lock.acquire(
        blocking=False
    ):

        print(
            "⏳ Synchronisation déjà en cours..."
        )

        return False

    try:

        print(
            "🔄 Début synchronisation automatique..."
        )

        matches = (
            fetch_matches_from_football_data()
        )

        if not matches:

            print(
                "ℹ️ Aucun match récupéré."
            )

            return False

        saved = save_matches_to_supabase(
            matches
        )

        if not saved:

            print(
                "❌ Synchronisation Supabase échouée."
            )

            return False

        matches_cache = {
            "data": matches,
            "timestamp": time.time()
        }

        print(
            "✅ Synchronisation terminée."
        )

        return True

    except Exception as error:

        print(
            "❌ Erreur synchronisation:",
            error
        )

        return False

    finally:

        sync_lock.release()


# ============================================================
# CACHE MATCHS
# ============================================================

def get_matches_data(
    force_refresh=False
):

    global matches_cache

    current_time = time.time()

    cache_is_valid = (
        matches_cache["data"] is not None
        and (
            current_time
            - matches_cache["timestamp"]
        ) < CACHE_DURATION
    )

    if (
        not force_refresh
        and cache_is_valid
    ):

        return matches_cache["data"]

    matches = (
        fetch_matches_from_football_data()
    )

    if matches:

        save_matches_to_supabase(
            matches
        )

        matches_cache = {
            "data": matches,
            "timestamp": time.time()
        }

        return matches

    if matches_cache["data"] is not None:

        print(
            "⚠️ Utilisation du dernier cache disponible."
        )

        return matches_cache["data"]

    return []


# ============================================================
# THREAD SYNCHRONISATION AUTOMATIQUE
# ============================================================

def automatic_sync_loop():

    print(
        "🚀 Synchronisation automatique activée."
    )

    time.sleep(5)

    while True:

        try:

            sync_matches_once()

        except Exception as error:

            print(
                "❌ Erreur thread automatique:",
                error
            )

        time.sleep(60)


def start_background_sync():

    global background_thread

    if (
        background_thread is not None
        and background_thread.is_alive()
    ):

        return

    background_thread = threading.Thread(
        target=automatic_sync_loop,
        daemon=True,
        name="cisse-pronos-sync"
    )

    background_thread.start()

    print(
        "🟢 Thread de synchronisation démarré."
    )


# ============================================================
# ============================================================
# ADMIN — OUTILS CACHE
# ============================================================
# ============================================================

def admin_cache_is_valid(
    entry,
    duration
):

    if not entry:
        return False

    if entry.get("data") is None:
        return False

    return (
        time.time()
        - entry.get("timestamp", 0)
    ) < duration


# ============================================================
# ADMIN — NORMALISER UNE COMPÉTITION
# ============================================================

def normalize_admin_competition(
    competition
):

    area = competition.get(
        "area"
    ) or {}

    if not isinstance(
        area,
        dict
    ):

        area = {}

    current_season = competition.get(
        "currentSeason"
    ) or {}

    if not isinstance(
        current_season,
        dict
    ):

        current_season = {}

    return {
        "id": competition.get("id"),
        "code": competition.get("code"),
        "name": competition.get("name"),
        "type": competition.get("type"),
        "emblem": competition.get("emblem"),
        "plan": competition.get("plan"),

        "area": {
            "id": area.get("id"),
            "name": area.get("name"),
            "code": area.get("code"),
            "flag": area.get("flag")
        },

        "currentSeason": {
            "id": current_season.get("id"),
            "startDate": current_season.get(
                "startDate"
            ),
            "endDate": current_season.get(
                "endDate"
            ),
            "currentMatchday": current_season.get(
                "currentMatchday"
            )
        }
        if current_season
        else None
    }


# ============================================================
# ADMIN — COMPÉTITIONS
#
# GET /api/admin/competitions
#
# Retourne UNIQUEMENT les pays sélectionnés
# et les 3 compétitions européennes demandées.
#
# Aucun club fictif.
# ============================================================

@app.route(
    "/api/admin/competitions",
    methods=["GET"]
)
def api_admin_competitions():

    cache_entry = admin_cache[
        "competitions"
    ]

    if admin_cache_is_valid(
        cache_entry,
        ADMIN_COMPETITIONS_CACHE_DURATION
    ):

        return jsonify(
            cache_entry["data"]
        )

    data, error = football_data_request(
        "/competitions"
    )

    if error:

        if cache_entry["data"] is not None:

            cached = dict(
                cache_entry["data"]
            )

            cached["cached"] = True

            return jsonify(cached)

        return jsonify({
            "success": False,
            "source": "Football-Data.org",
            "real_data_only": True,
            "error": error["message"]
        }), error["status"]

    provider_competitions = data.get(
        "competitions",
        []
    )

    if not isinstance(
        provider_competitions,
        list
    ):

        provider_competitions = []

    country_competitions = {
        country["key"]: []
        for country in ADMIN_COUNTRIES
    }

    europe_competitions = []

    for competition in provider_competitions:

        if not isinstance(
            competition,
            dict
        ):

            continue

        normalized = normalize_admin_competition(
            competition
        )

        area_name = normalize_text(
            normalized["area"]["name"]
        )

        competition_name = normalize_text(
            normalized["name"]
        )

        # ----------------------------------------------------
        # COMPÉTITIONS NATIONALES
        # ----------------------------------------------------

        matched_country = None

        for country_key, aliases in (
            ADMIN_COUNTRY_ALIASES.items()
        ):

            normalized_aliases = {
                normalize_text(alias)
                for alias in aliases
            }

            if area_name in normalized_aliases:

                matched_country = country_key
                break

        if matched_country is not None:

            country_competitions[
                matched_country
            ].append(
                normalized
            )

            continue

        # ----------------------------------------------------
        # COMPÉTITIONS EUROPÉENNES
        # ----------------------------------------------------

        if competition_name in EUROPE_COMPETITIONS:

            europe_info = EUROPE_COMPETITIONS[
                competition_name
            ]

            normalized["displayName"] = (
                europe_info["name"]
            )

            normalized["displayFlag"] = (
                europe_info["flag"]
            )

            europe_competitions.append(
                normalized
            )

    # --------------------------------------------------------
    # CONSTRUIRE LES 30 PAYS DANS L'ORDRE DEMANDÉ
    # --------------------------------------------------------

    countries = []

    for country in ADMIN_COUNTRIES:

        competitions = country_competitions[
            country["key"]
        ]

        competitions.sort(
            key=lambda item: normalize_text(
                item.get("name")
            )
        )

        countries.append({
            "key": country["key"],
            "name": country["name"],
            "flag": country["flag"],

            # Nombre réel de compétitions
            # trouvées chez Football-Data.org
            "count": len(competitions),

            "competitions": competitions
        })

    europe_competitions.sort(
        key=lambda item: normalize_text(
            item.get("name")
        )
    )

    result = {
        "success": True,
        "source": "Football-Data.org",
        "real_data_only": True,
        "cached": False,

        "countries": countries,

        "europe": {
            "key": "Europe",
            "name": "Europe",
            "flag": "🌍",
            "count": len(
                europe_competitions
            ),
            "competitions": europe_competitions
        }
    }

    admin_cache[
        "competitions"
    ] = {
        "data": result,
        "timestamp": time.time()
    }

    return jsonify(result)


# ============================================================
# ADMIN — ÉQUIPES D'UNE COMPÉTITION
#
# GET /api/admin/competitions/<competition_id>/teams
#
# Exemple :
# /api/admin/competitions/PL/teams
#
# Les clubs viennent directement de Football-Data.org.
# ============================================================

@app.route(
    "/api/admin/competitions/<competition_id>/teams",
    methods=["GET"]
)
def api_admin_competition_teams(
    competition_id
):

    competition_id = str(
        competition_id
    ).strip()

    if not competition_id:

        return jsonify({
            "success": False,
            "error": "competition_id manquant"
        }), 400

    cache_key = competition_id

    cache_entry = admin_cache[
        "teams"
    ].get(
        cache_key
    )

    if admin_cache_is_valid(
        cache_entry,
        ADMIN_TEAMS_CACHE_DURATION
    ):

        cached = dict(
            cache_entry["data"]
        )

        cached["cached"] = True

        return jsonify(cached)

    data, error = football_data_request(
        f"/competitions/{competition_id}/teams",
        params=None
    )

    if error:

        if (
            cache_entry
            and cache_entry.get("data")
        ):

            cached = dict(
                cache_entry["data"]
            )

            cached["cached"] = True

            return jsonify(cached)

        return jsonify({
            "success": False,
            "source": "Football-Data.org",
            "real_data_only": True,
            "competition_id": competition_id,
            "error": error["message"]
        }), error["status"]

    provider_teams = data.get(
        "teams",
        []
    )

    if not isinstance(
        provider_teams,
        list
    ):

        provider_teams = []

    teams = []

    for team in provider_teams:

        if not isinstance(
            team,
            dict
        ):

            continue

        area = team.get(
            "area"
        ) or {}

        if not isinstance(
            area,
            dict
        ):

            area = {}

        teams.append({
            "id": team.get("id"),
            "name": team.get("name"),
            "shortName": team.get(
                "shortName"
            ),
            "tla": team.get(
                "tla"
            ),
            "crest": team.get(
                "crest"
            ),

            "area": {
                "id": area.get("id"),
                "name": area.get("name"),
                "code": area.get("code"),
                "flag": area.get("flag")
            }
        })

    teams.sort(
        key=lambda team: normalize_text(
            team.get("name")
        )
    )

    season = data.get(
        "season"
    )

    result = {
        "success": True,
        "source": "Football-Data.org",
        "real_data_only": True,
        "cached": False,

        "competition_id": competition_id,

        "count": len(teams),

        "season": season,

        "teams": teams
    }

    admin_cache[
        "teams"
    ][cache_key] = {
        "data": result,
        "timestamp": time.time()
    }

    return jsonify(result)


# ============================================================
# ADMIN — MATCHS D'UN CLUB
#
# GET /api/admin/teams/<team_id>/matches
#
# Les matchs viennent directement de Football-Data.org.
#
# Aucun enregistrement dans public.matchs.
# Cela reste séparé du système MATCHS LIVE.
# ============================================================

@app.route(
    "/api/admin/teams/<int:team_id>/matches",
    methods=["GET"]
)
def api_admin_team_matches(
    team_id
):

    # --------------------------------------------------------
    # PARAMÈTRES OPTIONNELS
    # --------------------------------------------------------

    date_from = request.args.get(
        "dateFrom"
    )

    date_to = request.args.get(
        "dateTo"
    )

    competitions = request.args.get(
        "competitions"
    )

    status = request.args.get(
        "status"
    )

    limit = request.args.get(
        "limit"
    )

    # --------------------------------------------------------
    # CLÉ CACHE
    # --------------------------------------------------------

    cache_key = (
        f"{team_id}|"
        f"{date_from or ''}|"
        f"{date_to or ''}|"
        f"{competitions or ''}|"
        f"{status or ''}|"
        f"{limit or ''}"
    )

    cache_entry = admin_cache[
        "team_matches"
    ].get(
        cache_key
    )

    if admin_cache_is_valid(
        cache_entry,
        ADMIN_TEAM_MATCHES_CACHE_DURATION
    ):

        cached = dict(
            cache_entry["data"]
        )

        cached["cached"] = True

        return jsonify(cached)

    # --------------------------------------------------------
    # PARAMÈTRES FOOTBALL-DATA
    # --------------------------------------------------------

    params = {}

    if date_from:
        params["dateFrom"] = date_from

    if date_to:
        params["dateTo"] = date_to

    if competitions:
        params["competitions"] = competitions

    if status:
        params["status"] = status

    if limit:

        try:

            parsed_limit = int(
                limit
            )

            if parsed_limit > 0:
                params["limit"] = min(
                    parsed_limit,
                    100
                )

        except ValueError:

            return jsonify({
                "success": False,
                "error": "limit invalide"
            }), 400

    # --------------------------------------------------------
    # REQUÊTE RÉELLE
    # --------------------------------------------------------

    data, error = football_data_request(
        f"/teams/{team_id}/matches",
        params=params
    )

    if error:

        if (
            cache_entry
            and cache_entry.get("data")
        ):

            cached = dict(
                cache_entry["data"]
            )

            cached["cached"] = True

            return jsonify(cached)

        return jsonify({
            "success": False,
            "source": "Football-Data.org",
            "real_data_only": True,
            "team_id": team_id,
            "error": error["message"]
        }), error["status"]

    provider_matches = data.get(
        "matches",
        []
    )

    if not isinstance(
        provider_matches,
        list
    ):

        provider_matches = []

    matches = []

    for raw_match in provider_matches:

        if not isinstance(
            raw_match,
            dict
        ):

            continue

        try:

            converted = convert_match(
                raw_match
            )

            matches.append(
                converted
            )

        except Exception as error_convert:

            print(
                "⚠️ Erreur conversion match Admin:",
                error_convert
            )

    # --------------------------------------------------------
    # TRI CHRONOLOGIQUE
    # --------------------------------------------------------

    matches.sort(
        key=lambda match: (
            parse_utc_date(
                match.get("date_match")
            )
            or datetime.max.replace(
                tzinfo=timezone.utc
            )
        )
    )

    result = {
        "success": True,
        "source": "Football-Data.org",
        "real_data_only": True,
        "cached": False,

        "team_id": team_id,

        "count": len(matches),

        "matches": matches
    }

    admin_cache[
        "team_matches"
    ][cache_key] = {
        "data": result,
        "timestamp": time.time()
    }

    return jsonify(result)


# ============================================================
# API MATCHS
# ============================================================

@app.route(
    "/api/matches",
    methods=["GET"]
)
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

        print(
            "❌ /api/matches:",
            error
        )

        return jsonify({
            "success": False,
            "error": str(error),
            "matches": []
        }), 500


# ============================================================
# API LIVE
# ============================================================

@app.route(
    "/api/live",
    methods=["GET"]
)
def api_live():

    try:

        matches = get_matches_data(
            force_refresh=True
        )

        live_matches = [
            match
            for match in matches
            if match.get("statut") in [
                "IN_PLAY",
                "PAUSED"
            ]
        ]

        return jsonify({
            "success": True,
            "source": "Football-Data.org",
            "count": len(
                live_matches
            ),
            "matches": live_matches
        })

    except Exception as error:

        print(
            "❌ /api/live:",
            error
        )

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

        status = match.get(
            "statut"
        )

        score1 = match.get(
            "score1"
        )

        score2 = match.get(
            "score2"
        )

        if status in [
            "IN_PLAY",
            "PAUSED"
        ]:

            live += 1

        elif status == "FINISHED":

            finished += 1

        elif status == "CANCELLED":

            cancelled += 1

        elif status in [
            "SCHEDULED",
            "TIMED"
        ]:

            upcoming += 1

        if status != "FINISHED":
            continue

        if (
            score1 is None
            or score2 is None
        ):

            continue

        try:

            score1 = int(score1)
            score2 = int(score2)

        except (
            ValueError,
            TypeError
        ):

            continue

        matches_with_scores += 1

        goals = (
            score1
            + score2
        )

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

        if (
            score1 > 0
            and score2 > 0
        ):

            btts_yes += 1

        if score2 == 0:

            clean_sheet_home += 1

        if score1 == 0:

            clean_sheet_away += 1

        if (
            score1 == 0
            and score2 == 0
        ):

            zero_zero += 1

    average_goals = 0

    if matches_with_scores > 0:

        average_goals = round(
            total_goals
            / matches_with_scores,
            2
        )

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
            "matches_with_scores": (
                matches_with_scores
            ),
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
            "no": (
                matches_with_scores
                - btts_yes
            )
            if matches_with_scores > 0
            else 0
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


# ============================================================
# MATCHS DU JOUR
# ============================================================

def filter_matches_for_today(
    matches
):

    today = utc_now().date()

    result = []

    for match in matches:

        date_match = parse_utc_date(
            match.get("date_match")
        )

        if not date_match:
            continue

        if date_match.date() == today:

            result.append(
                match
            )

    return result


# ============================================================
# STATISTIQUES DU JOUR
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

        statistics[
            "competitions"
        ] = {}

        return jsonify({

            "success": True,

            "date": (
                utc_now()
                .date()
                .isoformat()
            ),

            "source": (
                "Football-Data.org"
            ),

            "data_policy": {
                "real_data_only": True
            },

            "statistics": statistics
        })

    except Exception as error:

        print(
            "❌ /api/statistics/today:",
            error
        )

        return jsonify({
            "success": False,
            "error": str(error)
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

        "success": True,

        "message": (
            "CISSE PRONOS API"
        ),

        "status": "online",

        "source": (
            "Football-Data.org"
        ),

        "automatic_sync": True,

        "sync_interval_seconds": 60,

        "data_policy": {
            "real_data_only": True
        },

        "admin_catalog": {

            "competitions":
                "/api/admin/competitions",

            "competition_teams":
                "/api/admin/competitions/{competition_id}/teams",

            "team_matches":
                "/api/admin/teams/{team_id}/matches"
        }
    })


# ============================================================
# DÉMARRAGE SYNCHRONISATION
# ============================================================

start_background_sync()


# ============================================================
# MODE LOCAL
# ============================================================

if __name__ == "__main__":

    app.run(

        host="0.0.0.0",

        port=int(
            os.getenv(
                "PORT",
                5000
            )
        )
  )
