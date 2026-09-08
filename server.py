from flask import Flask, jsonify
from datetime import datetime, timezone, timedelta
import os
import requests


# ============================================================
# APPLICATION
# ============================================================

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False


# ============================================================
# CONFIGURATION
# ============================================================

FOOTBALL_DATA_URL = "https://api.football-data.org/v4"

CACHE_DURATION_SECONDS = 60

matches_cache = {
    "timestamp": None,
    "data": None
}


# ============================================================
# OUTILS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def parse_utc_date(value):
    """
    Transforme une date ISO Football-Data.org en datetime UTC.
    Retourne None si la date est invalide.
    """
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        ).astimezone(timezone.utc)

    except (ValueError, TypeError):
        return None


def calculate_match_minute(match):
    """
    Calcule une minute approximative à partir de l'heure
    officielle du coup d'envoi.

    IMPORTANT :
    - aucun chiffre inventé pour les matchs programmés
    - maximum 90
    - PAUSED est limité à 45
    """

    status = match.get("status")

    if status in [
        "SCHEDULED",
        "TIMED",
        "POSTPONED",
        "CANCELLED"
    ]:
        return 0

    if status in [
        "FINISHED",
        "SUSPENDED",
        "AWARDED"
    ]:
        return 0

    if status not in [
        "IN_PLAY",
        "PAUSED"
    ]:
        return 0

    utc_date = match.get("utcDate")

    if not utc_date:
        return 0

    kickoff = parse_utc_date(utc_date)

    if kickoff is None:
        return 0

    now = utc_now()

    elapsed_seconds = (
        now - kickoff
    ).total_seconds()

    if elapsed_seconds < 0:
        return 0

    elapsed_minutes = int(
        elapsed_seconds // 60
    )

    if status == "PAUSED":
        return min(elapsed_minutes, 45)

    if elapsed_minutes < 1:
        return 1

    if elapsed_minutes > 90:
        return 90

    return elapsed_minutes


# ============================================================
# CONVERSION D'UN MATCH FOOTBALL-DATA.ORG
# ============================================================

def convert_match(match):
    """
    Transforme un match Football-Data.org en ligne
    compatible avec la table Supabase public.matchs.
    """

    home_team = match.get("homeTeam") or {}
    away_team = match.get("awayTeam") or {}

    score = match.get("score") or {}
    full_time = score.get("fullTime") or {}

    home_score = full_time.get("home")
    away_score = full_time.get("away")

    status = match.get("status")

    minute = calculate_match_minute(match)

    return {
        "id": match.get("id"),

        "equipe1": home_team.get("name"),
        "equipe2": away_team.get("name"),

        "logo1": home_team.get("crest"),
        "logo2": away_team.get("crest"),

        "date_match": match.get("utcDate"),

        "statut": status,

        "minute": minute,

        # IMPORTANT :
        # On conserve None si aucun score réel n'est disponible.
        # Un match programmé ne devient donc pas artificiellement 0-0.
        "score1": home_score,
        "score2": away_score,

        "created_at": utc_now().isoformat(),
        "updated_at": utc_now().isoformat()
    }


# ============================================================
# SAUVEGARDE SUPABASE
# ============================================================

def save_matches_to_supabase(data):
    """
    Sauvegarde les matchs réels dans public.matchs.
    """

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not supabase_key:
        return {
            "success": False,
            "message": "Variables Supabase manquantes."
        }

    matches = data.get("matches", [])

    if not matches:
        return {
            "success": True,
            "message": "Aucun match à sauvegarder.",
            "count": 0
        }

    rows = []

    for match in matches:

        try:
            row = convert_match(match)

            if not row["id"]:
                continue

            rows.append(row)

        except Exception:
            continue

    if not rows:
        return {
            "success": True,
            "message": "Aucun match valide à sauvegarder.",
            "count": 0
        }

    url = (
        supabase_url.rstrip("/")
        + "/rest/v1/matchs"
    )

    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }

    try:

        response = requests.post(
            url,
            headers=headers,
            json=rows,
            timeout=20
        )

        if response.status_code >= 400:

            return {
                "success": False,
                "status_code": response.status_code,
                "message": response.text
            }

        return {
            "success": True,
            "count": len(rows)
        }

    except requests.RequestException as error:

        return {
            "success": False,
            "message": str(error)
        }


