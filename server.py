from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime, timezone, timedelta
import os
import requests
import threading
import time
import unicodedata


# ============================================================
# CISSE PRONOS API
# API-FOOTBALL VERSION
# ============================================================

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

CORS(
    app,
    resources={r"/api/*": {"origins": "*"}},
    supports_credentials=False
)


# ============================================================
# CONFIGURATION
# ============================================================

API_FOOTBALL_URL = "https://v3.football.api-sports.io"

API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv(
    "SUPABASE_SERVICE_ROLE_KEY",
    ""
).strip()

SUPABASE_MATCHS_URL = (
    f"{SUPABASE_URL}/rest/v1/matchs"
    if SUPABASE_URL
    else ""
)


# ============================================================
# PARAMÈTRES DE SYNCHRONISATION
# ============================================================

# Avec une clé gratuite API-Football, il faut éviter de
# multiplier inutilement les appels.
#
# 900 secondes = 15 minutes.
#
# Pour un abonnement supérieur, cette valeur pourra être
# réduite plus tard.
SYNC_INTERVAL_SECONDS = int(
    os.getenv("SYNC_INTERVAL_SECONDS", "900")
)

MATCHES_CACHE_SECONDS = int(
    os.getenv("MATCHES_CACHE_SECONDS", "120")
)

ADMIN_COMPETITIONS_CACHE_SECONDS = int(
    os.getenv("ADMIN_COMPETITIONS_CACHE_SECONDS", "3600")
)

ADMIN_TEAMS_CACHE_SECONDS = int(
    os.getenv("ADMIN_TEAMS_CACHE_SECONDS", "1800")
)

ADMIN_TEAM_MATCHES_CACHE_SECONDS = int(
    os.getenv("ADMIN_TEAM_MATCHES_CACHE_SECONDS", "300")
)


# ============================================================
# PAYS AUTORISÉS
# ============================================================

ALLOWED_COUNTRIES = [
    "England",
    "Spain",
    "Italy",
    "France",
    "Germany",
    "Netherlands",
    "Portugal",
    "Poland",
    "Sweden",
    "Norway",
    "Turkey",
    "Switzerland",
    "Belgium",
    "Austria",
    "Denmark",
    "Saudi Arabia",
    "Czech Republic",
    "Scotland",
    "Greece",
    "Estonia",
    "Latvia",
    "Russia",
    "Croatia",
    "Finland",
    "Hungary",
    "Cyprus",
    "Serbia",
    "Slovakia",
    "Slovenia",
    "United States"
]


# ============================================================
# COMPÉTITIONS EUROPÉENNES AUTORISÉES
# ============================================================

EUROPE_COMPETITIONS = {
    "UEFA Champions League",
    "UEFA Europa League",
    "UEFA Europa Conference League"
}


# ============================================================
# COMPÉTITIONS NATIONALES PRINCIPALES
#
# On utilise des alias car le nom peut légèrement varier
# selon la version/les données API-Football.
# ============================================================

NATIONAL_COMPETITION_ALIASES = {

    "England": {
        "Premier League",
        "Championship"
    },

    "Spain": {
        "La Liga"
    },

    "Italy": {
        "Serie A"
    },

    "France": {
        "Ligue 1"
    },

    "Germany": {
        "Bundesliga"
    },

    "Netherlands": {
        "Eredivisie"
    },

    "Portugal": {
        "Primeira Liga"
    },

    "Poland": {
        "Ekstraklasa"
    },

    "Sweden": {
        "Allsvenskan"
    },

    "Norway": {
        "Eliteserien"
    },

    "Turkey": {
        "Super Lig",
        "Süper Lig"
    },

    "Switzerland": {
        "Super League"
    },

    "Belgium": {
        "Jupiler Pro League",
        "Pro League"
    },

    "Austria": {
        "Bundesliga"
    },

    "Denmark": {
        "Superliga"
    },

    "Saudi Arabia": {
        "Saudi Pro League",
        "Pro League"
    },

    "Czech Republic": {
        "Czech Liga",
        "1. Liga"
    },

    "Scotland": {
        "Premiership"
    },

    "Greece": {
        "Super League 1",
        "Super League"
    },

    "Estonia": {
        "Meistriliiga"
    },

    "Latvia": {
        "Virsliga"
    },

    "Russia": {
        "Premier League"
    },

    "Croatia": {
        "HNL"
    },

    "Finland": {
        "Veikkausliiga"
    },

    "Hungary": {
        "NB I"
    },

    "Cyprus": {
        "1. Division"
    },

    "Serbia": {
        "Super Liga"
    },

    "Slovakia": {
        "Super Liga"
    },

    "Slovenia": {
        "1. SNL"
    },

    "United States": {
        "Major League Soccer",
        "MLS"
    }
}


# ============================================================
# ALIAS DES PAYS API-FOOTBALL
# ============================================================

