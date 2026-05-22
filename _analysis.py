"""Build a text coaching report directly from raw Riot match + timeline JSON."""
from __future__ import annotations

from datetime import datetime, timezone

_TOP_ZONES = {"top_lane", "blue_top_jungle", "red_top_jungle", "top_river"}
_ROTATABLE_ZONES = {"mid_lane", "bot_lane", "dragon_pit", "bot_river", "blue_bot_jungle", "red_bot_jungle"}
_OBJECTIVE_TYPES = {"ELITE_MONSTER_KILL"}
_MONSTER_TYPES = {"DRAGON", "BARON_NASHOR", "RIFTHERALD", "HORDE"}
_QUEUE_NAMES = {
    400: "Normal Draft", 420: "Ranked Solo/Duo", 430: "Normal Blind",
    440: "Ranked Flex", 450: "ARAM", 480: "Swiftplay", 490: "Quickplay",
    700: "Clash", 900: "URF", 1700: "Arena", 1900: "URF",
}


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


def _parse_events(timeline: dict) -> list[dict]:
    events = []
    for frame in timeline["info"]["frames"]:
        events.extend(frame["events"])
    return sorted(events, key=lambda e: e["timestamp"])


def _parse_frames(timeline: dict) -> dict[int, list[dict]]:
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
    return [e for e in events if start <= e["timestamp"] <= end and (types is None or e["type"] in types)]


def _find_player(participants: list[dict], puuid: str, game_name: str = "", tag_line: str = "") -> dict | None:
    p = next((p for p in participants if p["puuid"] == puuid), None)
    if p:
        return p
    if game_name:
        return next((
            p for p in participants
            if p.get("riotIdGameName", "").lower() == game_name.lower()
            and (not tag_line or p.get("riotIdTagline", "").lower() == tag_line.lower())
        ), None)
    return None


def _find_opponent(participants: list[dict], player: dict) -> dict | None:
    role = player.get("teamPosition") or ""
    opp_team = 200 if player["teamId"] == 100 else 100
    return next((p for p in participants if p["teamId"] == opp_team and p.get("teamPosition") == role), None) if role else None


def find_lane_opponent(match_data: dict, puuid: str, game_name: str = "", tag_line: str = "") -> dict | None:
    participants = match_data["info"]["participants"]
    player = _find_player(participants, puuid, game_name, tag_line)
    return _find_opponent(participants, player) if player else None


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


def _team_id(participants: list[dict], pid: int | None) -> int | None:
    if pid is None:
        return None
    p = next((x for x in participants if x["participantId"] == pid), None)
    return p.get("teamId") if p else None