# ============================================================
# RÉCUPÉRATION FOOTBALL-DATA.ORG
# ============================================================

def fetch_matches_from_football_data():
    """
    Récupère les matchs d'aujourd'hui et de demain.
    """

    token = os.getenv("FOOTBALL_DATA_TOKEN")

    if not token:
        return None, {
            "success": False,
            "message": "FOOTBALL_DATA_TOKEN manquant."
        }

    today = utc_now().date()

    tomorrow = today + timedelta(days=1)

    date_from = today.isoformat()
    date_to = tomorrow.isoformat()

    url = f"{FOOTBALL_DATA_URL}/matches"

    headers = {
        "X-Auth-Token": token
    }

    params = {
        "dateFrom": date_from,
        "dateTo": date_to
    }

    try:

        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=30
        )

        if response.status_code >= 400:

            return None, {
                "success": False,
                "status_code": response.status_code,
                "message": response.text
            }

        data = response.json()

        return data, {
            "success": True
        }

    except requests.RequestException as error:

        return None, {
            "success": False,
            "message": str(error)
        }


# ============================================================
# CACHE
# ============================================================

def get_matches_data(force_refresh=False):
    """
    Retourne les données Football-Data.org.

    Cache de 60 secondes.
    """

    global matches_cache

    now = utc_now()

    if not force_refresh:

        if (
            matches_cache["timestamp"] is not None
            and matches_cache["data"] is not None
        ):

            elapsed = (
                now - matches_cache["timestamp"]
            ).total_seconds()

            if elapsed < CACHE_DURATION_SECONDS:

                return (
                    matches_cache["data"],
                    {
                        "from_cache": True
                    }
                )

    data, result = fetch_matches_from_football_data()

    if data is None:

        return None, result

    matches_cache["timestamp"] = now
    matches_cache["data"] = data

    return (
        data,
        {
            "from_cache": False
        }
    )


# ============================================================
# STATISTIQUES
# ============================================================

def percentage(value, total):
    """
    Calcule un pourcentage uniquement si le dénominateur
    est supérieur à zéro.
    """

    if total <= 0:
        return None

    return round(
        (value / total) * 100,
        2
    )