COUNTRY_ALIASES = {
    "england": "England",
    "spain": "Spain",
    "italy": "Italy",
    "france": "France",
    "germany": "Germany",
    "netherlands": "Netherlands",
    "portugal": "Portugal",
    "poland": "Poland",
    "sweden": "Sweden",
    "norway": "Norway",
    "turkey": "Turkey",
    "switzerland": "Switzerland",
    "belgium": "Belgium",
    "austria": "Austria",
    "denmark": "Denmark",
    "saudiarabia": "Saudi Arabia",
    "czechrepublic": "Czech Republic",
    "czechia": "Czech Republic",
    "scotland": "Scotland",
    "greece": "Greece",
    "estonia": "Estonia",
    "latvia": "Latvia",
    "russia": "Russia",
    "croatia": "Croatia",
    "finland": "Finland",
    "hungary": "Hungary",
    "cyprus": "Cyprus",
    "serbia": "Serbia",
    "slovakia": "Slovakia",
    "slovenia": "Slovenia",
    "unitedstates": "United States",
    "usa": "United States",
    "us": "United States"
}


# ============================================================
# CACHE
# ============================================================

matches_cache = {
    "timestamp": 0,
    "data": []
}

live_cache = {
    "timestamp": 0,
    "data": []
}

admin_competitions_cache = {
    "timestamp": 0,
    "data": None
}

admin_teams_cache = {}

admin_team_matches_cache = {}

competition_context_cache = {}

team_context_cache = {}

provider_leagues_cache = {
    "timestamp": 0,
    "data": []
}


# ============================================================
# LOCK
# ============================================================

cache_lock = threading.Lock()


# ============================================================
# OUTILS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def today_utc():
    return now_utc().date()


def normalize_text(value):
    if value is None:
        return ""

    value = str(value)

    value = unicodedata.normalize(
        "NFD",
        value
    )

    value = "".join(
        c for c in value
        if unicodedata.category(c) != "Mn"
    )

    return (
        value
        .lower()
        .replace("-", "")
        .replace("_", "")
        .replace(" ", "")
        .replace(".", "")
        .replace("'", "")
        .replace("’", "")
    )


def normalize_country(provider_country):
    if not provider_country:
        return None

    key = normalize_text(provider_country)

    return COUNTRY_ALIASES.get(key)


def safe_int(value, default=None):
    try:
        return int(value)
    except Exception:
        return default


def api_key_available():
    return bool(API_FOOTBALL_KEY)


# ============================================================
# API-FOOTBALL REQUEST
# ============================================================

def api_football_request(
    endpoint,
    params=None,
    timeout=30
):
    if not API_FOOTBALL_KEY:
        return None, (
            "La variable API_FOOTBALL_KEY n'est pas configurée "
            "dans Render."
        )

    url = (
        f"{API_FOOTBALL_URL.rstrip('/')}/"
        f"{endpoint.lstrip('/')}"
    )

    headers = {
        "x-apisports-key": API_FOOTBALL_KEY,
        "Accept": "application/json"
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            params=params or {},
            timeout=timeout
        )

    except requests.RequestException as exc:
        return None, (
            f"Erreur réseau API-Football: {str(exc)}"
        )

    try:
        data = response.json()
    except Exception:
        return None, (
            f"API-Football a retourné une réponse "
            f"non JSON. HTTP {response.status_code}"
        )

    if response.status_code != 200:
        return None, (
            f"API-Football HTTP {response.status_code}: "
            f"{data}"
        )

    errors = data.get("errors")

    if errors:
        return None, (
            f"API-Football errors: {errors}"
        )

    return data, None


# ============================================================
# CHARGER TOUTES LES COMPÉTITIONS API-FOOTBALL
# ============================================================

def get_provider_leagues(force_refresh=False):

    with cache_lock:

        cache_valid = (
            provider_leagues_cache["data"]
            and
            (
                time.time()
                - provider_leagues_cache["timestamp"]
                <
                ADMIN_COMPETITIONS_CACHE_SECONDS
            )
        )

        if cache_valid and not force_refresh:
            return provider_leagues_cache["data"], None

    data, error = api_football_request(
        "/leagues",
        params={}
    )

    if error:
        return [], error

    leagues = data.get("response", [])

    with cache_lock:
        provider_leagues_cache["data"] = leagues
        provider_leagues_cache["timestamp"] = time.time()

    return leagues, None


# ============================================================
# SAISON COURANTE D'UNE COMPÉTITION
# ============================================================

def get_current_season_from_league(
    league_item
):
    seasons = league_item.get("seasons") or []

    if not seasons:
        return datetime.now().year

    current = [
        s for s in seasons
        if s.get("current") is True
    ]

    if current:
        return current[-1].get(
            "year",
            datetime.now().year
        )

    years = []

    for season in seasons:
        year = safe_int(
            season.get("year")
        )

        if year:
            years.append(year)

    if years:
        return max(years)

    return datetime.now().year


# ============================================================
# VÉRIFIER SI UNE COMPÉTITION EST AUTORISÉE
# ============================================================