def _ts(ms: int) -> str:
    m, s = divmod(ms // 1000, 60)
    return f"{m:02d}:{s:02d}"


def _dur(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    return f"{m}m{s:02d}s"


def _sep(title: str = "") -> str:
    line = "=" * 60
    return f"\n{line}\n{title}\n{line}" if title else f"\n{line}"


def _gold_flag(unspent: int) -> str:
    if unspent >= 2500:
        return f"  !! {unspent}g unspent - severe recall delay, high shutdown risk if caught"
    if unspent >= 1500:
        return f"  !  {unspent}g unspent - should have recalled before this engagement"
    if unspent >= 800:
        return f"  ~  {unspent}g unspent - slightly over-delayed recall"
    return ""


def _dragon_kills(events: list[dict]) -> list[dict]:
    return [e for e in events if e["type"] == "ELITE_MONSTER_KILL" and e.get("monsterType") == "DRAGON"]


def _objective_spawns_in_window(events: list[dict], ts: int, window_ms: int = 90_000) -> list[tuple[int, str]]:
    end = ts + window_ms
    upcoming: list[tuple[int, str]] = []
    dragons_before = [e for e in _dragon_kills(events) if e["timestamp"] <= ts]
    next_dragon = 5 * 60_000 if not dragons_before else dragons_before[-1]["timestamp"] + 5 * 60_000
    if ts < next_dragon <= end:
        upcoming.append((next_dragon, "Dragon spawning"))
    barons_before = [
        e for e in events
        if e["type"] == "ELITE_MONSTER_KILL" and e.get("monsterType") == "BARON_NASHOR" and e["timestamp"] <= ts
    ]
    next_baron = 20 * 60_000 if not barons_before else barons_before[-1]["timestamp"] + 6 * 60_000
    if ts < next_baron <= end:
        upcoming.append((next_baron, "Baron spawning"))
    return sorted(upcoming)


def _herald_available(events: list[dict], ts: int) -> bool:
    herald_taken = any(
        e["type"] == "ELITE_MONSTER_KILL" and e.get("monsterType") == "RIFTHERALD" and e["timestamp"] <= ts
        for e in events
    )
    return not herald_taken and ts < 20 * 60_000


def _top_side_activity(events: list[dict], participants: list[dict], player_id: int, ts: int, window_ms: int = 90_000) -> bool:
    start = ts - window_ms
    for e in events:
        if not (start <= e["timestamp"] <= ts):
            continue
        if e["type"] == "CHAMPION_KILL" and e.get("killerId") == player_id:
            return True
        if e["type"] == "BUILDING_KILL" and e.get("killerId") == player_id:
            pos = e.get("position", {})
            if _zone(pos.get("x"), pos.get("y")) in _TOP_ZONES:
                return True
    return False


def _top_tower_transition_near_dragon(events: list[dict], participants: list[dict], player_id: int, player_team: int, ts: int, window_ms: int = 90_000) -> bool:
    start, end = ts - window_ms, ts + window_ms
    top_towers = [
        e for e in events
        if start <= e["timestamp"] <= end
        and e["type"] == "BUILDING_KILL"
        and e.get("laneType") == "TOP_LANE"
        and e.get("towerType") in {"OUTER_TURRET", "INNER_TURRET"}
        and _side(participants, e.get("killerId"), player_team) == "ally"
    ]
    has_outer = any(e.get("towerType") == "OUTER_TURRET" for e in top_towers)
    player_inner = any(e.get("towerType") == "INNER_TURRET" and e.get("killerId") == player_id for e in top_towers)
    return has_outer and player_inner


def _dragon_label(involvement: str, secured_by: str, zone: str, events: list[dict], participants: list[dict], player_id: int, player_team: int, ts: int, pf_list: list[dict]) -> tuple[str, str, str]:
    if involvement in ("secured", "assisted"):
        return "good_objective_contribution", "Good presence at dragon. Look to extend this into a post-dragon push.", "high"
    if involvement == "nearby":
        if secured_by == "ally":
            return "good_objective_contribution", "You were close - ensure you were inside the pit contributing, not just adjacent.", "medium"
        return "missed_rotation", "You were at dragon but the enemy secured it. Check whether your team had priority to contest.", "medium"

    frames_before = [f for f in pf_list if ts - 60_000 <= f["ts"] < ts]
    if frames_before and any(_zone(f.get("x"), f.get("y")) in {"top_river", "bot_river", "dragon_pit"} for f in frames_before):
        return "too_late_to_rotate", "You were rotating toward dragon but arrived after it spawned or was taken. Track the spawn timer and start rotating ~45s earlier.", "medium"

    if zone in _TOP_ZONES:
        if secured_by == "ally" and _top_tower_transition_near_dragon(events, participants, player_id, player_team, ts):
            return "correct_trade", "Ally team secured dragon while you converted top outer into inner turret pressure. This is a valid cross-map trade; review only whether you could safely reset after.", "high"
        if _top_side_activity(events, participants, player_id, ts):
            return "correct_trade", "Top-side kill or tower pressure found - this trade may be valid. Verify the lead was meaningful and that the top-side advantage outweighed dragon value.", "medium"
        return "low_impact_absence", "You were top-side with no kill or tower found. This absence was likely unnecessary - rotate earlier when dragon is spawning.", "medium"
    if zone in _ROTATABLE_ZONES:
        return "missed_rotation", "You were in a position to rotate to dragon but did not. Prioritise dragon timer awareness and push your wave before the spawn window.", "high"
    if zone in ("blue_base", "red_base"):
        return "unclear_low_confidence", "You were recalling or in base - check if the back timing was forced or avoidable next replay.", "low"
    return "unclear_low_confidence", "Unable to determine reason for absence - review the replay for this dragon.", "low"


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
        f"Champion : {player['championName']} ({role})  -  {result}",
        f"Date     : {date_str}  |  {queue}  |  {_dur(duration)}",
        f"KDA      : {player['kills']}/{player['deaths']}/{player['assists']}",
        f"CS       : {cs} ({cs_pm:.1f}/min)  |  Gold: {player['goldEarned']:,}",
        f"Damage   : {player.get('totalDamageDealtToChampions', 0):,}  |  Vision: {player.get('visionScore', 0)}",
        "",
        f"YOUR TEAM : {my_team}",
        f"ENEMY TEAM: {enemy_team}",
    ])


