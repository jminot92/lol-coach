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


def init(api_key: str | None, regional: str = "americas", platform: str = "na1") -> None:
    global _KEY, _REGIONAL, _PLATFORM
    _KEY = (api_key or "").strip()
    if not _KEY:
        raise RuntimeError(
            "RIOT_API_KEY is missing. In Colab, add it in the Secrets panel "
            "and make sure Notebook access is toggled on. Riot dev keys also "
            "expire every 24 hours, so renew it if needed."
        )
    _REGIONAL = regional
    _PLATFORM = platform


def _get(url: str, params: dict | None = None) -> dict | list:
    try:
        r = requests.get(url, headers={"X-Riot-Token": _KEY}, params=params, timeout=10)
        r.raise_for_status()
    except requests.HTTPError as exc:
        response = exc.response
        status = response.status_code if response is not None else "unknown"
        detail = response.text[:500] if response is not None else str(exc)
        hint = ""
        if status in (401, 403):
            hint = " The Riot API key is probably expired, invalid, or not enabled for this notebook."
        elif status == 429:
            hint = " Riot rate limited the request; wait a minute and retry."
        raise RuntimeError(f"Riot API request failed ({status}).{hint} URL: {url}. Response: {detail}") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"Riot API request failed before receiving a response. URL: {url}. Error: {exc}") from exc
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