def competition_is_allowed(
    league,
    country_name
):

    league_name = (
        league.get("name") or ""
    ).strip()

    # --------------------------------------------------------
    # Compétitions européennes
    # --------------------------------------------------------

    if league_name in EUROPE_COMPETITIONS:
        return True

    # --------------------------------------------------------
    # Pays non autorisé
    # --------------------------------------------------------

    if country_name not in ALLOWED_COUNTRIES:
        return False

    # --------------------------------------------------------
    # Compétitions nationales autorisées
    # --------------------------------------------------------

    allowed_names = (
        NATIONAL_COMPETITION_ALIASES
        .get(country_name, set())
    )

    normalized_name = normalize_text(
        league_name
    )

    for allowed_name in allowed_names:

        if (
            normalized_name
            ==
            normalize_text(allowed_name)
        ):
            return True

    return False


# ============================================================
# CONSTRUIRE LE CATALOGUE ADMIN
# ============================================================

def build_admin_catalog(force_refresh=False):

    with cache_lock:

        if (
            admin_competitions_cache["data"]
            and
            not force_refresh
            and
            (
                time.time()
                -
                admin_competitions_cache["timestamp"]
                <
                ADMIN_COMPETITIONS_CACHE_SECONDS
            )
        ):
            return admin_competitions_cache["data"], None

    leagues, error = get_provider_leagues(
        force_refresh=force_refresh
    )

    if error:
        return None, error

    countries_map = {}

    # --------------------------------------------------------
    # Initialiser TOUS les pays autorisés.
    #
    # Cela permet à l'Admin d'afficher 0 seulement si
    # API-Football ne possède aucune compétition correspondante.
    # --------------------------------------------------------

    for country in ALLOWED_COUNTRIES:

        countries_map[country] = {
            "name": country,
            "count": 0,
            "flag": None,
            "competitions": []
        }

    # --------------------------------------------------------
    # Parcourir les compétitions API-Football
    # --------------------------------------------------------

    for item in leagues:

        league = item.get("league") or {}
        country = item.get("country") or {}

        league_id = league.get("id")
        league_name = league.get("name")

        if not league_id or not league_name:
            continue

        provider_country = (
            country.get("name")
            or ""
        )

        admin_country = normalize_country(
            provider_country
        )

        # ----------------------------------------------------
        # Europe
        # ----------------------------------------------------

        if league_name in EUROPE_COMPETITIONS:

            season = get_current_season_from_league(
                item
            )

            competition = {
                "id": league_id,
                "name": league_name,
                "code": str(league_id),
                "type": league.get("type"),
                "emblem": league.get("logo"),
                "country": {
                    "name": provider_country,
                    "code": country.get("code"),
                    "flag": country.get("flag")
                },
                "season": season,
                "source": "API-Football"
            }

            key = "__EUROPE__"

            if key not in countries_map:

                countries_map[key] = {
                    "name": "Europe",
                    "count": 0,
                    "flag": None,
                    "competitions": []
                }

            countries_map[key][
                "competitions"
            ].append(competition)

            countries_map[key]["count"] += 1

            competition_context_cache[
                str(league_id)
            ] = {
                "season": season,
                "country": "Europe",
                "name": league_name
            }

            continue

        # ----------------------------------------------------
        # Pays
        # ----------------------------------------------------

        if not admin_country:
            continue

        if admin_country not in ALLOWED_COUNTRIES:
            continue

        if not competition_is_allowed(
            league,
            admin_country
        ):
            continue

        season = get_current_season_from_league(
            item
        )

        competition = {
            "id": league_id,
            "name": league_name,
            "code": str(league_id),
            "type": league.get("type"),
            "emblem": league.get("logo"),
            "country": {
                "name": provider_country,
                "code": country.get("code"),
                "flag": country.get("flag")
            },
            "season": season,
            "source": "API-Football"
        }

        countries_map[
            admin_country
        ]["competitions"].append(
            competition
        )

        countries_map[
            admin_country
        ]["count"] += 1

        if not countries_map[
            admin_country
        ].get("flag"):

            countries_map[
                admin_country
            ]["flag"] = country.get("flag")

        competition_context_cache[
            str(league_id)
        ] = {
            "season": season,
            "country": admin_country,
            "name": league_name
        }

    # --------------------------------------------------------
    # Supprimer les pays sans compétition
    # seulement si souhaité.
    #
    # Ici on LES CONSERVE afin que l'Admin voie clairement
    # les pays sélectionnés.
    # --------------------------------------------------------

    countries = []

    # Europe d'abord
    europe = countries_map.get("__EUROPE__")

    if europe and europe["competitions"]:

        europe["competitions"].sort(
            key=lambda x: x["name"]
        )

        countries.append({
            "name": "Europe",
            "count": europe["count"],
            "flag": None,
            "competitions": europe["competitions"]
        })

    # Puis les pays dans l'ordre voulu
    for country in ALLOWED_COUNTRIES:

        data = countries_map[country]

        data["competitions"].sort(
            key=lambda x: x["name"]
        )

        countries.append(data)

    result = {
        "source": "API-Football",
        "updated_at": now_utc().isoformat(),
        "countries": countries
    }

    with cache_lock:
        admin_competitions_cache[
            "data"
        ] = result

        admin_competitions_cache[
            "timestamp"
        ] = time.time()

    return result, None