def _opponent_context(opponent: dict | None, opponent_info: dict | None) -> str:
    if opponent is None:
        return "No direct lane opponent found."
    name = opponent.get("riotIdGameName") or opponent.get("summonerName") or opponent.get("championName", "Opponent")
    champ = opponent.get("championName", "?")
    lines = [f"{name} on {champ}"]
    mastery = (opponent_info or {}).get("champion_mastery") if opponent_info else None
    if not mastery:
        lines.append("Champion mastery: unavailable")
        lines.append("Interpretation: no API mastery context available; judge difficulty from lane data and replay.")
        return "\n".join(lines)
    points = int(mastery.get("championPoints", 0) or 0)
    level = mastery.get("championLevel", "?")
    if points >= 500_000:
        read = "champion specialist / likely comfort pick"
    elif points >= 150_000:
        read = "very experienced on this champion"
    elif points >= 50_000:
        read = "experienced on this champion"
    elif points >= 10_000:
        read = "some champion experience"
    else:
        read = "low recorded champion experience"
    lines.append(f"Champion mastery: level {level}, {points:,} points")
    lines.append(f"Interpretation: {read}. Treat this as context, not proof of player skill.")
    return "\n".join(lines)


def _lane_phase(player: dict, opponent: dict | None, frames: dict[int, list[dict]]) -> str:
    ppid = player["participantId"]
    opid = opponent["participantId"] if opponent else None
    pf_list = frames.get(ppid, [])
    of_list = frames.get(opid, []) if opid else []
    opp_label = opponent["championName"] if opponent else "-"
    lines = [f"vs {opp_label}", ""]
    header = f"{'Min':>3}  {'CS':>4}  {'Gold':>6}  {'Lvl':>3}"
    if opponent:
        header += f"    {'oCS':>4}  {'oGold':>6}  {'oLvl':>3}    {'+/-CS':>6}  {'+/-Gold':>8}"
    lines.append(header)
    lines.append("-" * len(header))
    for minute in [5, 10, 14]:
        pf = _at_minute(pf_list, minute)
        of = _at_minute(of_list, minute) if opid else None
        if not pf:
            continue
        row = f"{minute:>3}  {pf['cs']:>4}  {pf['total_gold']:>6}  {pf['level']:>3}"
        if opponent and of:
            row += f"    {of['cs']:>4}  {of['total_gold']:>6}  {of['level']:>3}    {pf['cs'] - of['cs']:>+6}  {pf['total_gold'] - of['total_gold']:>+8}"
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
        assists = [_pname(participants, a) for a in evt.get("assistingParticipantIds", [])]
        threat = f"killed by {killer}" + (f" (+{', '.join(assists)})" if assists else "")
        unspent = pf["current_gold"] if pf else 0
        level = pf["level"] if pf else "?"
        total_gold = pf["total_gold"] if pf else 0
        gold_lead = (pf["total_gold"] - of["total_gold"]) if (pf and of) else None
        lead_str = f"  gold lead vs laner: {gold_lead:+,}" if gold_lead is not None else ""
        pre_kills = [e for e in events if e["type"] == "CHAMPION_KILL" and e.get("killerId") == ppid and ts - 30_000 <= e["timestamp"] < ts]
        lines.append(f"Death #{i} @ {_ts(ts)}  zone: {zone}")
        lines.append(f"  {threat}  |  level {level}{lead_str}")
        if pre_kills:
            lines.append(f"  Classification: risky_overstay_but_traded (secured {len(pre_kills)} kill(s) in the 30s before dying)")
        if total_gold >= 6000:
            lines.append(f"  !! Shutdown risk - {total_gold:,}g total (high-value target)")
        gold_flag = _gold_flag(unspent)
        if gold_flag:
            lines.append(gold_flag)
        for k in [e for e in events if e["type"] == "CHAMPION_KILL" and e.get("killerId") == ppid and ts < e["timestamp"] <= ts + 60_000][:3]:
            elapsed = (k["timestamp"] - ts) // 1000
            lines.append(f"  Player post-death kill: {_pname(participants, k.get('victimId'))} at {_ts(k['timestamp'])} (+{elapsed}s)")
        for window_end, label in [(15_000, "15s"), (30_000, "30s"), (60_000, "60s")]:
            window_start = ts + (0 if label == "15s" else (15_000 if label == "30s" else 30_000))
            ally_kills = [e for e in events if e["type"] == "CHAMPION_KILL" and window_start < e["timestamp"] <= ts + window_end and _side(participants, e.get("killerId"), pt) == "ally"]
            if ally_kills:
                lines.append(f"  Allies: {len(ally_kills)} kill(s) within {label} of your death")
        after = _window(events, ts, ts + 90_000)
        objs = [e for e in after if e["type"] in _OBJECTIVE_TYPES and e.get("monsterType") in _MONSTER_TYPES]
        towers = [e for e in after if e["type"] == "BUILDING_KILL"]
        for o in objs[:2]:
            sub = (o.get("monsterSubType") or o.get("monsterType") or "objective").replace("_", " ").title()
            elapsed = (o["timestamp"] - ts) // 1000
            lines.append(f"  -> {_ts(o['timestamp'])}  {_side(participants, o.get('killerId'), pt)} took {sub} (occurred within {elapsed}s of death)")
        for t in towers[:2]:
            elapsed = (t["timestamp"] - ts) // 1000
            lane = t.get("laneType", "?").replace("_", " ")
            lines.append(f"  -> {_ts(t['timestamp'])}  {_side(participants, t.get('killerId'), pt)} tower fell {lane} (occurred within {elapsed}s of death)")
        if not objs and not towers:
            lines.append("  -> No objectives or towers within 90s of death")
        lines.append("")
    return "\n".join(lines)


