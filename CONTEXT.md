# lol-coach — Project Context

## What this is

A post-game League of Legends coaching tool for **Chumpanda#NA1** (NA server).

After a game ends, run the Jupyter notebook to pull the match from the Riot API,
generate a structured text coaching report, and drop it into Claude or ChatGPT for analysis.

---

## The three files that matter

| File | Purpose |
|------|---------|
| `coaching.ipynb` | The workflow — 4 cells, run top to bottom |
| `_api.py` | Riot API wrapper with local JSON file cache |
| `_analysis.py` | Converts raw Riot JSON → coaching text report |

Everything else in this repo is dead code from earlier iterations (BigQuery, Cloud Run,
MCP server, FastAPI, Streamlit, SQLite). It has been removed.

---

## How to run

1. Open `coaching.ipynb` in VS Code
2. Select the `.venv` Python kernel (see setup below if first time)
3. **Cell 1** — loads `.env`, initialises API client
4. **Cell 2** — fetches last 10 games, prints a table
5. **Cell 3** — edit `MATCH_INDEX` (0 = most recent) or paste a `MATCH_ID` directly
6. **Cell 4** — generates `<MATCH_ID>_coaching.txt`, saves it, prints a preview

Paste the `.txt` file into Claude or ChatGPT and ask for coaching.

---

## First-time VS Code setup (one-off)

```
uv add ipykernel
```

Then in VS Code: open `coaching.ipynb` → "Select Kernel" (top right) → Python (.venv).

---

## Configuration (`.env`)

```
RIOT_API_KEY            # Dev key — expires every 24h, renew at developer.riotgames.com
RIOT_REGIONAL_ROUTE     # americas (NA/BR/LAN/LAS)
RIOT_PLATFORM_ROUTE     # na1
LOL_MATCH_AI_MY_PUUID   # Chumpanda#NA1 PUUID, hardcoded to avoid Riot API drift
```

**Important:** Riot development API keys expire every 24 hours. If you get a 403 error,
go to [developer.riotgames.com](https://developer.riotgames.com), regenerate the key,
and paste it into `.env`.

---

## Match cache (`match_cache/`)

Fetched matches are saved as JSON files (`<MATCH_ID>.json` + `<MATCH_ID>_timeline.json`).
On repeat runs the notebook loads from cache instantly — no API calls.

Currently 20 matches cached (all of Chumpanda's local history as of 2026-05-21).

To clear the cache and re-fetch fresh: delete files from `match_cache/`.

---

## What the coaching report contains

1. **Match header** — champion, result, KDA, CS/min, gold, damage, vision
2. **Key decision windows** — the main coaching layer:
   - 1st and 2nd dragon: was player present, zone at time, what happened in 90s after
   - First outer tower: player path after it fell, objectives in window
   - High unspent gold: any minute where player held >1200g before spending (flags if an objective was contested at same time)
3. **Lane phase snapshot** — CS / gold / level at 5, 10, 14 min vs enemy laner
4. **Deaths & aftermath** — each death: zone, killer, gold lead at time, unspent gold flag, objectives/towers lost in 90s
5. **Full timeline** — every kill, objective, tower, and turret plate in chronological order

---

## What was removed and why

| Removed | Reason |
|---------|--------|
| `src/lol_match_ai/` | Full package (CLI, MCP server, store, FastAPI, BigQuery) — replaced by the 2-file helper approach |
| `data/lol_matches.sqlite3` | SQLite DB — data exported to `match_cache/` JSON files |
| `exports/` | JSONL exports — data seeded into `match_cache/`, no longer needed |
| `scripts/` | Cloud Run / BigQuery deployment scripts — never deployed, project abandoned |
| `cloud_functions/` | GCP budget guard — never deployed |
| `docs/`, `openapi/`, `sql/` | Cloud architecture docs — obsolete |
| `streamlit_app.py`, `Dockerfile` | Earlier UI iteration — abandoned |
| `.claude/settings.json` MCP config | MCP server removed |

**Google Cloud:** The `GOOGLE_CLOUD_PROJECT=lol-ai-analyser` project was created but
**nothing was ever deployed** (all scripts were `.example.ps1` templates). No active
Cloud Run jobs, no BigQuery tables, no scheduled tasks. Nothing to pause or shut down.

---

## Player info

- **Riot ID:** Chumpanda#NA1
- **Region:** NA (platform: na1, regional route: americas)
- **Main champion:** Teemo (top lane)
- **PUUID:** `BH62RseTDGAvIlFWtNySS1R6y0rDbW4Fxa54eXKr-8ZSCsV3HI6-rc4-opydNutmRqMr4c2uUwtFxg`

Note: The PUUID is hardcoded in `.env` rather than looked up via API, because Riot's
account API can return a different PUUID for the same account than what appears in
historical match participant data ("PUUID drift"). The hardcoded value matches the
participant records in the cached matches.

---

## Possible next improvements

- Add multi-match selection in Cell 3 (loop over several games, generate one file each)
- Add CS differential chart (matplotlib, already available via Streamlit/pandas install)
- Fetch opponent champion mastery from Riot API during Cell 4 for richer context
- Automate Cell 1 + Cell 2 on game-end via a Windows scheduled task or file watcher