# ============================================================
# CONVERTIR UNE COMPÉTITION
# ============================================================

def normalize_admin_competition(
    competition
):

    if not competition:
        return None

    league_id = competition.get("id")

    context = competition_context_cache.get(
        str(league_id),
        {}
    )

    season = (
        competition.get("season")
        or context.get("season")
        or datetime.now().year
    )

    return {
        "id": league_id,
        "name": competition.get("name"),
        "code": str(league_id),
        "type": competition.get("type"),
        "emblem": competition.get("emblem"),
        "country": competition.get("country"),
        "season": season,
        "source": "API-Football"
    }


# ============================================================
# CONVERTIR UNE ÉQUIPE
# ============================================================

def convert_team(
    item,
    competition_id,
    season
):

    team = item.get("team") or {}
    venue = item.get("venue") or {}

    team_id = team.get("id")

    if not team_id:
        return None

    # Conserver le contexte pour /teams/<id>/matches
    team_context_cache[
        str(team_id)
    ] = {
        "competition_id": competition_id,
        "season": season
    }

    return {
        "id": team_id,
        "name": team.get("name"),
        "shortName": (
            team.get("code")
            or team.get("name")
        ),
        "tla": team.get("code"),
        "logo": team.get("logo"),
        "country": team.get("country"),
        "founded": team.get("founded"),
        "national": team.get("national"),
        "venue": {
            "id": venue.get("id"),
            "name": venue.get("name"),
            "city": venue.get("city"),
            "capacity": venue.get("capacity"),
            "image": venue.get("image")
        },
        "competition_id": competition_id,
        "season": season,
        "source": "API-Football"
    }


# ============================================================
# CONVERTIR UN MATCH API-FOOTBALL
# ============================================================

def convert_fixture(
    fixture_item
):

    fixture = fixture_item.get("fixture") or {}
    league = fixture_item.get("league") or {}
    teams = fixture_item.get("teams") or {}
    goals = fixture_item.get("goals") or {}
    score = fixture_item.get("score") or {}

    fixture_id = fixture.get("id")

    if not fixture_id:
        return None

    home = teams.get("home") or {}
    away = teams.get("away") or {}

    status = fixture.get("status") or {}

    status_short = (
        status.get("short")
        or ""
    ).upper()

    elapsed = status.get("elapsed")
    extra = status.get("extra")

    # --------------------------------------------------------
    # Statut CISSE PRONOS
    # --------------------------------------------------------

    live_statuses = {
        "1H",
        "2H",
        "ET",
        "P",
        "LIVE"
    }

    paused_statuses = {
        "HT",
        "BT",
        "INT"
    }

    finished_statuses = {
        "FT",
        "AET",
        "PEN"
    }

    cancelled_statuses = {
        "CANC",
        "ABD",
        "AWD",
        "WO"
    }

    postponed_statuses = {
        "PST",
        "SUSP"
    }

    if status_short in live_statuses:
        statut = "IN_PLAY"

    elif status_short in paused_statuses:
        statut = "PAUSED"

    elif status_short in finished_statuses:
        statut = "FINISHED"

    elif status_short in cancelled_statuses:
        statut = "CANCELLED"

    elif status_short in postponed_statuses:
        statut = "POSTPONED"

    else:
        statut = "SCHEDULED"

    # --------------------------------------------------------
    # Minute
    # --------------------------------------------------------

    minute = None

    if elapsed is not None:
        minute = elapsed

    # --------------------------------------------------------
    # Scores
    # --------------------------------------------------------

    score_home = goals.get("home")
    score_away = goals.get("away")

    # API peut retourner null avant le match
    if score_home is None:
        score_home = (
            score.get("fulltime") or {}
        ).get("home")

    if score_away is None:
        score_away = (
            score.get("fulltime") or {}
        ).get("away")

    # --------------------------------------------------------
    # Compétition
    # --------------------------------------------------------

    competition = {
        "id": league.get("id"),
        "name": league.get("name"),
        "code": str(
            league.get("id")
            or ""
        ),
        "type": league.get("type"),
        "emblem": league.get("logo"),
        "country": league.get("country")
    }

    # --------------------------------------------------------
    # Date
    # --------------------------------------------------------

    date_match = fixture.get("date")

    # --------------------------------------------------------
    # Stade
    # --------------------------------------------------------

    venue = fixture.get("venue") or {}

    # --------------------------------------------------------
    # Matchday / round
    # --------------------------------------------------------

    round_name = league.get("round")

    return {
        "id": fixture_id,

        "equipe1": home.get("name"),
        "equipe2": away.get("name"),

        "shortName1": home.get("name"),
        "shortName2": away.get("name"),

        "tla1": home.get("winner"),
        "tla2": away.get("winner"),

        "date_match": date_match,

        "statut": statut,

        "status": status_short,

        "minute": minute,

        "injuryTime": extra,

        "score1": score_home,
        "score2": score_away,

        "logo1": home.get("logo"),
        "logo2": away.get("logo"),

        "competition": competition,

        "competition_id": league.get("id"),

        "season": league.get("season"),

        "matchday": round_name,

        "stage": round_name,

        "group": None,

        "venue": venue.get("name"),

        "venue_city": venue.get("city"),

        "updated_at": now_utc().isoformat(),

        "source": "API-Football"
    }