def _decision_windows(events: list[dict], participants: list[dict], player: dict, frames: dict[int, list[dict]]) -> str:
    ppid = player["participantId"]
    pt = player["teamId"]
    pf_list = frames.get(ppid, [])
    lines: list[str] = []
    dragons = _dragon_kills(events)
    for i, evt in enumerate(dragons[:2]):
        label_num = "1st" if i == 0 else "2nd"
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
        assessment, recommendation, confidence = _dragon_label(involvement, secured_by, zone, events, participants, ppid, pt, ts, pf_list)
        display_zone = zone
        if confidence != "low" and involvement in {"secured", "assisted"} and zone not in {"dragon_pit", "unknown"}:
            display_zone = f"dragon_area (timeline: {zone})"
        after = _window(events, ts, ts + 90_000)
        kills_n = len([e for e in after if e["type"] == "CHAMPION_KILL"])
        towers_n = len([e for e in after if e["type"] == "BUILDING_KILL"])
        lines.append(f"[{_ts(ts)}] {label_num} Dragon - {sub}")
        lines.append("  Facts:")
        lines.append(f"    Secured by {secured_by}  |  player zone: {display_zone}  |  involvement: {involvement}" + (f"  |  {gold:,}g" if gold else ""))
        if kills_n or towers_n:
            lines.append(f"    Next 90s: {kills_n} kill(s), {towers_n} tower(s)")
        lines.append(f"  Interpretation: {assessment}")
        lines.append(f"  Recommendation : {recommendation}")
        lines.append(f"  Confidence     : {confidence}")
        lines.append("")

    top_outer = [e for e in events if e["type"] == "BUILDING_KILL" and e.get("towerType") == "OUTER_TURRET" and e.get("laneType") == "TOP_LANE"]
    ally_top = next((e for e in top_outer if _side(participants, e.get("killerId"), pt) == "ally"), None)
    enemy_top = next((e for e in top_outer if _side(participants, e.get("killerId"), pt) == "enemy"), None)
    for tag, evt, rec in [
        ("Ally took TOP LANE outer turret", ally_top, "Rotate to contest dragon or secure Rift Herald vision. Don't stay split-pushing with no priority objective available."),
        ("Enemy took TOP LANE outer turret", enemy_top, "You will face dive pressure top. Play closer to your tower or look for a counter-play trade on another objective before they push further."),
    ]:
        if evt is None:
            continue
        ts = evt["timestamp"]
        pf_after = [f for f in pf_list if ts < f["ts"] <= ts + 90_000]
        path = " -> ".join(_zone(f["x"], f["y"]) for f in pf_after[:3]) or "unknown"
        after = _window(events, ts, ts + 90_000)
        objs_after = [e for e in after if e["type"] in _OBJECTIVE_TYPES and e.get("monsterType") in _MONSTER_TYPES]
        upcoming = _objective_spawns_in_window(events, ts, 90_000)
        if upcoming:
            next_label = ", ".join(f"{name} at {_ts(spawn_ts)}" for spawn_ts, name in upcoming)
            rec = f"Move first for {next_label}; spend or reset only if you can arrive before spawn."
        elif "Ally took" in tag and _herald_available(events, ts):
            rec = "Use top priority to secure Rift Herald vision or reset before the next cross-map play."
        elif "Ally took" in tag:
            rec = "No major objective is spawning immediately; reset, spend gold, then place deeper vision."
        lines.append(f"[{_ts(ts)}] {tag}")
        lines.append("  Facts:")
        lines.append(f"    Player path after: {path}")
        if objs_after:
            for o in objs_after[:2]:
                sub = (o.get("monsterSubType") or o.get("monsterType") or "obj").replace("_", " ").title()
                elapsed = (o["timestamp"] - ts) // 1000
                lines.append(f"    -> {_ts(o['timestamp'])}  {_side(participants, o.get('killerId'), pt)} took {sub} (occurred within {elapsed}s)")
        elif upcoming:
            for spawn_ts, name in upcoming:
                lines.append(f"    -> {name} at {_ts(spawn_ts)} (inside next 90s)")
        else:
            lines.append("    No objectives taken or spawning within 90s")
        lines.append("  Interpretation: tower transition window")
        lines.append(f"  Recommendation : {rec}")
        lines.append("  Confidence     : medium")
        lines.append("")

    gold_thresh, spend_drop = 1500, 500
    for j in range(len(pf_list) - 1):
        f_now, f_next = pf_list[j], pf_list[j + 1]
        if f_now["minute"] == 0 or f_now["current_gold"] < gold_thresh:
            continue
        if f_next["current_gold"] >= f_now["current_gold"] - spend_drop:
            continue
        ts_ms = f_now["ts"]
        gained_30s = [
            e for e in events
            if ts_ms < e["timestamp"] <= ts_ms + 30_000
            and ((e["type"] in _OBJECTIVE_TYPES and e.get("monsterType") in _MONSTER_TYPES) or e["type"] == "BUILDING_KILL")
        ]
        if gained_30s:
            lines.append(f"[{f_now['minute']:02d}:00] High unspent gold: {f_now['current_gold']}g")
            lines.append("  Facts:")
            for e in gained_30s[:2]:
                elapsed = (e["timestamp"] - ts_ms) // 1000
                team = _side(participants, e.get("killerId"), pt)
                if e["type"] == "BUILDING_KILL":
                    name = f"{e.get('towerType', 'tower').replace('_', ' ').title()} ({e.get('laneType', '?').replace('_', ' ')})"
                    lines.append(f"    -> {_ts(e['timestamp'])}  {team} destroyed {name} (+{elapsed}s)")
                else:
                    name = (e.get("monsterSubType") or e.get("monsterType") or "objective").replace("_", " ").title()
                    lines.append(f"    -> {_ts(e['timestamp'])}  {team} secured {name} (+{elapsed}s)")
            lines.append("  Interpretation: acceptable_greed")
            lines.append("  Recommendation : Good conversion window. Spend on the next reset before taking another fight.")
            lines.append("  Confidence     : medium")
            lines.append("")
            continue
        contested_90s = [e for e in events if e["type"] == "ELITE_MONSTER_KILL" and e.get("monsterType") == "DRAGON" and abs(e["timestamp"] - ts_ms) <= 90_000]
        upcoming_90s = _objective_spawns_in_window(events, ts_ms, 90_000)
        lines.append(f"[{f_now['minute']:02d}:00] High unspent gold: {f_now['current_gold']}g")
        lines.append("  Facts:")
        if contested_90s:
            ally_secured = any(_side(participants, d.get("killerId"), pt) == "ally" for d in contested_90s)
            for d in contested_90s:
                sub = (d.get("monsterSubType") or "Dragon").replace("_", " ").title()
                dteam = _side(participants, d.get("killerId"), pt)
                elapsed = abs(d["timestamp"] - ts_ms) // 1000
                if dteam == "ally":
                    lines.append(f"    ~ {sub} occurred within {elapsed}s ({dteam}) - your team secured it, so this is a lower-urgency reset review")
                else:
                    lines.append(f"    ! {sub} occurred within {elapsed}s ({dteam}) - recall timing may have hurt your ability to contest")
            if ally_secured:
                lines.append("  Interpretation: team secured nearby objective despite high unspent gold")
                lines.append("  Recommendation : Since the team secured the objective, focus on spending cleanly after the play.")
                lines.append("  Confidence     : medium")
            else:
                lines.append("  Interpretation: high gold likely reduced contest power")
                lines.append("  Recommendation : Recall between waves well before the dragon spawn window, not during it.")
                lines.append("  Confidence     : high")
        elif upcoming_90s:
            for spawn_ts, name in upcoming_90s:
                elapsed = (spawn_ts - ts_ms) // 1000
                lines.append(f"    -> {name} in {elapsed}s at {_ts(spawn_ts)}")
            lines.append("  Interpretation: high gold before an objective spawn window")
            lines.append("  Recommendation : Recall immediately if safe, or avoid fighting until the gold is spent.")
            lines.append("  Confidence     : medium")
        else:
            lines.append("    No objective clash within 90s - recall timing appears acceptable here")
            lines.append("  Interpretation: low objective pressure during reset window")
            lines.append("  Recommendation : Fine to recall, but spending gold sooner accelerates your power spike.")
            lines.append("  Confidence     : medium")
        lines.append("")
    return "\n".join(lines) if lines else "No key decision windows found.\n"


