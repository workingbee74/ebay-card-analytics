import os
import requests
import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL", "")

TEAMS_URL = "https://statsapi.mlb.com/api/v1/teams?sportId=1"


def get_mlb_players():
    teams_response = requests.get(TEAMS_URL, timeout=30)
    teams_response.raise_for_status()

    teams = teams_response.json().get("teams", [])

    players = set()

    for team in teams:
        team_id = team["id"]

        roster_url = (
            f"https://statsapi.mlb.com/api/v1/teams/"
            f"{team_id}/roster?rosterType=active"
        )

        response = requests.get(roster_url, timeout=30)
        response.raise_for_status()

        roster = response.json().get("roster", [])

        for entry in roster:
            person = entry.get("person", {})
            name = person.get("fullName")

            if name:
                players.add(name)

    return sorted(players)


def save_players(players):
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is missing")

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for name in players:
                cur.execute(
                    """
                    INSERT INTO players (player_name, player_type)
                    VALUES (%s, 'mlb')
                    ON CONFLICT (player_name)
                    DO UPDATE SET player_type = 'mlb';
                    """,
                    (name,)
                )

        conn.commit()


if __name__ == "__main__":
    players = get_mlb_players()

    print(f"Found {len(players)} active MLB players")

    for name in players[:20]:
        print(name)

    save_players(players)

    print(f"Saved {len(players)} MLB players to PostgreSQL")