# ============================================================
# OBTENIR LES IDS DES COMPÉTITIONS AUTORISÉES
# ============================================================

def get_allowed_competition_ids():

    catalog, error = build_admin_catalog()

    if error or not catalog:
        return set()

    ids = set()

    for country in catalog.get(
        "countries",
        []
    ):

        for competition in country.get(
            "competitions",
            []
        ):

            competition_id = competition.get(
                "id"
            )

            if competition_id:
                ids.add(
                    int(competition_id)
                )

    return ids


# ============================================================
# MATCHES DU JOUR + DEMAIN
# ============================================================

def fetch_matches_from_api_football():

    today = today_utc()

    tomorrow = today + timedelta(days=1)

    params = {
        "from": today.isoformat(),
        "to": tomorrow.isoformat(),
        "timezone": "UTC"
    }

    data, error = api_football_request(
        "/fixtures",
        params=params,
        timeout=40
    )

    if error:
        print(
            "[API-FOOTBALL] Erreur fixtures:",
            error
        )

        return [], error

    fixtures = data.get(
        "response",
        []
    )

    allowed_ids = get_allowed_competition_ids()

    matches = []

    for item in fixtures:

        league = item.get(
            "league"
        ) or {}

        league_id = league.get(
            "id"
        )

        if not league_id:
            continue

        if int(league_id) not in allowed_ids:
            continue

        match = convert_fixture(
            item
        )

        if match:
            matches.append(match)

    matches.sort(
        key=lambda m: (
            m.get("date_match")
            or ""
        )
    )

    print(
        f"[API-FOOTBALL] "
        f"{len(matches)} matchs récupérés."
    )

    return matches, None


# ============================================================
# SYNCHRONISER LES MATCHS
# ============================================================

def sync_matches_once():

    global matches_cache
    global live_cache

    matches, error = (
        fetch_matches_from_api_football()
    )

    if error:
        return False, error

    # --------------------------------------------------------
    # Cache général
    # --------------------------------------------------------

    with cache_lock:

        matches_cache[
            "data"
        ] = matches

        matches_cache[
            "timestamp"
        ] = time.time()

    # --------------------------------------------------------
    # Cache LIVE dérivé des mêmes données
    #
    # Pas besoin d'une deuxième requête API.
    # --------------------------------------------------------

    live_matches = [
        match
        for match in matches
        if match.get("statut")
        in {
            "IN_PLAY",
            "PAUSED"
        }
    ]

    with cache_lock:

        live_cache[
            "data"
        ] = live_matches

        live_cache[
            "timestamp"
        ] = time.time()

    # --------------------------------------------------------
    # Supabase
    # --------------------------------------------------------

    save_matches_to_supabase(
        matches
    )

    return True, None


# ============================================================
# SUPABASE
# ============================================================

def get_existing_created_at(
    match_id
):

    if not SUPABASE_MATCHS_URL:
        return None

    if not SUPABASE_SERVICE_ROLE_KEY:
        return None

    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": (
            f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"
        )
    }

    params = {
        "id": f"eq.{match_id}",
        "select": "created_at",
        "limit": 1
    }

    try:

        response = requests.get(
            SUPABASE_MATCHS_URL,
            headers=headers,
            params=params,
            timeout=15
        )

        if response.status_code != 200:
            return None

        data = response.json()

        if data:
            return data[0].get(
                "created_at"
            )

    except Exception as exc:

        print(
            "[SUPABASE] "
            f"Erreur lecture created_at: {exc}"
        )

    return None


