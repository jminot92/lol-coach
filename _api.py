"""Thin Riot API wrapper with local JSON file caching."""
from __future__ import annotations

import json
from pathlib import Path

import requests

CACHE = Path(__file__).parent / "match_cache"
CACHE.mkdir(exist_ok=True)

_KEY: str = ""
_REGIONAL: str = "americas"
_PLATFORM: str = "na1"


def init(api_key: str, regional: str = "americas", platform: str = "na1") -> None:
    global _KEY, _REGIONAL, _PLATFORM
    _KEY = api_key
    _REGIONAL = regional
    _PLATFORM = platform


def _get(url: str, params: dict | None = None) -> dict | list:
    r = requests.get(url, headers={"X-Riot-Token": _KEY}, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def get_match_list(puuid: str, count: int = 10) -> list[str]:
    return _get(
        f"https://{_REGIONAL}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids",
        params={"start": 0, "count": count},
    )


def get_match(match_id: str) -> dict:
    f = CACHE / f"{match_id}.json"
    if f.exists():
        return json.loads(f.read_text())
    data = _get(f"https://{_REGIONAL}.api.riotgames.com/lol/match/v5/matches/{match_id}")
    f.write_text(json.dumps(data))
    return data


def get_timeline(match_id: str) -> dict:
    f = CACHE / f"{match_id}_timeline.json"
    if f.exists():
        return json.loads(f.read_text())
    data = _get(f"https://{_REGIONAL}.api.riotgames.com/lol/match/v5/matches/{match_id}/timeline")
    f.write_text(json.dumps(data))
    return data


def lookup_puuid(game_name: str, tag_line: str) -> str:
    data = _get(
        f"https://{_REGIONAL}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
    )
    return data["puuid"]


def get_champion_mastery(puuid: str, champion_id: int) -> dict | None:
    url = (
        f"https://{_PLATFORM}.api.riotgames.com/lol/champion-mastery/v4/"
        f"champion-masteries/by-puuid/{puuid}/by-champion/{champion_id}"
    )
    try:
        return _get(url)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise
