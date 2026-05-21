"""Build a text coaching report directly from raw Riot match + timeline JSON."""
from __future__ import annotations

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Zone inference
# ---------------------------------------------------------------------------

def _zone(x: int | None, y: int | None) -> str:
    if x is None or y is None:
        return "unknown"
    if x < 2000 and y < 2000:
        return "blue_base"
    if x > 13000 and y > 13000:
        return "red_base"
    if 8500 < x < 11200 and 3000 < y < 6200:
        return "dragon_pit"
    if 3500 < x < 6500 and 8500 < y < 12000:
        return "baron_pit"
    if x < 2800 or (x < 5000 and y > 9000):
        return "top_lane"
    if y < 2800 or (y < 5000 and x > 10000):
        return "bot_lane"
    if abs(x - y) < 2500 and 3500 < x < 11500:
        return "mid_lane"
    if x < 8000 and y > x + 500:
        return "top_river"
    if y < 8000 and x > y + 500:
        return "bot_river"
    if x < 7500 and y > 7500:
        return "blue_top_jungle"
    if x < 7500:
        return "blue_bot_jungle"
    if y > 7500:
        return "red_top_jungle"
    return "red_bot_jungle"


# ---------------------------------------------------------------------------
# Timeline parsing
# ---------------------------------------------------------------------------

def _parse_events(timeline: dict) -> list[dict]:
    events: list[dict] = []
    for frame in timeline["info"]["frames"]:
        events.extend(frame["events"])
    return sorted(events, key=lambda e: e["timestamp"])


def _parse_frames(timeline: dict) -> dict[int, list[dict]]:
    """Returns {participant_id: [frame dicts sorted by minute]}."""
    result: dict[int, list[dict]] = {}
    for frame in timeline["info"]["frames"]:
        ts = frame["timestamp"]
        minute = ts // 60_000
        for pid_str, pf in frame["participantFrames"].items():
            pid = int(pid_str)
            pos = pf.get("position", {})
            result.setdefault(pid, []).append({
                "minute": minute,
                "ts": ts,
                "total_gold": pf.get("totalGold", 0),
                "current_gold": pf.get("currentGold", 0),
                "cs": pf.get("minionsKilled", 0) + pf.get("jungleMinionsKilled", 0),
                "level": pf.get("level", 1),
                "x": pos.get("x"),
                "y": pos.get("y"),
            })
    return result


def _nearest(frames: list[dict], ts: int) -> dict | None:
    return min(frames, key=lambda f: abs(f["ts"] - ts)) if frames else None


def _at_minute(frames: list[dict], minute: int) -> dict | None:
    return next((f for f in frames if f["minute"] == minute), None)


def _window(events: list[dict], start: int, end: int, types: list[str] | None = None) -> list[dict]:
    return [
        e for e in events
        if start <= e["timestamp"] <= end
        and (types is None or e["type"] in types)
    ]


# ---------------------------------------------------------------------------
# Participant helpers
# ---------------------------------------------------------------------------

def _find_player(participants: list[dict], puuid: str, game_name: str = "", tag_line: str = "") -> dict | None:
    p = next((p for p in participants if p["puuid"] == puuid), None)
    if p:
        return p
    # Fallback: match by Riot ID — handles PUUID drift between old cached and live API data
    if game_name:
        p = next((
            p for p in participants
            if p.get("riotIdGameName", "").lower() == game_name.lower()
            and (not tag_line or p.get("riotIdTagline", "").lower() == tag_line.lower())
        ), None)
    return p


def _find_opponent(participants: list[dict], player: dict) -> dict | None:
    role = player.get("teamPosition") or ""
    opp_team = 200 if player["teamId"] == 100 else 100
    return next(
        (p for p in participants if p["teamId"] == opp_team and p.get("teamPosition") == role),
        None,
    ) if role else None


def _pname(participants: list[dict], pid: int | None) -> str:
    if pid is None:
        return "?"
    p = next((x for x in participants if x["participantId"] == pid), None)
    return p["championName"] if p else f"P{pid}"


def _side(participants: list[dict], pid: int | None, player_team: int) -> str:
    if pid is None:
        return "?"
    p = next((x for x in participants if x["participantId"] == pid), None)
    return "ally" if (p and p["teamId"] == player_team) else "enemy"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_QUEUE_NAMES: dict[int, str] = {
    400: "Normal Draft", 420: "Ranked Solo/Duo", 430: "Normal Blind",
    440: "Ranked Flex", 450: "ARAM", 480: "Swiftplay", 490: "Quickplay",
    700: "Clash", 900: "URF", 1700: "Arena", 1900: "URF",
}

_OBJECTIVE_TYPES = {"ELITE_MONSTER_KILL", "DRAGON_SOUL_GIVEN"}
_MONSTER_TYPES = {"DRAGON", "BARON_NASHOR", "RIFTHERALD", "HORDE"}


def _ts(ms: int) -> str:
    m, s = divmod(ms // 1000, 60)
    return f"{m:02d}:{s:02d}"


def _dur(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    return f"{m}m{s:02d}s"


def _sep(title: str = "") -> str:
    line = "=" * 60
    return f"\n{line}\n{title}\n{line}" if title else f"\n{'=' * 60}"


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _match_header(info: dict, player: dict, participants: list[dict]) -> str:
    duration = info.get("gameDuration", 0)
    queue = _QUEUE_NAMES.get(info.get("queueId"), f"Queue {info.get('queueId')}")
    start_ms = info.get("gameStartTimestamp", 0)
    date_str = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

    role = player.get("teamPosition") or player.get("individualPosition") or "?"
    cs = player["totalMinionsKilled"] + player.get("neutralMinionsKilled", 0)
    cs_pm = cs * 60 / duration if duration else 0
    result = "WIN" if player["win"] else "LOSS"

    my_team = " | ".join(p["championName"] for p in participants if p["teamId"] == player["teamId"])
    enemy_team = " | ".join(p["championName"] for p in participants if p["teamId"] != player["teamId"])

    return "\n".join([
        f"Champion : {player['championName']} ({role})  —  {result}",
        f"Date     : {date_str}  |  {queue}  |  {_dur(duration)}",
        f"KDA      : {player['kills']}/{player['deaths']}/{player['assists']}",
        f"CS       : {cs} ({cs_pm:.1f}/min)  |  Gold: {player['goldEarned']:,}",
        f"Damage   : {player.get('totalDamageDealtToChampions', 0):,}  |  Vision: {player.get('visionScore', 0)}",
        "",
        f"YOUR TEAM : {my_team}",
        f"ENEMY TEAM: {enemy_team}",
    ])


def _lane_phase(player: dict, opponent: dict | None, frames: dict[int, list[dict]]) -> str:
    ppid = player["participantId"]
    opid = opponent["participantId"] if opponent else None
    pf_list = frames.get(ppid, [])
    of_list = frames.get(opid, []) if opid else []
    opp_label = opponent["championName"] if opponent else "—"

    lines = [f"vs {opp_label}", ""]
    header = f"{'Min':>3}  {'CS':>4}  {'Gold':>6}  {'Lvl':>3}"
    if opponent:
        header += f"    {'oCS':>4}  {'oGold':>6}  {'oLvl':>3}    {'±CS':>4}  {'±Gold':>6}"
    lines.append(header)
    lines.append("-" * len(header))

    for minute in [5, 10, 14]:
        pf = _at_minute(pf_list, minute)
        of = _at_minute(of_list, minute) if opid else None
        if not pf:
            continue
        row = f"{minute:>3}  {pf['cs']:>4}  {pf['total_gold']:>6}  {pf['level']:>3}"
        if opponent and of:
            cs_d = pf["cs"] - of["cs"]
            g_d = pf["total_gold"] - of["total_gold"]
            row += f"    {of['cs']:>4}  {of['total_gold']:>6}  {of['level']:>3}    {cs_d:>+4}  {g_d:>+6}"
        lines.append(row)

    return "\n".join(lines)


def _deaths(events: list[dict], participants: list[dict], player: dict, opponent: dict | None, frames: dict[int, list[dict]]) -> str:
    ppid = player["participantId"]
    pt = player["teamId"]
    pf_list = frames.get(ppid, [])
    of_list = frames.get(opponent["participantId"], []) if opponent else []

    deaths = [e for e in events if e["type"] == "CHAMPION_KILL" and e.get("victimId") == ppid]
    if not deaths:
        return "No deaths."

    lines: list[str] = []
    for i, evt in enumerate(deaths, 1):
        ts = evt["timestamp"]
        pos = evt.get("position", {})
        zone = _zone(pos.get("x"), pos.get("y"))
        pf = _nearest(pf_list, ts)
        of = _nearest(of_list, ts) if of_list else None

        killer = _pname(participants, evt.get("killerId"))
        assist_names = [_pname(participants, a) for a in evt.get("assistingParticipantIds", [])]
        threat = f"killed by {killer}" + (f" (+{', '.join(assist_names)})" if assist_names else "")

        unspent = pf["current_gold"] if pf else 0
        level = pf["level"] if pf else "?"
        gold_lead = (pf["total_gold"] - of["total_gold"]) if (pf and of) else None

        lead_str = f"  gold lead vs laner: {gold_lead:+,}" if gold_lead is not None else ""

        lines.append(f"Death #{i} @ {_ts(ts)}  zone: {zone}")
        lines.append(f"  {threat}  |  level {level}{lead_str}")
        if unspent > 800:
            lines.append(f"  ! {unspent}g unspent at death — sub-optimal recall timing")

        after = _window(events, ts, ts + 90_000)
        objs = [e for e in after if e["type"] in _OBJECTIVE_TYPES or e.get("monsterType") in _MONSTER_TYPES]
        towers = [e for e in after if e["type"] == "BUILDING_KILL"]

        for o in objs[:2]:
            sub = o.get("monsterSubType") or o.get("monsterType") or "objective"
            team = _side(participants, o.get("killerId"), pt)
            lines.append(f"  -> {_ts(o['timestamp'])}  {team} took {sub.replace('_', ' ').title()} (+{(o['timestamp']-ts)//1000}s)")
        for t in towers[:2]:
            team = _side(participants, t.get("killerId"), pt)
            lane = t.get("laneType", "?").replace("_", " ")
            lines.append(f"  -> {_ts(t['timestamp'])}  {team} tower fell {lane} (+{(t['timestamp']-ts)//1000}s)")
        if not objs and not towers:
            lines.append("  -> No objectives/towers lost in 90s")
        lines.append("")

    return "\n".join(lines)


def _decision_windows(events: list[dict], participants: list[dict], player: dict, frames: dict[int, list[dict]]) -> str:
    ppid = player["participantId"]
    pt = player["teamId"]
    pf_list = frames.get(ppid, [])
    lines: list[str] = []

    # Dragons (first two)
    dragons = [e for e in events if e["type"] == "ELITE_MONSTER_KILL" and e.get("monsterType") == "DRAGON"]
    for i, evt in enumerate(dragons[:2]):
        label = "1st" if i == 0 else "2nd"
        ts = evt["timestamp"]
        sub = (evt.get("monsterSubType") or "Dragon").replace("_", " ").title()
        secured_by = _side(participants, evt.get("killerId"), pt)
        pf = _nearest(pf_list, ts)
        zone = _zone(pf["x"], pf["y"]) if pf else "unknown"
        gold = pf["total_gold"] if pf else None

        assists = evt.get("assistingParticipantIds", [])
        if evt.get("killerId") == ppid:
            involvement = "secured"
        elif ppid in assists:
            involvement = "assisted"
        elif zone == "dragon_pit":
            involvement = "nearby"
        else:
            involvement = "absent"

        after = _window(events, ts, ts + 90_000)
        kills_n = len([e for e in after if e["type"] == "CHAMPION_KILL"])
        towers_n = len([e for e in after if e["type"] == "BUILDING_KILL"])

        lines.append(f"[{_ts(ts)}] {label} Dragon — {sub}")
        lines.append(f"  Secured by {secured_by}  |  player was in {zone} ({involvement})" +
                     (f"  |  {gold:,}g" if gold else ""))

        if secured_by == "enemy" and involvement == "absent":
            lines.append(f"  ! Enemy took {sub} uncontested — was rotating possible?")
        elif secured_by == "ally" and involvement == "absent":
            lines.append(f"  ? Team took {sub} without you — was split-push worth it?")
        elif involvement in ("secured", "assisted", "nearby"):
            lines.append(f"  + Player contributed to {sub}")

        if kills_n:
            lines.append(f"  Next 90s: {kills_n} kill(s), {towers_n} tower(s)")
        lines.append("")

    # First outer tower
    outer_towers = [e for e in events if e["type"] == "BUILDING_KILL" and e.get("towerType") == "OUTER_TURRET"]
    if outer_towers:
        evt = outer_towers[0]
        ts = evt["timestamp"]
        taken_by = _side(participants, evt.get("killerId"), pt)
        lane = evt.get("laneType", "?").replace("_", " ")
        pf_after = [f for f in pf_list if ts < f["ts"] <= ts + 90_000]
        path = " -> ".join(_zone(f["x"], f["y"]) for f in pf_after[:3]) or "unknown"

        after = _window(events, ts, ts + 90_000)
        objs_after = [e for e in after if e["type"] in _OBJECTIVE_TYPES or e.get("monsterType") in _MONSTER_TYPES]

        lines.append(f"[{_ts(ts)}] First Outer Tower — {lane}")
        lines.append(f"  Taken by {taken_by}  |  player path after: {path}")
        if objs_after:
            for o in objs_after[:2]:
                sub = (o.get("monsterSubType") or o.get("monsterType") or "obj").replace("_", " ").title()
                oteam = _side(participants, o.get("killerId"), pt)
                lines.append(f"  -> {_ts(o['timestamp'])}  {oteam} took {sub}")
        else:
            lines.append("  No objectives in 90s — recall/vision window available")
        lines.append("")

    # High unspent gold before spend
    GOLD_THRESH, SPEND_DROP = 1200, 500
    for j in range(len(pf_list) - 1):
        f_now, f_next = pf_list[j], pf_list[j + 1]
        if f_now["minute"] == 0:
            continue
        if f_now["current_gold"] >= GOLD_THRESH and f_next["current_gold"] < f_now["current_gold"] - SPEND_DROP:
            ts_ms = f_now["ts"]
            nearby = [
                e for e in events
                if e["type"] == "ELITE_MONSTER_KILL" and e.get("monsterType") == "DRAGON"
                and abs(e["timestamp"] - ts_ms) <= 90_000
            ]
            lines.append(f"[{f_now['minute']:02d}:00] High unspent gold: {f_now['current_gold']}g")
            if nearby:
                for d in nearby:
                    sub = (d.get("monsterSubType") or "Dragon").replace("_", " ").title()
                    dteam = _side(participants, d.get("killerId"), pt)
                    lines.append(f"  ! {sub} occurred nearby ({dteam}) — was recall timing hurting contest?")
            else:
                lines.append("  No objective clash — recall appears acceptable")
            lines.append("")

    return "\n".join(lines) if lines else "No key decision windows found.\n"


def _timeline(events: list[dict], participants: list[dict], player: dict) -> str:
    ppid = player["participantId"]
    pt = player["teamId"]
    KEY = {"CHAMPION_KILL", "ELITE_MONSTER_KILL", "DRAGON_SOUL_GIVEN", "BUILDING_KILL", "TURRET_PLATE_DESTROYED"}
    lines: list[str] = []

    for evt in events:
        etype = evt["type"]
        if etype not in KEY:
            continue
        ts = evt["timestamp"]
        t = _ts(ts)
        kid = evt.get("killerId")
        vid = evt.get("victimId")

        if etype == "CHAMPION_KILL":
            k, v = _pname(participants, kid), _pname(participants, vid)
            sk = _side(participants, kid, pt).upper()
            sv = _side(participants, vid, pt).upper()
            assists = [_pname(participants, a) for a in evt.get("assistingParticipantIds", []) if a != ppid]
            a_str = f" (+{', '.join(assists)})" if assists else ""
            if vid == ppid:
                lines.append(f"[{t}] YOU DIED    by {k}{a_str}")
            elif kid == ppid:
                lines.append(f"[{t}] YOU KILLED  {v}{a_str}")
            else:
                lines.append(f"[{t}] KILL        {sk} {k} -> {sv} {v}{a_str}")

        elif etype == "ELITE_MONSTER_KILL":
            monster = (evt.get("monsterSubType") or evt.get("monsterType") or "?").replace("_", " ").title()
            lines.append(f"[{t}] OBJECTIVE   {_side(participants, kid, pt).upper()} secured {monster}")

        elif etype == "DRAGON_SOUL_GIVEN":
            lines.append(f"[{t}] DRAGON SOUL {_side(participants, kid, pt).upper()} claimed dragon soul")

        elif etype == "BUILDING_KILL":
            ttype = evt.get("towerType", "").replace("_", " ")
            lane = evt.get("laneType", "").replace("_", " ")
            lines.append(f"[{t}] BUILDING    {_side(participants, kid, pt).upper()} destroyed {ttype} ({lane})")

        elif etype == "TURRET_PLATE_DESTROYED":
            who = "YOU" if kid == ppid else _side(participants, kid, pt).upper()
            lines.append(f"[{t}] PLATE       {who} took turret plate")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def build_coaching_report(
    match_data: dict,
    timeline_data: dict,
    puuid: str,
    game_name: str = "",
    tag_line: str = "",
) -> str:
    info = match_data["info"]
    participants = info["participants"]
    player = _find_player(participants, puuid, game_name, tag_line)
    if not player:
        return f"Error: Could not find {game_name}#{tag_line} in this match's participants."

    opponent = _find_opponent(participants, player)
    events = _parse_events(timeline_data)
    frames = _parse_frames(timeline_data)
    label = f"{game_name}#{tag_line}" if game_name else puuid[:16] + "..."

    return "\n".join([
        f"COACHING REPORT — {label}",
        "=" * 60,
        _match_header(info, player, participants),
        _sep("KEY DECISION WINDOWS"),
        _decision_windows(events, participants, player, frames),
        _sep("LANE PHASE SNAPSHOT"),
        _lane_phase(player, opponent, frames),
        _sep("DEATHS & AFTERMATH"),
        _deaths(events, participants, player, opponent, frames),
        _sep("FULL TIMELINE"),
        _timeline(events, participants, player),
    ])