def save_matches_to_supabase(
    matches
):

    if not SUPABASE_MATCHS_URL:
        print(
            "[SUPABASE] "
            "SUPABASE_URL non configurée."
        )
        return False

    if not SUPABASE_SERVICE_ROLE_KEY:
        print(
            "[SUPABASE] "
            "SUPABASE_SERVICE_ROLE_KEY non configurée."
        )
        return False

    if not matches:
        return True

    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": (
            f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"
        ),
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }

    rows = []

    for match in matches:

        row = {
            "id": match.get("id"),
            "equipe1": match.get("equipe1"),
            "equipe2": match.get("equipe2"),
            "short_name1": match.get(
                "shortName1"
            ),
            "short_name2": match.get(
                "shortName2"
            ),
            "tla1": match.get("tla1"),
            "tla2": match.get("tla2"),
            "date_match": match.get(
                "date_match"
            ),
            "statut": match.get(
                "statut"
            ),
            "minute": match.get(
                "minute"
            ),
            "injury_time": match.get(
                "injuryTime"
            ),
            "score1": match.get(
                "score1"
            ),
            "score2": match.get(
                "score2"
            ),
            "logo1": match.get(
                "logo1"
            ),
            "logo2": match.get(
                "logo2"
            ),
            "competition": (
                match.get(
                    "competition"
                ) or {}
            ).get("name"),
            "season": match.get(
                "season"
            ),
            "matchday": match.get(
                "matchday"
            ),
            "stage": match.get(
                "stage"
            ),
            "group_name": match.get(
                "group"
            ),
            "venue": match.get(
                "venue"
            ),
            "updated_at": match.get(
                "updated_at"
            )
        }

        # Supprimer les valeurs None seulement si nécessaire.
        rows.append(row)

    try:

        response = requests.post(
            SUPABASE_MATCHS_URL,
            headers=headers,
            json=rows,
            timeout=30
        )

        if response.status_code not in (
            200,
            201,
            204
        ):

            print(
                "[SUPABASE] "
                f"Erreur {response.status_code}: "
                f"{response.text[:1000]}"
            )

            return False

        print(
            f"[SUPABASE] "
            f"{len(rows)} matchs synchronisés."
        )

        return True

    except requests.RequestException as exc:

        print(
            "[SUPABASE] "
            f"Erreur réseau: {exc}"
        )

        return False


# ============================================================
# BOUCLE DE SYNCHRONISATION AUTOMATIQUE
# ============================================================

def automatic_sync_loop():

    print(
        "[SYNC] "
        "Boucle API-Football démarrée."
    )

    time.sleep(5)

    while True:

        try:

            if not api_key_available():

                print(
                    "[SYNC] "
                    "API_FOOTBALL_KEY absente."
                )

            else:

                success, error = (
                    sync_matches_once()
                )

                if not success:

                    print(
                        "[SYNC] "
                        f"Échec: {error}"
                    )

        except Exception as exc:

            print(
                "[SYNC] "
                f"Erreur inattendue: {exc}"
            )

        time.sleep(
            SYNC_INTERVAL_SECONDS
        )


# ============================================================
# ADMIN — COMPÉTITIONS
# ============================================================

@app.route(
    "/api/admin/competitions",
    methods=["GET"]
)
def admin_competitions():

    force = (
        request.args.get(
            "refresh",
            "0"
        )
        == "1"
    )

    data, error = build_admin_catalog(
        force_refresh=force
    )

    if error:

        return jsonify({
            "success": False,
            "error": error,
            "source": "API-Football"
        }), 502

    return jsonify({
        "success": True,
        **data
    })


# ============================================================
# ADMIN — ÉQUIPES D'UNE COMPÉTITION
# ============================================================

@app.route(
    "/api/admin/competitions/<int:competition_id>/teams",
    methods=["GET"]
)
def admin_competition_teams(
    competition_id
):

    cache_key = str(
        competition_id
    )

    cached = admin_teams_cache.get(
        cache_key
    )

    if cached:

        if (
            time.time()
            -
            cached["timestamp"]
            <
            ADMIN_TEAMS_CACHE_SECONDS
        ):

            return jsonify({
                "success": True,
                "source": "API-Football",
                "competition_id": competition_id,
                "season": cached["season"],
                "teams": cached["teams"]
            })

    # --------------------------------------------------------
    # Trouver la saison
    # --------------------------------------------------------

    context = competition_context_cache.get(
        cache_key
    )

    season = None

    if context:
        season = context.get(
            "season"
        )

    # --------------------------------------------------------
    # Si le cache de contexte n'existe plus après redémarrage,
    # récupérer la compétition directement.
    # --------------------------------------------------------

    if not season:

        data, error = api_football_request(
            "/leagues",
            params={
                "id": competition_id
            }
        )

        if error:

            return jsonify({
                "success": False,
                "error": error
            }), 502

        response = data.get(
            "response",
            []
        )

        if response:

            season = (
                get_current_season_from_league(
                    response[0]
                )
            )

        else:

            season = datetime.now().year

    # --------------------------------------------------------
    # API-Football /teams
    # --------------------------------------------------------

    data, error = api_football_request(
        "/teams",
        params={
            "league": competition_id,
            "season": season
        },
        timeout=40
    )

    if error:

        return jsonify({
            "success": False,
            "error": error,
            "competition_id": competition_id
        }), 502

    provider_teams = data.get(
        "response",
        []
    )

    teams = []

    for item in provider_teams:

        team = convert_team(
            item,
            competition_id,
            season
        )

        if team:
            teams.append(team)

    teams.sort(
        key=lambda team: (
            team.get("name")
            or ""
        ).lower()
    )

    admin_teams_cache[
        cache_key
    ] = {
        "timestamp": time.time(),
        "season": season,
        "teams": teams
    }

    return jsonify({
        "success": True,
        "source": "API-Football",
        "competition_id": competition_id,
        "season": season,
        "teams": teams
    })