def _timeline(events: list[dict], participants: list[dict], player: dict, info: dict) -> str:
    ppid = player["participantId"]
    pt = player["teamId"]
    key = {"CHAMPION_KILL", "ELITE_MONSTER_KILL", "DRAGON_SOUL_GIVEN", "BUILDING_KILL", "TURRET_PLATE_DESTROYED"}
    dragon_counts = {100: 0, 200: 0}
    lines: list[str] = []
    for evt in events:
        etype = evt["type"]
        if etype not in key:
            continue
        ts = evt["timestamp"]
        t = _ts(ts)
        kid = evt.get("killerId")
        vid = evt.get("victimId")
        kid_team = _team_id(participants, kid)
        if etype == "CHAMPION_KILL":
            k, v = _pname(participants, kid), _pname(participants, vid)
            sk, sv = _side(participants, kid, pt).upper(), _side(participants, vid, pt).upper()
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
            if evt.get("monsterType") == "DRAGON" and kid_team in dragon_counts:
                dragon_counts[kid_team] += 1
            lines.append(f"[{t}] OBJECTIVE   {_side(participants, kid, pt).upper()} secured {monster}")
        elif etype == "DRAGON_SOUL_GIVEN":
            team_id = evt.get("teamId")
            if team_id in dragon_counts and dragon_counts[team_id] >= 4:
                team_label = "YOUR TEAM" if team_id == pt else "ENEMY TEAM"
                lines.append(f"[{t}] DRAGON SOUL {team_label} achieved dragon soul")
        elif etype == "BUILDING_KILL":
            ttype = evt.get("towerType", "").replace("_", " ")
            lane = evt.get("laneType", "").replace("_", " ")
            lines.append(f"[{t}] BUILDING    {_side(participants, kid, pt).upper()} destroyed {ttype} ({lane})")
        elif etype == "TURRET_PLATE_DESTROYED":
            if info.get("mapId") == 11 and ts > 14 * 60_000:
                continue
            who = "YOU" if kid == ppid else _side(participants, kid, pt).upper()
            lines.append(f"[{t}] PLATE       {who} took turret plate")
    return "\n".join(lines)