def calculate_statistics(matches):
    """
    Calcule les statistiques à partir des vrais matchs.

    Aucun score, aucune statistique et aucun résultat
    n'est inventé.
    """

    total_matches = len(matches)

    live = 0
    finished = 0
    upcoming = 0
    postponed = 0
    cancelled = 0

    finished_analyzed = 0

    total_goals = 0
    home_goals = 0
    away_goals = 0

    home_wins = 0
    draws = 0
    away_wins = 0

    over_counts = {
        "over_0_5": 0,
        "over_1_5": 0,
        "over_2_5": 0,
        "over_3_5": 0,
        "over_4_5": 0
    }

    btts_yes = 0
    btts_no = 0

    clean_sheet_home = 0
    clean_sheet_away = 0

    zero_zero = 0

    score_distribution = {}

    competitions = {}

    for match in matches:

        status = match.get("status")

        # ----------------------------------------------------
        # STATUT
        # ----------------------------------------------------

        if status in ["IN_PLAY", "PAUSED"]:
            live += 1

        elif status == "FINISHED":
            finished += 1

        elif status in ["POSTPONED"]:
            postponed += 1

        elif status in ["CANCELLED"]:
            cancelled += 1

        else:
            upcoming += 1

        # ----------------------------------------------------
        # COMPÉTITION
        # ----------------------------------------------------

        competition = match.get("competition") or {}

        competition_name = (
            competition.get("name")
            or "Compétition inconnue"
        )

        competition_code = (
            competition.get("code")
            or ""
        )

        # Football-Data.org fournit la zone/pays
        # au niveau du match.
        area = match.get("area") or {}

        country = area.get("name")

        if competition_name not in competitions:

            competitions[competition_name] = {
                "competition": competition_name,
                "code": competition_code,
                "country": country,

                "matches": 0,
                "finished": 0,
                "live": 0,
                "upcoming": 0,

                "goals": 0,
                "analyzed_finished": 0,
                "average_goals": None
            }

        comp = competitions[competition_name]

        comp["matches"] += 1

        if status in ["IN_PLAY", "PAUSED"]:

            comp["live"] += 1

        elif status == "FINISHED":

            comp["finished"] += 1

        elif status not in [
            "POSTPONED",
            "CANCELLED"
        ]:

            comp["upcoming"] += 1

        # ----------------------------------------------------
        # SCORES RÉELS
        # ----------------------------------------------------

        score = match.get("score") or {}
        full_time = score.get("fullTime") or {}

        home_score = full_time.get("home")
        away_score = full_time.get("away")

        # On analyse uniquement les matchs terminés
        # avec deux scores numériques réels.
        if (
            status != "FINISHED"
            or home_score is None
            or away_score is None
        ):
            continue

        try:

            home_score = int(home_score)
            away_score = int(away_score)

        except (ValueError, TypeError):

            continue

        finished_analyzed += 1

        comp["analyzed_finished"] += 1

        match_goals = (
            home_score + away_score
        )

        total_goals += match_goals

        home_goals += home_score
        away_goals += away_score

        comp["goals"] += match_goals

        # ----------------------------------------------------
        # RÉSULTAT
        # ----------------------------------------------------

        if home_score > away_score:

            home_wins += 1

        elif home_score < away_score:

            away_wins += 1

        else:

            draws += 1

        # ----------------------------------------------------
        # OVER
        # ----------------------------------------------------

        if match_goals > 0.5:
            over_counts["over_0_5"] += 1

        if match_goals > 1.5:
            over_counts["over_1_5"] += 1

        if match_goals > 2.5:
            over_counts["over_2_5"] += 1

        if match_goals > 3.5:
            over_counts["over_3_5"] += 1

        if match_goals > 4.5:
            over_counts["over_4_5"] += 1

        # ----------------------------------------------------
        # BTTS
        # ----------------------------------------------------

        if (
            home_score > 0
            and away_score > 0
        ):

            btts_yes += 1

        else:

            btts_no += 1

        # ----------------------------------------------------
        # CLEAN SHEETS
        # ----------------------------------------------------

        if away_score == 0:

            clean_sheet_home += 1

        if home_score == 0:

            clean_sheet_away += 1

        # ----------------------------------------------------
        # 0-0
        # ----------------------------------------------------

        if (
            home_score == 0
            and away_score == 0
        ):

            zero_zero += 1

        # ----------------------------------------------------
        # DISTRIBUTION DES SCORES
        # ----------------------------------------------------

        score_key = (
            f"{home_score}-{away_score}"
        )

        if score_key not in score_distribution:

            score_distribution[score_key] = 0

        score_distribution[score_key] += 1

    # ========================================================
    # POURCENTAGES
    # ========================================================

    results_total = (
        home_wins
        + draws
        + away_wins
    )

    over = {}

    for key, count in over_counts.items():

        over[key] = {
            "count": count,
            "percentage": percentage(
                count,
                finished_analyzed
            )
        }

    # ========================================================
    # BTTS
    # ========================================================

    btts = {
        "yes": btts_yes,
        "no": btts_no,

        "yes_percentage": percentage(
            btts_yes,
            finished_analyzed
        ),

        "no_percentage": percentage(
            btts_no,
            finished_analyzed
        )
    }

    # ========================================================
    # MOYENNE DE BUTS
    # ========================================================

    average_goals = None

    if finished_analyzed > 0:

        average_goals = round(
            total_goals / finished_analyzed,
            2
        )

    # ========================================================
    # MOYENNES PAR COMPÉTITION
    # ========================================================

    competition_list = []

    for comp in competitions.values():

        analyzed = comp["analyzed_finished"]

        if analyzed > 0:

            comp["average_goals"] = round(
                comp["goals"] / analyzed,
                2
            )

        competition_list.append(comp)

    # Tri : plus grandes compétitions d'abord
    competition_list.sort(
        key=lambda item: item["matches"],
        reverse=True
    )

    # ========================================================
    # DISTRIBUTION DES SCORES
    # ========================================================

    score_list = []

    for score, count in score_distribution.items():

        score_list.append({
            "score": score,
            "count": count,
            "percentage": percentage(
                count,
                finished_analyzed
            )
        })

    score_list.sort(
        key=lambda item: item["count"],
        reverse=True
    )

    # ========================================================
    # RÉSULTAT FINAL
    # ========================================================

    return {
        "date": utc_now().date().isoformat(),

        "source": "Football-Data.org",

        "data_policy": {
            "real_data_only": True,
            "no_fake_statistics": True,
            "finished_statistics_require_real_score": True
        },

        "overview": {
            "total_matches": total_matches,
            "live": live,
            "finished": finished,
            "finished_analyzed": finished_analyzed,
            "upcoming": upcoming,
            "postponed": postponed,
            "cancelled": cancelled
        },

        "goals": {
            "total": total_goals,
            "home": home_goals,
            "away": away_goals,
            "average_per_finished_match": average_goals
        },

        "results": {
            "home_wins": home_wins,
            "draws": draws,
            "away_wins": away_wins,

            "home_wins_percentage": percentage(
                home_wins,
                results_total
            ),

            "draws_percentage": percentage(
                draws,
                results_total
            ),

            "away_wins_percentage": percentage(
                away_wins,
                results_total
            )
        },

        "over": over,

        "btts": btts,

        "clean_sheets": {
            "home": clean_sheet_home,
            "away": clean_sheet_away,
            "total": (
                clean_sheet_home
                + clean_sheet_away
            )
        },

        "zero_zero": {
            "count": zero_zero,
            "percentage": percentage(
                zero_zero,
                finished_analyzed
            )
        },

        "score_distribution": score_list,

        "competitions": competition_list
    }