# ============================================================
# ADMIN — MATCHS D'UNE ÉQUIPE
# ============================================================

@app.route(
    "/api/admin/teams/<int:team_id>/matches",
    methods=["GET"]
)
def admin_team_matches(
    team_id
):

    # --------------------------------------------------------
    # Paramètres facultatifs
    # --------------------------------------------------------

    competition_id = request.args.get(
        "competition_id",
        type=int
    )

    season_param = request.args.get(
        "season",
        type=int
    )

    date_from = request.args.get(
        "dateFrom"
    )

    date_to = request.args.get(
        "dateTo"
    )

    limit = request.args.get(
        "limit",
        default=100,
        type=int
    )

    if limit > 100:
        limit = 100

    # --------------------------------------------------------
    # Contexte enregistré quand les équipes ont été chargées
    # --------------------------------------------------------

    context = team_context_cache.get(
        str(team_id),
        {}
    )

    if not competition_id:
        competition_id = context.get(
            "competition_id"
        )

    season = (
        season_param
        or context.get("season")
        or datetime.now().year
    )

    # --------------------------------------------------------
    # Cache
    # --------------------------------------------------------

    cache_key = (
        f"{team_id}:"
        f"{competition_id}:"
        f"{season}:"
        f"{date_from}:"
        f"{date_to}:"
        f"{limit}"
    )

    cached = admin_team_matches_cache.get(
        cache_key
    )

    if cached:

        if (
            time.time()
            -
            cached["timestamp"]
            <
            ADMIN_TEAM_MATCHES_CACHE_SECONDS
        ):

            return jsonify({
                "success": True,
                "source": "API-Football",
                "team_id": team_id,
                "season": season,
                "matches": cached["matches"]
            })

    # --------------------------------------------------------
    # Paramètres API-Football
    # --------------------------------------------------------

    params = {
        "team": team_id,
        "season": season
    }

    if competition_id:
        params["league"] = competition_id

    if date_from:
        params["from"] = date_from

    if date_to:
        params["to"] = date_to

    data, error = api_football_request(
        "/fixtures",
        params=params,
        timeout=40
    )

    if error:

        return jsonify({
            "success": False,
            "error": error,
            "team_id": team_id
        }), 502

    provider_matches = data.get(
        "response",
        []
    )

    matches = []

    for item in provider_matches:

        match = convert_fixture(
            item
        )

        if match:
            matches.append(match)

    matches.sort(
        key=lambda match: (
            match.get("date_match")
            or ""
        )
    )

    matches = matches[:limit]

    admin_team_matches_cache[
        cache_key
    ] = {
        "timestamp": time.time(),
        "matches": matches
    }

    return jsonify({
        "success": True,
        "source": "API-Football",
        "team_id": team_id,
        "season": season,
        "matches": matches
    })


# ============================================================
# MATCHS PUBLICS
# ============================================================

@app.route(
    "/api/matches",
    methods=["GET"]
)
def get_matches():

    force = (
        request.args.get(
            "refresh",
            "0"
        )
        == "1"
    )

    with cache_lock:

        cache_is_valid = (
            matches_cache["data"]
            and
            (
                time.time()
                -
                matches_cache["timestamp"]
                <
                MATCHES_CACHE_SECONDS
            )
        )

        if (
            cache_is_valid
            and
            not force
        ):

            return jsonify({
                "success": True,
                "source": "API-Football",
                "count": len(
                    matches_cache["data"]
                ),
                "matches": matches_cache["data"]
            })

    success, error = sync_matches_once()

    if not success:

        # Si une ancienne donnée existe,
        # on la renvoie plutôt que de casser le site.

        with cache_lock:

            if matches_cache["data"]:

                return jsonify({
                    "success": True,
                    "source": "API-Football",
                    "stale": True,
                    "error": error,
                    "count": len(
                        matches_cache["data"]
                    ),
                    "matches": matches_cache["data"]
                })

        return jsonify({
            "success": False,
            "error": error,
            "source": "API-Football"
        }), 502

    with cache_lock:

        matches = matches_cache["data"]

    return jsonify({
        "success": True,
        "source": "API-Football",
        "count": len(matches),
        "matches": matches
    })


# ============================================================
# LIVE
# ============================================================

@app.route(
    "/api/live",
    methods=["GET"]
)
def get_live():

    with cache_lock:

        cache_is_valid = (
            live_cache["data"]
            and
            (
                time.time()
                -
                live_cache["timestamp"]
                <
                MATCHES_CACHE_SECONDS
            )
        )

        if cache_is_valid:

            return jsonify({
                "success": True,
                "source": "API-Football",
                "count": len(
                    live_cache["data"]
                ),
                "matches": live_cache["data"]
            })

    # --------------------------------------------------------
    # Pas de deuxième appel API live.
    #
    # On utilise la synchronisation générale.
    # --------------------------------------------------------

    success, error = sync_matches_once()

    if not success:

        with cache_lock:

            if live_cache["data"]:

                return jsonify({
                    "success": True,
                    "source": "API-Football",
                    "stale": True,
                    "error": error,
                    "count": len(
                        live_cache["data"]
                    ),
                    "matches": live_cache["data"]
                })

        return jsonify({
            "success": False,
            "error": error,
            "source": "API-Football"
        }), 502

    with cache_lock:

        live_matches = live_cache["data"]

    return jsonify({
        "success": True,
        "source": "API-Football",
        "count": len(live_matches),
        "matches": live_matches
    })