def _teemo_shrooms(events: list[dict], participants: list[dict], player: dict, dragon_events: list[dict]) -> str | None:
    if player.get("championName", "").lower() != "teemo":
        return None
    ppid = player["participantId"]
    shrooms = [e for e in events if e["type"] == "WARD_PLACED" and e.get("wardType") == "TEEMO_MUSHROOM" and e.get("creatorId") == ppid]
    if not shrooms:
        return "No Teemo shrooms recorded in timeline data."
    early = sum(1 for e in shrooms if e["timestamp"] < 10 * 60_000)
    mid = sum(1 for e in shrooms if 10 * 60_000 <= e["timestamp"] < 20 * 60_000)
    late = sum(1 for e in shrooms if e["timestamp"] >= 20 * 60_000)
    lines = [
        f"Total shrooms placed: {len(shrooms)}  (early <10m: {early}  |  mid 10-20m: {mid}  |  late >20m: {late})",
        "  Note: Riot timeline data does not include shroom placement coordinates.",
    ]
    for evt in dragon_events[:2]:
        ts = evt["timestamp"]
        sub = (evt.get("monsterSubType") or "Dragon").replace("_", " ").title()
        around = [e for e in shrooms if abs(e["timestamp"] - ts) <= 120_000]
        lines.append(f"  {sub}: {len(around)} shroom(s) placed within 2 min of dragon spawn" if around else f"  {sub}: no shrooms placed within 2 min of dragon spawn")
    return "\n".join(lines)


