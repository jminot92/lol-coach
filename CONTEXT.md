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

**If the report looks stale after a code update:** re-run Cell 1 — it now always
`git pull`s the latest `_analysis.py`. Cell 1 prints the current commit hash so you
can confirm which version is loaded. If the notebook structure itself changed, close
the tab, do **Runtime → Disconnect and delete runtime**, and reopen the link fresh.

---

## Colab secrets (one-time setup per Google account)

In Colab: click the key icon (left sidebar) → Add new secret:

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
| Cell 1 | Colab setup — clones repo (or pulls latest), installs deps, prints commit hash |
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

---

## What the coaching report contains

1. **Match header** — champion, result, KDA, CS/min, gold, damage, vision, team comps
2. **Key decision windows** — the main coaching layer:
   - Facts, interpretation, recommendation, and confidence are separated
   - 1st and 2nd dragon: labelled assessment, recommendation, and confidence
   - TOP LANE outer turret: ally-taken and enemy-taken tracked separately, with live objective context
   - High unspent gold: flagged at 1500g+ with 30s objective conversion exception
3. **Lane opponent context** — opponent champion mastery when Riot's Champion Mastery API is available
4. **Lane phase snapshot** — CS / gold / level at 5, 10, 14 min vs enemy laner
5. **Deaths & aftermath** — zone, killer, gold tier flags (800/1500/2500g), shutdown risk,
   pre-death kill classification, ally kills at 15/30/60s after death, objectives/towers within 90s
6. **Teemo shroom usage** — total placed, early/mid/late buckets, correlation with dragon windows
   (Teemo only; Riot API does not include placement coordinates)
7. **Full timeline** — every kill, objective, tower, and valid turret plate chronologically

---

## Dragon assessment labels

| Label | Meaning |
|-------|---------|
| `good_objective_contribution` | Player secured, assisted, or was in the pit |
| `correct_trade` | Player absent top-side with a kill or tower found in 90s prior |
| `too_late_to_rotate` | Player was moving toward dragon but arrived after it was taken |
| `missed_rotation` | Player in a rotatable zone (mid/bot/river) but did not rotate |
| `low_impact_absence` | Player was top-side but no kill or tower pressure found |
| `unclear_low_confidence` | Player in base or zone could not be determined |

---

## Known Riot API limitations

- `WARD_PLACED` (Teemo shrooms) events contain no position coordinates — zone inference is impossible
- `DRAGON_SOUL_GIVEN` fires in Swiftplay after every dragon (game-mode quirk), not just the 4th —
  treat these events as unreliable metadata
- Participant PUUID in historical match JSON differs from the Account API PUUID (handled via name fallback)

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

## Recently completed refinements

1. Tower transition: classify as `correct_trade` when ally secured dragon and player converted top outer -> inner in the same window.
2. High unspent gold near an ally-secured objective now uses softer wording.
3. Guaranteed-gain exception: high gold followed by a tower/objective within 30s is classified as `acceptable_greed`.
4. `DRAGON_SOUL_GIVEN` is only shown when the team has actually secured 4 dragons.
5. Death context now surfaces player's own post-death kills explicitly.
6. Standard Summoner's Rift turret plates after 14:00 are suppressed.
7. Objective recommendations now consider already-taken Herald and upcoming dragon/baron spawns.
8. Dragon involvement zones prefer `dragon_pit` / `dragon_area` when the player assisted or secured the objective.
9. Lane opponent champion mastery is fetched in Cell 5 and included as context.

## Pending refinements (next session)

1. Multi-match selection in Cell 4 (loop over several games, one file each)
2. CS differential chart using matplotlib

---

## Possible future improvements

- Multi-match selection in Cell 4 (loop over several games, one file each)
- CS differential chart using matplotlib
- Windows scheduled task / file watcher to auto-run after a game ends