# ============================================================
# STATISTIQUES
# ============================================================

def calculate_statistics(
    matches
):

    total = len(matches)

    live = 0
    upcoming = 0
    finished = 0
    cancelled = 0
    postponed = 0

    total_goals = 0

    home_wins = 0
    away_wins = 0
    draws = 0

    over_05 = 0
    over_15 = 0
    over_25 = 0
    over_35 = 0
    over_45 = 0

    btts = 0

    clean_sheet_home = 0
    clean_sheet_away = 0

    zero_zero = 0

    for match in matches:

        statut = match.get(
            "statut"
        )

        score1 = match.get(
            "score1"
        )

        score2 = match.get(
            "score2"
        )

        if statut in {
            "IN_PLAY",
            "PAUSED"
        }:
            live += 1

        elif statut == "FINISHED":
            finished += 1

        elif statut == "CANCELLED":
            cancelled += 1

        elif statut == "POSTPONED":
            postponed += 1

        else:
            upcoming += 1

        # ----------------------------------------------------
        # Statistiques uniquement si scores numériques
        # ----------------------------------------------------

        if (
            isinstance(score1, int)
            and
            isinstance(score2, int)
        ):

            goals = (
                score1
                +
                score2
            )

            total_goals += goals

            if score1 > score2:
                home_wins += 1

            elif score2 > score1:
                away_wins += 1

            else:
                draws += 1

            if goals >= 1:
                over_05 += 1

            if goals >= 2:
                over_15 += 1

            if goals >= 3:
                over_25 += 1

            if goals >= 4:
                over_35 += 1

            if goals >= 5:
                over_45 += 1

            if (
                score1 > 0
                and
                score2 > 0
            ):
                btts += 1

            if score2 == 0:
                clean_sheet_home += 1

            if score1 == 0:
                clean_sheet_away += 1

            if (
                score1 == 0
                and
                score2 == 0
            ):
                zero_zero += 1

    return {
        "total": total,

        "upcoming": upcoming,

        "live": live,

        "finished": finished,

        "cancelled": cancelled,

        "postponed": postponed,

        "goals": total_goals,

        "result": {
            "home": home_wins,
            "draw": draws,
            "away": away_wins
        },

        "over": {
            "0.5": over_05,
            "1.5": over_15,
            "2.5": over_25,
            "3.5": over_35,
            "4.5": over_45
        },

        "btts": btts,

        "clean_sheets": {
            "home": clean_sheet_home,
            "away": clean_sheet_away
        },

        "zero_zero": zero_zero
    }


@app.route(
    "/api/statistics/today",
    methods=["GET"]
)
def statistics_today():

    with cache_lock:
        matches = list(
            matches_cache["data"]
        )

    if not matches:

        success, error = sync_matches_once()

        if not success:

            return jsonify({
                "success": False,
                "error": error
            }), 502

        with cache_lock:
            matches = list(
                matches_cache["data"]
            )

    stats = calculate_statistics(
        matches
    )

    return jsonify({
        "success": True,
        "source": "API-Football",
        "updated_at": now_utc().isoformat(),
        "statistics": stats
    })


# ============================================================
# HEALTH CHECK / RACINE
# ============================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return jsonify({
        "name": "CISSE PRONOS API",

        "status": "online",

        "source": "API-Football",

        "provider": {
            "name": "API-Football",
            "configured": api_key_available()
        },

        "supabase": {
            "configured": bool(
                SUPABASE_URL
                and
                SUPABASE_SERVICE_ROLE_KEY
            )
        },

        "routes": [
            "/api/admin/competitions",
            "/api/admin/competitions/<competition_id>/teams",
            "/api/admin/teams/<team_id>/matches",
            "/api/matches",
            "/api/live",
            "/api/statistics/today"
        ],

        "updated_at": now_utc().isoformat()
    })


# ============================================================
# HEALTH
# ============================================================

@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return jsonify({
        "status": "ok",
        "service": "CISSE PRONOS API",
        "provider": "API-Football",
        "api_key_configured": api_key_available(),
        "supabase_configured": bool(
            SUPABASE_URL
            and
            SUPABASE_SERVICE_ROLE_KEY
        ),
        "timestamp": now_utc().isoformat()
    })


# ============================================================
# SYNCHRONISATION AU DÉMARRAGE
# ============================================================

def start_background_sync():

    thread = threading.Thread(
        target=automatic_sync_loop,
        daemon=True
    )

    thread.start()


# ============================================================
# START
# ============================================================

start_background_sync()


if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "5000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
)