def build_coaching_report(match_data: dict, timeline_data: dict, puuid: str, game_name: str = "", tag_line: str = "", opponent_info: dict | None = None) -> str:
    info = match_data["info"]
    participants = info["participants"]
    player = _find_player(participants, puuid, game_name, tag_line)
    if not player:
        return f"Error: Could not find {game_name}#{tag_line} in this match's participants."
    opponent = _find_opponent(participants, player)
    events = _parse_events(timeline_data)
    frames = _parse_frames(timeline_data)
    label = f"{game_name}#{tag_line}" if game_name else puuid[:16] + "..."
    sections = [
        f"COACHING REPORT - {label}",
        "=" * 60,
        _match_header(info, player, participants),
        _sep("KEY DECISION WINDOWS"),
        _decision_windows(events, participants, player, frames),
        _sep("LANE OPPONENT CONTEXT"),
        _opponent_context(opponent, opponent_info),
        _sep("LANE PHASE SNAPSHOT"),
        _lane_phase(player, opponent, frames),
        _sep("DEATHS & AFTERMATH"),
        _deaths(events, participants, player, opponent, frames),
    ]
    dragon_events = _dragon_kills(events)
    shroom_section = _teemo_shrooms(events, participants, player, dragon_events)
    if shroom_section is not None:
        sections.append(_sep("TEEMO SHROOM USAGE"))
        sections.append(shroom_section)
    sections.append(_sep("FULL TIMELINE"))
    sections.append(_timeline(events, participants, player, info))
    return "\n".join(sections)
