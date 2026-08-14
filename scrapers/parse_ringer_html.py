import json
from pathlib import Path

from bs4 import BeautifulSoup
import pandas as pd

HTML_PATH = Path("data/2026 Ringer Preseason Fantasy Football Rankings _ The Ringer Fantasy Football.html")
OUT_PATH  = Path("data/ringer_2026_rankings.csv")
FORMAT    = "half-ppr"


def parse_auction_value(raw: str) -> int:
    """'$$65' → 65.  Returns 0 if blank or unparseable."""
    cleaned = raw.replace("$", "").strip()
    return int(cleaned) if cleaned.isdigit() else 0


def extract_players(html_path: Path) -> pd.DataFrame:
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "lxml")

    # Find the script tag that contains the player data (Next.js RSC flight payload)
    target_script = None
    for tag in soup.find_all("script"):
        if tag.string and "auctionValues" in tag.string:
            target_script = tag.string
            break

    if target_script is None:
        raise RuntimeError("Could not find player data in HTML")

    # The script content is a JS string where inner quotes are escaped as \"
    # Unescape \" → " so we can extract valid JSON
    decoded = target_script.replace('\\"', '"')

    # Locate the "players" array and extract it with balanced-bracket parsing
    players_key = '"players":['
    key_idx = decoded.find(players_key)
    if key_idx == -1:
        raise RuntimeError('Could not locate "players" array in script content')

    start = decoded.index("[", key_idx + len('"players":'))
    depth = 0
    end = start
    for i, ch in enumerate(decoded[start:], start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i
                break

    players = json.loads(decoded[start : end + 1])

    rows = []
    for obj in players:
        rows.extend(_extract_row(obj))

    if not rows:
        raise RuntimeError(
            "No player rows were extracted. The HTML structure may have changed."
        )

    df = (
        pd.DataFrame(rows)
        .sort_values("overall_rank")
        .drop_duplicates(subset=["player_name", "position"])
        .reset_index(drop=True)
    )
    return df


def _extract_row(obj: dict) -> list:
    """Return a list with 0 or 1 row dicts extracted from a player object."""
    overall_rank = obj.get("rankings", {}).get(FORMAT, 9999)
    if overall_rank == 9999:
        return []

    return [{
        "player_name":     obj.get("name", ""),
        "position":        obj.get("position", "").upper(),
        "team":            obj.get("team", ""),
        "overall_rank":    overall_rank,
        "positional_rank": obj.get("positionalRankings", {}).get(FORMAT, 9999),
        "auction_value":   parse_auction_value(
                               obj.get("auctionValues", {}).get(FORMAT, "")
                           ),
        "bye_week":        obj.get("playerMeta", {}).get("byeWeek", 0),
    }]


if __name__ == "__main__":
    df = extract_players(HTML_PATH)
    df.to_csv(OUT_PATH, index=False)
    print(f"Saved {len(df)} players to {OUT_PATH}")
    print(df.head(20).to_string())
