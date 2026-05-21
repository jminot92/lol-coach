# lol-coach — Project Context

## What this is

A post-game League of Legends coaching tool for **Chumpanda#NA1** (NA server).

After a game ends, open the notebook in Google Colab, run the cells, and a coaching
`.txt` file downloads automatically. Paste it into Claude or ChatGPT for analysis.

---

## GitHub repo

**https://github.com/jminot92/lol-coach** (public)

The notebook is opened directly from GitHub via Colab — no local install needed on
any machine.

---

## The three files that matter

| File | Purpose |
|------|---------|
| `coaching.ipynb` | The workflow — 5 cells, run top to bottom |
| `_api.py` | Riot API wrapper with local JSON file cache |
| `_analysis.py` | Converts raw Riot JSON → coaching text report |

---

## How to run (Colab — works on any machine)

1. Open the bookmark: `colab.research.google.com/github/jminot92/lol-coach/blob/master/coaching.ipynb`
2. Run all 5 cells in order
3. Cell 5 auto-downloads `<MATCH_ID>_coaching.txt`
4. Paste the file into Claude or ChatGPT

The Colab secret `RIOT_API_KEY` must be set (see below). Dev keys expire every 24h —
renew at [developer.riotgames.com](https://developer.riotgames.com) and update the secret.

---

## Colab secrets (one-time setup per Google account)

In Colab: click the 🔑 key icon (left sidebar) → Add new secret:

| Name | Value |
|------|-------|
| `RIOT_API_KEY` | Your key from developer.riotgames.com |

Toggle **Notebook access** on. No other secrets needed.

---

## How to run locally (VS Code on laptop)

1. Open `coaching.ipynb` in VS Code
2. Select the `.venv` kernel — if missing, run `uv add ipykernel` first
3. Cell 1 detects it's not Colab and skips the clone step
4. Cell 2 loads `.env` instead of Colab secrets

Requires `.env` in the project root with `RIOT_API_KEY` set.

---

## Notebook cell structure

| Cell | Purpose |
|------|---------|
| Cell 1 | Colab setup — clones repo, installs deps (skipped when running locally) |
| Cell 2 | Setup — loads API key, looks up live PUUID, initialises client |
| Cell 3 | Recent games — fetches last 10, prints table |
| Cell 4 | Select match — edit `MATCH_INDEX` (0 = most recent) or paste a `MATCH_ID` |
| Cell 5 | Generate file — builds report, saves `.txt`, auto-downloads in Colab |

---

## PUUID drift (important)

Riot has two different PUUIDs for the same account:
- **Account PUUID** — returned by the Account API, used for `get_match_list`
- **Participant PUUID** — stored inside historical match JSON, different value

Cell 2 always calls `lookup_puuid(GAME_NAME, TAG_LINE)` to get the live account PUUID
for API calls. When searching participant data in old cached matches, `_analysis.py`
falls back to matching by `riotIdGameName` if the PUUID doesn't match. Both are handled
automatically — nothing to configure.

---

## Match cache (`match_cache/`)

Only relevant when running locally. Fetched matches are saved as JSON files
(`<MATCH_ID>.json` + `<MATCH_ID>_timeline.json`) so repeat runs are instant.

In Colab the cache is per-session (ephemeral) — matches re-fetch from the API each
time, which takes ~1 second per match.

Currently 20 matches pre-cached locally (Chumpanda's history as of 2026-05-21).

---

## What the coaching report contains

1. **Match header** — champion, result, KDA, CS/min, gold, damage, vision
2. **Key decision windows** — the main coaching layer:
   - 1st and 2nd dragon: player zone at time, involvement, what happened in 90s after
   - First outer tower: player path after it fell, objectives in 90s window
   - High unspent gold: any minute holding >1200g before spending — flagged if a dragon was contested at the same time
3. **Lane phase snapshot** — CS / gold / level at 5, 10, 14 min vs enemy laner
4. **Deaths & aftermath** — each death with zone, killer, gold lead, unspent gold flag, objectives/towers lost in 90s
5. **Full timeline** — every kill, objective, tower, and turret plate chronologically

---

## Player info

- **Riot ID:** Chumpanda#NA1
- **Region:** NA (platform: na1, regional route: americas)
- **Main champion:** Teemo (top lane)

---

## What was removed and why

| Removed | Reason |
|---------|--------|
| `src/lol_match_ai/` | Full package (CLI, MCP server, SQLite store, FastAPI, BigQuery) — replaced by 2-file helper approach |
| `data/lol_matches.sqlite3` | SQLite DB — data migrated to `match_cache/` JSON files |
| `exports/` | JSONL exports — seeded into `match_cache/`, no longer needed |
| `scripts/` | Cloud Run / BigQuery deployment scripts — never deployed |
| `cloud_functions/` | GCP budget guard — never deployed |
| `docs/`, `openapi/`, `sql/` | Cloud architecture docs — obsolete |
| `streamlit_app.py`, `Dockerfile` | Earlier UI iteration — abandoned |

**Google Cloud:** The `lol-ai-analyser` GCP project was created but nothing was ever
deployed. No active Cloud Run jobs, no BigQuery tables, no scheduled tasks.

---

## Possible next improvements

- Multi-match selection in Cell 4 (loop over several games, one file each)
- Fetch opponent champion mastery from Riot API for richer opponent context
- CS differential chart using matplotlib
- Windows scheduled task / file watcher to auto-run after a game ends