# ============================================================
# FILTRER UNIQUEMENT AUJOURD'HUI
# ============================================================

def filter_matches_for_today(matches):
    """
    Garde uniquement les matchs dont la date UTC
    correspond à aujourd'hui.

    Ceci évite que les matchs de demain soient
    inclus dans /api/statistics/today.
    """

    today = utc_now().date()

    today_matches = []

    for match in matches:

        utc_date = match.get("utcDate")

        parsed_date = parse_utc_date(utc_date)

        if parsed_date is None:
            continue

        if parsed_date.date() == today:

            today_matches.append(match)

    return today_matches


# ============================================================
# ROUTE PRINCIPALE
# ============================================================

@app.route("/")
def home():

    return jsonify({
        "name": "CISSE PRONOS API",
        "status": "online",
        "source": "Football-Data.org",
        "real_data_only": True
    })


# ============================================================
# API MATCHS
# ============================================================

@app.route("/api/matches")
def api_matches():

    data, result = get_matches_data()

    if data is None:

        return jsonify({
            "source": "Football-Data.org",
            "success": False,
            "error": result
        }), 500

    save_result = save_matches_to_supabase(data)

    # Toujours retourner une structure identique.
    return jsonify({
        "source": "Football-Data.org",

        "success": True,

        "from_cache": result.get(
            "from_cache",
            False
        ),

        "supabase": save_result,

        "data": data
    })


# ============================================================
# API LIVE
# ============================================================

@app.route("/api/live")
def api_live():

    token = os.getenv("FOOTBALL_DATA_TOKEN")

    if not token:

        return jsonify({
            "source": "Football-Data.org",
            "success": False,
            "error": "FOOTBALL_DATA_TOKEN manquant."
        }), 500

    url = f"{FOOTBALL_DATA_URL}/matches"

    headers = {
        "X-Auth-Token": token
    }

    params = {
        "status": "LIVE"
    }

    try:

        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=30
        )

        if response.status_code >= 400:

            return jsonify({
                "source": "Football-Data.org",
                "success": False,
                "error": response.text
            }), response.status_code

        data = response.json()

        save_result = save_matches_to_supabase(data)

        return jsonify({
            "source": "Football-Data.org",
            "success": True,
            "supabase": save_result,
            "data": data
        })

    except requests.RequestException as error:

        return jsonify({
            "source": "Football-Data.org",
            "success": False,
            "error": str(error)
        }), 500


# ============================================================
# STATISTIQUES DU JOUR
# ============================================================

@app.route("/api/statistics/today")
def api_statistics_today():

    data, result = get_matches_data()

    if data is None:

        return jsonify({
            "source": "Football-Data.org",
            "success": False,
            "error": result
        }), 500

    all_matches = data.get(
        "matches",
        []
    )

    # IMPORTANT :
    # On retire les matchs de demain.
    today_matches = filter_matches_for_today(
        all_matches
    )

    statistics = calculate_statistics(
        today_matches
    )

    return jsonify(
        statistics
    )


# ============================================================
# LANCEMENT
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "5000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
