import os
import requests
import psycopg
from bs4 import BeautifulSoup

DATABASE_URL = os.environ.get("DATABASE_URL", "")

MLB_PROSPECTS_URL = "https://www.mlb.com/prospects/stats/top-prospects"


def get_prospect_names():
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(
        MLB_PROSPECTS_URL,
        headers=headers,
        timeout=30
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    names = set()

    # MLB links prospect names to MiLB player profile pages.
    for link in soup.find_all("a"):
        href = link.get("href", "")
        name = link.get_text(" ", strip=True)

        if "/player/" in href and name:
            # Avoid image-only / navigation links
            if len(name.split()) >= 2:
                names.add(name)

    return sorted(names)


def save_players(names):
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is missing")

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for name in names:
                cur.execute(
                    """
                    INSERT INTO players (player_name, player_type)
                    VALUES (%s, 'prospect')
                    ON CONFLICT (player_name)
                    DO UPDATE SET player_type = 'prospect';
                    """,
                    (name,)
                )

        conn.commit()


if __name__ == "__main__":
    prospects = get_prospect_names()

    print(f"Found {len(prospects)} prospect names")

    for name in prospects[:20]:
        print(name)

    save_players(prospects)

    print(f"Saved {len(prospects)} prospects to PostgreSQL")
