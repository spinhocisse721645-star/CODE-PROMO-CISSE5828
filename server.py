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
    Calcule la minute approximative à partir de l'heure
    officielle du coup d'envoi.

    Aucun temps additionnel fictif n'est ajouté.
    """

    status = match.get("status")

    if status in ["SCHEDULED", "TIMED", "POSTPONED", "CANCELLED"]:
        return 0

    if status in ["FINISHED", "SUSPENDED", "AWARDED"]:
        return 0

    if status not in ["IN_PLAY", "PAUSED"]:
        return 0

    utc_date = match.get("utcDate")

    if not utc_date:
        return 0

    try:
        kickoff = datetime.fromisoformat(
            utc_date.replace("Z", "+00:00")
        )

        now = datetime.now(timezone.utc)

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

        if not home_name or not away_name:
            continue

        # ----------------------------------------------------
        # SCORES
        # ----------------------------------------------------
        # IMPORTANT :
        # On conserve NULL quand Football-Data.org ne fournit
        # pas encore de score.
        #
        # Cela évite de transformer un match à venir en 0-0.
        # ----------------------------------------------------

        home_score = full_time.get("home")
        away_score = full_time.get("away")

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
    # VERIFICATION
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
# OUTILS STATISTIQUES
# ============================================================

def calculate_statistics(matches):
    """
    Calcule les statistiques uniquement à partir des
    données réellement disponibles.

    Les statistiques de résultats utilisent uniquement
    les matchs FINISHED avec deux scores réels.
    """

    total_matches = len(matches)

    live_matches = [
        match for match in matches
        if match.get("status") in ["IN_PLAY", "PAUSED"]
    ]

    finished_matches = [
        match for match in matches
        if match.get("status") == "FINISHED"
    ]

    upcoming_matches = [
        match for match in matches
        if match.get("status") in [
            "SCHEDULED",
            "TIMED"
        ]
    ]

    postponed_matches = [
        match for match in matches
        if match.get("status") == "POSTPONED"
    ]

    cancelled_matches = [
        match for match in matches
        if match.get("status") == "CANCELLED"
    ]

    # ========================================================
    # MATCHS TERMINÉS AVEC SCORE RÉEL
    # ========================================================

    finished_with_score = []

    for match in finished_matches:

        score = match.get("score", {})
        full_time = score.get("fullTime", {})

        home = full_time.get("home")
        away = full_time.get("away")

        if (
            isinstance(home, int)
            and isinstance(away, int)
            and home >= 0
            and away >= 0
        ):
            finished_with_score.append({
                "match": match,
                "home": home,
                "away": away
            })

    analyzed_matches = len(finished_with_score)

    # ========================================================
    # STATISTIQUES DE BASE
    # ========================================================

    total_goals = 0
    home_goals = 0
    away_goals = 0

    home_wins = 0
    draws = 0
    away_wins = 0

    btts_yes = 0
    btts_no = 0

    clean_sheet_home = 0
    clean_sheet_away = 0

    zero_zero = 0

    over_05 = 0
    over_15 = 0
    over_25 = 0
    over_35 = 0
    over_45 = 0

    score_distribution = {}

    for item in finished_with_score:

        home = item["home"]
        away = item["away"]

        goals = home + away

        total_goals += goals
        home_goals += home
        away_goals += away

        # Résultat
        if home > away:
            home_wins += 1
        elif home == away:
            draws += 1
        else:
            away_wins += 1

        # BTTS
        if home > 0 and away > 0:
            btts_yes += 1
        else:
            btts_no += 1

        # Clean sheets
        if away == 0:
            clean_sheet_home += 1

        if home == 0:
            clean_sheet_away += 1

        # 0-0
        if home == 0 and away == 0:
            zero_zero += 1

        # Over
        if goals > 0.5:
            over_05 += 1

        if goals > 1.5:
            over_15 += 1

        if goals > 2.5:
            over_25 += 1

        if goals > 3.5:
            over_35 += 1

        if goals > 4.5:
            over_45 += 1

        # Distribution des scores
        score_key = f"{home}-{away}"

        score_distribution[score_key] = (
            score_distribution.get(score_key, 0) + 1
        )

    # ========================================================
    # POURCENTAGE
    # ========================================================

    def percentage(value, total):

        if total == 0:
            return None

        return round((value / total) * 100, 2)

    # ========================================================
    # MOYENNE DE BUTS
    # ========================================================

    average_goals = None

    if analyzed_matches > 0:
        average_goals = round(
            total_goals / analyzed_matches,
            2
        )

    # ========================================================
    # RESULTATS
    # ========================================================

    results = {
        "home_wins": home_wins,
        "draws": draws,
        "away_wins": away_wins,

        "home_wins_percentage": percentage(
            home_wins,
            analyzed_matches
        ),

        "draws_percentage": percentage(
            draws,
            analyzed_matches
        ),

        "away_wins_percentage": percentage(
            away_wins,
            analyzed_matches
        )
    }

    # ========================================================
    # OVER
    # ========================================================

    over = {
        "over_0_5": {
            "count": over_05,
            "percentage": percentage(
                over_05,
                analyzed_matches
            )
        },

        "over_1_5": {
            "count": over_15,
            "percentage": percentage(
                over_15,
                analyzed_matches
            )
        },

        "over_2_5": {
            "count": over_25,
            "percentage": percentage(
                over_25,
                analyzed_matches
            )
        },

        "over_3_5": {
            "count": over_35,
            "percentage": percentage(
                over_35,
                analyzed_matches
            )
        },

        "over_4_5": {
            "count": over_45,
            "percentage": percentage(
                over_45,
                analyzed_matches
            )
        }
    }

    # ========================================================
    # BTTS
    # ========================================================

    btts = {
        "yes": btts_yes,
        "no": btts_no,
        "yes_percentage": percentage(
            btts_yes,
            analyzed_matches
        ),
        "no_percentage": percentage(
            btts_no,
            analyzed_matches
        )
    }

    # ========================================================
    # CLEAN SHEETS
    # ========================================================

    clean_sheets = {
        "home": clean_sheet_home,
        "away": clean_sheet_away,
        "total": clean_sheet_home + clean_sheet_away
    }

    # ========================================================
    # SCORE DISTRIBUTION
    # ========================================================

    sorted_scores = sorted(
        score_distribution.items(),
        key=lambda item: item[1],
        reverse=True
    )

    score_distribution_result = []

    for score, count in sorted_scores:

        score_distribution_result.append({
            "score": score,
            "count": count,
            "percentage": percentage(
                count,
                analyzed_matches
            )
        })

    # ========================================================
    # STATISTIQUES PAR COMPETITION
    # ========================================================

    competitions = {}

    for match in matches:

        competition = match.get("competition", {})

        competition_name = competition.get("name")

        if not competition_name:
            continue

        if competition_name not in competitions:

            competitions[competition_name] = {
                "competition": competition_name,
                "code": competition.get("code"),
                "country": (
                    competition.get("area", {})
                    .get("name")
                ),
                "matches": 0,
                "finished": 0,
                "live": 0,
                "upcoming": 0,
                "goals": 0,
                "analyzed_finished": 0
            }

        item = competitions[competition_name]

        item["matches"] += 1

        status = match.get("status")

        if status == "FINISHED":
            item["finished"] += 1

        elif status in ["IN_PLAY", "PAUSED"]:
            item["live"] += 1

        elif status in ["SCHEDULED", "TIMED"]:
            item["upcoming"] += 1

        # Ajouter les buts seulement si les deux scores
        # réels sont disponibles.
        score = match.get("score", {})
        full_time = score.get("fullTime", {})

        home = full_time.get("home")
        away = full_time.get("away")

        if (
            status == "FINISHED"
            and isinstance(home, int)
            and isinstance(away, int)
        ):

            item["goals"] += home + away
            item["analyzed_finished"] += 1

    competition_result = []

    for item in competitions.values():

        if item["analyzed_finished"] > 0:

            item["average_goals"] = round(
                item["goals"] /
                item["analyzed_finished"],
                2
            )

        else:

            item["average_goals"] = None

        # Ce champ n'est pas calculé artificiellement.
        # Il sera ajouté plus tard si nous avons des données
        # suffisamment détaillées pour BTTS par compétition.

        competition_result.append(item)

    competition_result.sort(
        key=lambda item: item["matches"],
        reverse=True
    )

    # ========================================================
    # REPONSE FINALE
    # ========================================================

    return {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),

        "source": "Football-Data.org",

        "data_policy": {
            "real_data_only": True,
            "no_fake_statistics": True,
            "finished_statistics_require_real_score": True
        },

        "overview": {
            "total_matches": total_matches,
            "live": len(live_matches),
            "finished": len(finished_matches),
            "upcoming": len(upcoming_matches),
            "postponed": len(postponed_matches),
            "cancelled": len(cancelled_matches),
            "finished_analyzed": analyzed_matches
        },

        "goals": {
            "total": total_goals,
            "average_per_finished_match": average_goals,
            "home": home_goals,
            "away": away_goals
        },

        "results": results,

        "over": over,

        "btts": btts,

        "clean_sheets": clean_sheets,

        "zero_zero": {
            "count": zero_zero,
            "percentage": percentage(
                zero_zero,
                analyzed_matches
            )
        },

        "score_distribution": score_distribution_result,

        "competitions": competition_result
    }


# ============================================================
# PAGE PRINCIPALE
# ============================================================

@app.route("/")
def home():

    return jsonify({
        "message": (
            "CISSE PRONOS API fonctionne avec "
            "Football-Data.org et Supabase !"
        )
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

    now = datetime.now(timezone.utc)

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    if (
        matches_cache["data"] is not None
        and matches_cache["timestamp"] is not None
        and (
            now - matches_cache["timestamp"]
        ).total_seconds() < CACHE_DURATION
    ):

        return jsonify(matches_cache["data"])

    # --------------------------------------------------------
    # DATES
    # --------------------------------------------------------

    today = now.date()
    tomorrow = today + timedelta(days=1)

    date_from = today.strftime("%Y-%m-%d")
    date_to = tomorrow.strftime("%Y-%m-%d")

    # --------------------------------------------------------
    # FOOTBALL-DATA.ORG
    # --------------------------------------------------------

    token = os.getenv("FOOTBALL_DATA_TOKEN")

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

    if response.status_code != 200:

        return jsonify({
            "error": "Erreur Football-Data.org",
            "status": response.status_code,
            "details": response.text
        }), response.status_code

    data = response.json()

    # --------------------------------------------------------
    # SUPABASE
    # --------------------------------------------------------

    supabase_result = save_matches_to_supabase(data)

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    matches_cache["data"] = data
    matches_cache["timestamp"] = now

    return jsonify({
        "source": "Football-Data.org",
        "supabase": supabase_result,
        "data": data
    })


# ============================================================
# STATISTIQUES GENERALES DU JOUR
# ============================================================

@app.route("/api/statistics/today")
def statistics_today():

    token = os.getenv("FOOTBALL_DATA_TOKEN")

    if not token:

        return jsonify({
            "error": "FOOTBALL_DATA_TOKEN manquante"
        }), 500

    now = datetime.now(timezone.utc)

    # --------------------------------------------------------
    # UTILISATION DU CACHE
    # --------------------------------------------------------

    data = None

    if (
        matches_cache["data"] is not None
        and matches_cache["timestamp"] is not None
        and (
            now - matches_cache["timestamp"]
        ).total_seconds() < CACHE_DURATION
    ):

        data = matches_cache["data"]

    # --------------------------------------------------------
    # SI PAS DE CACHE : RECUPERATION REELLE
    # --------------------------------------------------------

    if data is None:

        today = now.date()

        tomorrow = today + timedelta(days=1)

        date_from = today.strftime("%Y-%m-%d")
        date_to = tomorrow.strftime("%Y-%m-%d")

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
                "error": (
                    "Impossible de contacter "
                    "Football-Data.org"
                ),
                "details": str(e)
            }), 502

        if response.status_code != 200:

            return jsonify({
                "error": "Erreur Football-Data.org",
                "status": response.status_code,
                "details": response.text
            }), response.status_code

        data = response.json()

        # Sauvegarde Supabase
        save_matches_to_supabase(data)

        # Mise en cache
        matches_cache["data"] = data
        matches_cache["timestamp"] = now

    # --------------------------------------------------------
    # CALCUL
    # --------------------------------------------------------

    matches_data = data.get("matches", [])

    statistics = calculate_statistics(matches_data)

    return jsonify(statistics)


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

    if response.status_code != 200:

        return jsonify({
            "error": "Erreur Football-Data.org",
            "status": response.status_code,
            "details": response.text
        }), response.status_code

    data = response.json()

    # --------------------------------------------------------
    # SUPABASE
    # --------------------------------------------------------

    supabase_result = save_matches_to_supabase(data)

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
