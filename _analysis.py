"""Build a text coaching report directly from raw Riot match + timeline JSON."""
from __future__ import annotations

import json
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


def _gold_severity(unspent: int) -> str:
    if unspent >= 2500:
        return "severe"
    if unspent >= 1500:
        return "high"
    if unspent >= 1000:
        return "medium"
    return "low"


_TRINKET_IDS = {3330, 3340, 3348, 3363, 3364}
_ELIXIR_IDS = {2138, 2139, 2140}
_CONTROL_WARD_ID = 2055
_BOOT_IDS = {
    1001, 3006, 3009, 3020, 3047, 3111, 3117, 3158, 3170, 2422,
    2423, 2424, 2425, 2426, 2427, 2428, 2429, 2430,
}
_ITEM_NAMES = {
    1001: "Boots", 1026: "Blasting Wand", 1028: "Ruby Crystal", 1031: "Chain Vest", 1033: "Null-Magic Mantle",
    1043: "Recurve Bow", 1052: "Amplifying Tome", 1056: "Doran's Ring", 1058: "Needlessly Large Rod", 1082: "Dark Seal", 2003: "Health Potion",
    2021: "Tunneler", 2031: "Refillable Potion", 2055: "Control Ward", 2138: "Elixir of Iron",
    2139: "Elixir of Sorcery", 2140: "Elixir of Wrath", 2504: "Kaenic Rookern", 2508: "Fated Ashes", 2510: "Dusk and Dawn", 2420: "Seeker's Armguard",
    2421: "Shattered Armguard", 2422: "Slightly Magical Footwear", 2423: "Perfectly Timed Boots",
    2424: "Broken Stopwatch", 2425: "Stopwatch", 2426: "Broken Stopwatch", 2427: "Broken Stopwatch",
    2428: "Broken Stopwatch", 2429: "Broken Stopwatch", 2430: "Broken Stopwatch",
    3001: "Abyssal Mask", 3002: "Trailblazer", 3003: "Archangel's Staff", 3004: "Manamune",
    3006: "Berserker's Greaves", 3009: "Boots of Swiftness", 3020: "Sorcerer's Shoes",
    3024: "Glacial Buckler", 3026: "Guardian Angel", 3031: "Infinity Edge", 3040: "Seraph's Embrace",
    3041: "Mejai's Soulstealer", 3042: "Muramana", 3046: "Phantom Dancer", 3047: "Plated Steelcaps",
    3050: "Zeke's Convergence", 3053: "Sterak's Gage", 3065: "Spirit Visage",
    3068: "Sunfire Aegis", 3071: "Black Cleaver", 3072: "Bloodthirster",
    3073: "Experimental Hexplate", 3074: "Ravenous Hydra", 3075: "Thornmail",
    3083: "Warmog's Armor", 3084: "Heartsteel", 3085: "Runaan's Hurricane",
    3087: "Statikk Shiv", 3089: "Rabadon's Deathcap", 3091: "Wit's End",
    3094: "Rapid Firecannon", 3100: "Lich Bane", 3102: "Banshee's Veil",
    3107: "Redemption", 3109: "Knight's Vow", 3110: "Frozen Heart",
    3111: "Mercury's Treads", 3115: "Nashor's Tooth", 3116: "Rylai's Crystal Scepter",
    3117: "Mobility Boots", 3121: "Fimbulwinter", 3124: "Guinsoo's Rageblade",
    3135: "Void Staff", 3137: "Cryptbloom", 3139: "Mercurial Scimitar",
    3108: "Fiendish Codex", 3113: "Aether Wisp", 3142: "Youmuu's Ghostblade", 3143: "Randuin's Omen", 3145: "Hextech Alternator", 3146: "Hextech Rocketbelt",
    3152: "Hextech Rocketbelt", 3153: "Blade of the Ruined King", 3156: "Maw of Malmortius",
    3157: "Zhonya's Hourglass", 3158: "Ionian Boots of Lucidity", 3161: "Spear of Shojin",
    3165: "Morellonomicon", 3170: "Symbiotic Soles", 3172: "Zephyr", 3177: "Guardian's Blade",
    3179: "Umbral Glaive", 3190: "Locket of the Iron Solari", 3193: "Gargoyle Stoneplate",
    3222: "Mikael's Blessing", 3302: "Terminus", 3330: "Scarecrow Effigy",
    3340: "Stealth Ward", 3348: "Arcane Sweeper", 3363: "Farsight Alteration",
    3364: "Oracle Lens", 3504: "Ardent Censer", 3508: "Essence Reaver",
    3742: "Dead Man's Plate", 3748: "Titanic Hydra", 3802: "Lost Chapter",
    3814: "Edge of Night", 3865: "World Atlas", 3871: "Celestial Opposition",
    3876: "Solstice Sleigh", 3905: "Imperial Mandate", 4401: "Force of Nature",
    3916: "Oblivion Orb", 4628: "Horizon Focus", 4629: "Cosmic Drive", 4630: "Blighting Jewel",
    4632: "Verdant Barrier", 4633: "Riftmaker", 4636: "Night Harvester",
    4637: "Demonic Embrace", 4638: "Watchful Wardstone", 4642: "Bandleglass Mirror",
    4643: "Vigilant Wardstone", 4644: "Crown of the Shattered Queen",
    4645: "Shadowflame", 4646: "Stormsurge", 4629: "Cosmic Drive",
    6653: "Liandry's Torment", 6655: "Luden's Companion", 6657: "Rod of Ages",
    6659: "Blackfire Torch", 6662: "Iceborn Gauntlet", 6664: "Hollow Radiance",
    6665: "Jak'Sho, The Protean", 6667: "Radiant Virtue", 6672: "Kraken Slayer",
    6673: "Immortal Shieldbow", 6675: "Navori Flickerblade", 6676: "The Collector",
    6692: "Eclipse", 6694: "Serylda's Grudge", 6695: "Serpent's Fang",
    6696: "Axiom Arc", 6697: "Hubris", 6698: "Profane Hydra", 6699: "Voltaic Cyclosword",
    6701: "Opportunity", 6610: "Sundered Sky", 6616: "Staff of Flowing Water",
    6617: "Moonstone Renewer", 6620: "Echoes of Helia", 6621: "Dawncore",
    6631: "Stridebreaker", 6632: "Divine Sunderer", 6660: "Bami's Cinder",
    6333: "Death's Dance", 6693: "Prowler's Claw", 6694: "Serylda's Grudge",
}


def _item_name(item_id: int) -> str:
    return _ITEM_NAMES.get(item_id, f"Item {item_id}")


def _remove_one(items: list[int], item_id: int) -> None:
    if item_id in items:
        items.remove(item_id)


def _inventory_state(events: list[dict], participant_id: int, ts: int, unspent: int, level: int | str = "?") -> dict:
    items: list[int] = []
    latest_purchase_ts: int | None = None
    elixir_event_ts: int | None = None
    saw_item_event = False
    for evt in sorted(events, key=lambda e: e["timestamp"]):
        if evt["timestamp"] > ts:
            break
        if evt.get("participantId") != participant_id:
            continue
        etype = evt.get("type")
        item_id = int(evt.get("itemId") or 0)
        if etype == "ITEM_PURCHASED" and item_id:
            saw_item_event = True
            latest_purchase_ts = evt["timestamp"]
            if item_id in _ELIXIR_IDS:
                elixir_event_ts = evt["timestamp"]
            else:
                items.append(item_id)
        elif etype in {"ITEM_SOLD", "ITEM_DESTROYED"} and item_id:
            saw_item_event = True
            if item_id in _ELIXIR_IDS:
                elixir_event_ts = evt["timestamp"]
            _remove_one(items, item_id)
        elif etype == "ITEM_UNDO":
            saw_item_event = True
            before_id = int(evt.get("beforeId") or 0)
            after_id = int(evt.get("afterId") or 0)
            if before_id:
                _remove_one(items, before_id)
            if after_id and after_id not in _ELIXIR_IDS:
                items.append(after_id)

    non_trinket_items = [item_id for item_id in items if item_id not in _TRINKET_IDS]
    slot_items = [item_id for item_id in non_trinket_items if item_id not in _ELIXIR_IDS]
    slot_count = min(len(slot_items), 6)
    six_slotted = slot_count >= 6
    boots_present = any(item_id in _BOOT_IDS for item_id in non_trinket_items)
    level_num = int(level) if isinstance(level, int) or (isinstance(level, str) and level.isdigit()) else 0
    elixir_purchasable = level_num >= 9 and unspent >= 500
    elixir_active = elixir_event_ts is not None and ts - elixir_event_ts <= 180_000
    slot_space = slot_count < 6

    if not saw_item_event:
        actionability = "unknown"
        upgrade_available = "unknown"
    elif six_slotted:
        actionability = "low"
        upgrade_available = "not directly; inventory was six-slotted, so upgrades require selling/swapping"
    elif unspent >= 1500:
        actionability = "likely_actionable"
        upgrade_available = "likely yes; open item slots plus 1500g+ usually means spendable combat power"
    elif unspent >= 1000:
        actionability = "possibly_actionable"
        upgrade_available = "possible component purchase; exact build path not proven"
    else:
        actionability = "low"
        upgrade_available = "unlikely major upgrade from unspent gold alone"

    defensive_swap = (
        "possible to evaluate after selling/swapping a slot" if six_slotted and unspent >= 1500
        else "possible if an open slot and matchup-specific component/item fits" if slot_space and unspent >= 1000
        else "unlikely from gold alone"
    )
    consumable_possible = (
        "control ward/consumable purchase possible if shop access and slot space existed"
        if slot_space and unspent >= 75
        else "no open non-trinket slot for control ward/consumable unless selling/swapping"
    )
    latest_purchase = _ts(latest_purchase_ts) if latest_purchase_ts is not None else "unknown"
    return {
        "items": non_trinket_items,
        "slot_count": slot_count,
        "six_slotted": six_slotted,
        "boots_present": boots_present,
        "latest_purchase_ts": latest_purchase_ts,
        "latest_purchase": latest_purchase,
        "elixir_active": elixir_active,
        "elixir_purchasable": elixir_purchasable,
        "upgrade_available": upgrade_available,
        "defensive_swap": defensive_swap,
        "consumable_possible": consumable_possible,
        "actionability": actionability,
        "item_data_missing": not saw_item_event,
    }


def _inventory_lines(state: dict, prefix: str = "- ") -> list[str]:
    items = state["items"]
    item_text = ", ".join(f"{item_id}:{_item_name(item_id)}" for item_id in items) if items else "none reconstructed"
    return [
        f"{prefix}Current items excluding trinket: {item_text}",
        f"{prefix}Item slots used excluding trinket: {state['slot_count']}/6; six_slotted={str(state['six_slotted']).lower()}; boots_present={str(state['boots_present']).lower()}",
        f"{prefix}Latest purchase timestamp: {state['latest_purchase']}",
        f"{prefix}Elixir active/purchasable: active={str(state['elixir_active']).lower()}, purchasable={str(state['elixir_purchasable']).lower()}",
        f"{prefix}Meaningful item upgrade available: {state['upgrade_available']}",
        f"{prefix}Defensive swap availability: {state['defensive_swap']}",
        f"{prefix}Control ward/consumable: {state['consumable_possible']}",
    ]


def _gold_actionability_label(unspent: int, item_state: dict | None) -> str:
    if not item_state or item_state.get("item_data_missing"):
        return f"{_gold_severity(unspent)}_provisional_item_state_unknown"
    if item_state.get("six_slotted") and unspent >= 1500:
        return "unspent_gold_low_actionability_six_slotted"
    if item_state.get("actionability") == "likely_actionable" and unspent >= 1500:
        return f"{_gold_severity(unspent)}_likely_actionable"
    return f"{_gold_severity(unspent)}_{item_state.get('actionability', 'unknown')}"


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


def _event_team(evt: dict, participants: list[dict]) -> int | None:
    if evt.get("type") == "ELITE_MONSTER_KILL":
        team_id = evt.get("killerTeamId")
        if team_id in (100, 200):
            return team_id
    if evt.get("type") == "BUILDING_KILL":
        destroyed_team = evt.get("teamId")
        if destroyed_team == 100:
            return 200
        if destroyed_team == 200:
            return 100
    return _team_id(participants, evt.get("killerId"))


def _event_side(evt: dict, participants: list[dict], player_team: int) -> str:
    team_id = _event_team(evt, participants)
    if team_id == player_team:
        return "ally"
    if team_id in (100, 200):
        return "enemy"
    return "?"


def _objective_name(evt: dict) -> str:
    return (evt.get("monsterSubType") or evt.get("monsterType") or "objective").replace("_", " ").title()


def _building_name(evt: dict) -> str:
    tower = evt.get("towerType") or evt.get("buildingType") or "building"
    lane = evt.get("laneType") or "?"
    return f"{tower.replace('_', ' ').title()} ({lane.replace('_', ' ')})"


def _meaningful_events(events: list[dict], start: int, end: int) -> list[dict]:
    return [
        e for e in events
        if start <= e["timestamp"] <= end
        and (
            (e["type"] in _OBJECTIVE_TYPES and e.get("monsterType") in _MONSTER_TYPES)
            or e["type"] == "BUILDING_KILL"
        )
    ]


def _building_victim_side(evt: dict, participants: list[dict], player_team: int) -> str:
    destroyed_team = evt.get("teamId")
    if destroyed_team == player_team:
        return "ALLY"
    if destroyed_team in (100, 200):
        return "ENEMY"
    return "UNKNOWN"


def _format_meaningful(evt: dict, participants: list[dict], player_team: int, base_ts: int) -> str:
    elapsed = (evt["timestamp"] - base_ts) // 1000
    side = _event_side(evt, participants, player_team)
    direction = f"+{elapsed}s" if elapsed >= 0 else f"{elapsed}s"
    if evt["type"] == "BUILDING_KILL":
        actor = side.upper()
        target = _building_victim_side(evt, participants, player_team)
        return f"{actor} destroyed {target} {_building_name(evt)} at {_ts(evt['timestamp'])} ({direction})"
    return f"{side} secured {_objective_name(evt)} at {_ts(evt['timestamp'])} ({direction})"


def _kill_desc(evt: dict, participants: list[dict], player_team: int, player_id: int) -> str:
    killer_id = evt.get("killerId")
    victim_id = evt.get("victimId")
    killer = "YOU" if killer_id == player_id else _pname(participants, killer_id)
    victim = "YOU" if victim_id == player_id else _pname(participants, victim_id)
    killer_side = _side(participants, killer_id, player_team)
    victim_side = _side(participants, victim_id, player_team)
    assists = [
        ("YOU" if a == player_id else _pname(participants, a))
        for a in evt.get("assistingParticipantIds", [])
    ]
    assist_text = f" (+{', '.join(assists)})" if assists else ""
    return f"{_ts(evt['timestamp'])} {killer_side} {killer} killed {victim_side} {victim}{assist_text}"


def _kill_counts(kills: list[dict], participants: list[dict], player_team: int) -> tuple[int, int]:
    allied_deaths = sum(1 for e in kills if _side(participants, e.get("victimId"), player_team) == "ally")
    enemy_deaths = sum(1 for e in kills if _side(participants, e.get("victimId"), player_team) == "enemy")
    return allied_deaths, enemy_deaths


def _participant_frame(frames: dict[int, list[dict]], participant_id: int, ts: int) -> dict | None:
    return _nearest(frames.get(participant_id, []), ts)


def _dist_sq(a: dict, b: dict) -> int | None:
    if a.get("x") is None or a.get("y") is None or b.get("x") is None or b.get("y") is None:
        return None
    dx = a["x"] - b["x"]
    dy = a["y"] - b["y"]
    return dx * dx + dy * dy


def _nearby_counts(frames: dict[int, list[dict]], participants: list[dict], player: dict, ts: int, radius: int = 3500) -> tuple[int, int, list[str], list[str]]:
    pframe = _participant_frame(frames, player["participantId"], ts)
    if not pframe:
        return 0, 0, [], []
    radius_sq = radius * radius
    allies: list[str] = []
    enemies: list[str] = []
    for p in participants:
        if p["participantId"] == player["participantId"]:
            continue
        frame = _participant_frame(frames, p["participantId"], ts)
        if not frame:
            continue
        dist = _dist_sq(pframe, frame)
        if dist is None or dist > radius_sq:
            continue
        label = f"{p['championName']}:{_zone(frame.get('x'), frame.get('y'))}"
        if p["teamId"] == player["teamId"]:
            allies.append(label)
        else:
            enemies.append(label)
    return len(allies), len(enemies), allies, enemies


def _position_samples(frames: dict[int, list[dict]], participants: list[dict], player: dict, ts: int) -> list[str]:
    samples: list[str] = []
    for offset_s in [-30, -15, -5, 0, 5, 15, 30]:
        sample_ts = ts + offset_s * 1000
        pframe = _participant_frame(frames, player["participantId"], sample_ts)
        if not pframe:
            samples.append(f"{offset_s:+d}s: player unknown")
            continue
        ally_n, enemy_n, allies, enemies = _nearby_counts(frames, participants, player, sample_ts)
        sample_age = abs(pframe["ts"] - sample_ts) // 1000
        samples.append(
            f"{offset_s:+d}s: player {_zone(pframe.get('x'), pframe.get('y'))} "
            f"(frame +/-{sample_age}s), allies nearby {ally_n}, enemies nearby {enemy_n}"
            + (f" | allies: {', '.join(allies[:3])}" if allies else "")
            + (f" | enemies: {', '.join(enemies[:3])}" if enemies else "")
        )
    return samples


def _objective_state(events: list[dict], participants: list[dict], ts: int, monster_type: str, first_spawn: int, respawn: int) -> str:
    kills = [
        e for e in events
        if e["type"] == "ELITE_MONSTER_KILL"
        and e.get("monsterType") == monster_type
        and e["timestamp"] <= ts
    ]
    next_spawn = first_spawn if not kills else kills[-1]["timestamp"] + respawn
    if ts >= next_spawn:
        return "alive/contestable"
    return f"dead/spawning in {(next_spawn - ts) // 1000}s"


def _dragon_soul_times(events: list[dict], participants: list[dict]) -> dict[int, int]:
    counts = {100: 0, 200: 0}
    soul_times: dict[int, int] = {}
    for evt in events:
        if evt["type"] != "ELITE_MONSTER_KILL" or evt.get("monsterType") != "DRAGON":
            continue
        if evt.get("monsterSubType") == "ELDER_DRAGON":
            continue
        team_id = _event_team(evt, participants)
        if team_id not in counts or team_id in soul_times:
            continue
        counts[team_id] += 1
        if counts[team_id] >= 4:
            soul_times[team_id] = evt["timestamp"]
    return soul_times


def _objective_state_lines(events: list[dict], participants: list[dict], player_team: int, ts: int) -> list[str]:
    soul_times = _dragon_soul_times(events, participants)
    soul_before = [t for t in soul_times.values() if t <= ts]
    lines = [
        f"Baron: {_objective_state(events, participants, ts, 'BARON_NASHOR', 20 * 60_000, 6 * 60_000)}",
    ]
    if soul_before:
        lines.append("Dragon: replaced by Elder after Dragon Soul")
        elder_kills = [
            e for e in events
            if e["type"] == "ELITE_MONSTER_KILL"
            and e.get("monsterType") == "DRAGON"
            and e.get("monsterSubType") == "ELDER_DRAGON"
            and e["timestamp"] <= ts
        ]
        first_elder = min(soul_before) + 6 * 60_000
        next_elder = first_elder if not elder_kills else elder_kills[-1]["timestamp"] + 6 * 60_000
        if ts >= next_elder:
            lines.append("Elder: alive/contestable")
        else:
            lines.append(f"Elder: dead/spawning in {(next_elder - ts) // 1000}s")
    else:
        lines.append(f"Dragon: {_objective_state(events, participants, ts, 'DRAGON', 5 * 60_000, 5 * 60_000)}")
        lines.append("Elder: not available yet (no Dragon Soul)")
    return lines


def _recent_major_objective(events: list[dict], participants: list[dict], player_team: int, ts: int, window_ms: int = 60_000) -> tuple[str | None, dict | None]:
    soul_times = _dragon_soul_times(events, participants)
    for team_id, soul_ts in soul_times.items():
        if team_id == player_team and ts - window_ms <= soul_ts <= ts:
            return "post_soul_overfight", {"timestamp": soul_ts, "name": "Dragon Soul"}
    for evt in reversed(events):
        if not (ts - window_ms <= evt["timestamp"] <= ts):
            continue
        if evt["type"] != "ELITE_MONSTER_KILL":
            continue
        if _event_team(evt, participants) != player_team:
            continue
        monster = evt.get("monsterType")
        sub = evt.get("monsterSubType")
        if monster == "BARON_NASHOR":
            return "post_baron_overfight", evt
        if monster == "DRAGON" and sub == "ELDER_DRAGON":
            return "post_elder_overfight", evt
        if monster == "DRAGON":
            return "post_dragon_overfight", evt
    return None, None


def _jungler_status(events: list[dict], participants: list[dict], player_team: int, ts: int) -> list[str]:
    lines: list[str] = []
    for side_label, team_id in [("ally", player_team), ("enemy", 200 if player_team == 100 else 100)]:
        jungler = next((p for p in participants if p["teamId"] == team_id and p.get("teamPosition") == "JUNGLE"), None)
        if not jungler:
            lines.append(f"{side_label} jungler: unknown")
            continue
        pid = jungler["participantId"]
        last_death = next((e for e in reversed(events) if e["type"] == "CHAMPION_KILL" and e.get("victimId") == pid and e["timestamp"] <= ts), None)
        if not last_death:
            lines.append(f"{side_label} jungler {jungler['championName']}: likely alive")
            continue
        death_age = (ts - last_death["timestamp"]) // 1000
        likely_dead_window = 55 if ts >= 30 * 60_000 else 35
        if death_age <= likely_dead_window:
            lines.append(f"{side_label} jungler {jungler['championName']}: likely dead, died {death_age}s earlier")
        else:
            lines.append(f"{side_label} jungler {jungler['championName']}: likely alive (respawn/activity heuristic)")
    return lines


def _smite_proximity(frames: dict[int, list[dict]], participants: list[dict], player: dict, ts: int) -> list[str]:
    pframe = _participant_frame(frames, player["participantId"], ts)
    if not pframe:
        return ["Smite holder proximity: unavailable (no player frame)"]
    lines: list[str] = []
    for p in participants:
        if 11 not in {p.get("summoner1Id"), p.get("summoner2Id")}:
            continue
        frame = _participant_frame(frames, p["participantId"], ts)
        if not frame:
            continue
        dist = _dist_sq(pframe, frame)
        dist_text = "unknown distance" if dist is None else f"~{int(dist ** 0.5)} units"
        side = "ally" if p["teamId"] == player["teamId"] else "enemy"
        lines.append(f"{side} smite holder {p['championName']}: {_zone(frame.get('x'), frame.get('y'))}, {dist_text} from player")
    return lines or ["Smite holder proximity: unavailable"]


def _objective_conversion(events: list[dict], participants: list[dict], player_team: int, ts: int, window_ms: int) -> list[dict]:
    return [
        e for e in _meaningful_events(events, ts, ts + window_ms)
        if _event_side(e, participants, player_team) == "enemy"
        and (
            e["type"] == "BUILDING_KILL"
            or e.get("monsterType") in {"BARON_NASHOR", "DRAGON"}
        )
    ]


def _major_conversion_name(evt: dict) -> str:
    if evt["type"] == "BUILDING_KILL":
        return _building_name(evt)
    return _objective_name(evt)


def _is_major_enemy_conversion(evt: dict) -> bool:
    if evt["type"] == "ELITE_MONSTER_KILL":
        return evt.get("monsterType") in {"BARON_NASHOR", "DRAGON"}
    if evt["type"] == "BUILDING_KILL":
        return evt.get("buildingType") == "INHIBITOR_BUILDING" or evt.get("towerType") in {"NEXUS_TURRET", "BASE_TURRET"}
    return False


def _trade_context(events: list[dict], participants: list[dict], player_team: int, ts: int, before_ms: int = 30_000, after_ms: int = 90_000) -> dict:
    meaningful = _meaningful_events(events, ts - before_ms, ts + after_ms)
    ally_gains = [e for e in meaningful if _event_side(e, participants, player_team) == "ally"]
    enemy_gains = [e for e in meaningful if _event_side(e, participants, player_team) == "enemy"]
    return {
        "ally_gains": ally_gains,
        "enemy_gains": enemy_gains,
        "pressure_trade": bool(ally_gains and enemy_gains),
    }


def _classify_death(
    zone: str,
    ts: int,
    unspent: int,
    teamfight_context: bool,
    post_objective_label: str | None,
    prev30_meaningful: list[dict],
    next90_meaningful: list[dict],
    enemy_major_60: list[dict],
    ally_nearby: int,
    enemy_nearby: int,
    gold_actionability: str = "unknown",
    pressure_trade: bool = False,
) -> str:
    enemy_gained = [e for e in next90_meaningful if e.get("_side") == "enemy"]
    ally_gained = [e for e in next90_meaningful if e.get("_side") == "ally"]
    if teamfight_context and post_objective_label:
        return "late_game_teamfight_death"
    if teamfight_context:
        if any(e["type"] == "ELITE_MONSTER_KILL" for e in prev30_meaningful + next90_meaningful):
            return "objective_fight_death"
        if ally_gained and not enemy_major_60:
            return "acceptable_trade_death"
        return "late_game_teamfight_death"
    if post_objective_label:
        return "post_objective_overfight"
    if pressure_trade:
        return "pressure_trade_death"
    if unspent >= 1500 and gold_actionability != "low" and (enemy_major_60 or enemy_gained):
        return "bad_unspent_gold_death"
    if zone in _TOP_ZONES | {"bot_lane", "blue_bot_jungle", "red_bot_jungle"} and enemy_nearby > ally_nearby:
        return "side_lane_collapse"
    if ally_gained and not enemy_gained:
        return "acceptable_trade_death"
    if ally_nearby == 0 and enemy_nearby <= 2:
        return "isolated_pick"
    return "unclear_low_confidence"


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
        return "low_impact_absence", "Candidate review: was a valuable top-side trade available, or could you crash/reset early enough to join?", "medium"
    if zone in _ROTATABLE_ZONES:
        return "missed_rotation", "Candidate review: was dragon realistically contestable from your lane state, or was a cross-map trade better?", "medium"
    if zone in ("blue_base", "red_base"):
        return "unclear_low_confidence", "You were recalling or in base - check if the back timing was forced or avoidable next replay.", "low"
    return "unclear_low_confidence", "Unable to determine reason for absence - review the replay for this dragon.", "low"


def _match_header(info: dict, player: dict, participants: list[dict], match_id: str = "", opponent: dict | None = None) -> str:
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
        f"Match ID : {match_id or info.get('gameId', '?')}",
        f"Champion : {player['championName']} ({role})  -  {result}",
        f"Date     : {date_str}  |  {queue}  |  {_dur(duration)}",
        f"KDA      : {player['kills']}/{player['deaths']}/{player['assists']}",
        f"CS       : {cs} ({cs_pm:.1f}/min)  |  Gold: {player['goldEarned']:,}",
        f"Damage   : {player.get('totalDamageDealtToChampions', 0):,}  |  Vision: {player.get('visionScore', 0)}",
        f"Lane opp.: {opponent['championName'] if opponent else 'unknown'}",
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
        pf = _nearest(pf_list, ts)
        zone = _zone(pos.get("x"), pos.get("y"))
        if zone == "unknown" and pf:
            zone = _zone(pf.get("x"), pf.get("y"))
        of = _nearest(of_list, ts) if of_list else None
        killer = _pname(participants, evt.get("killerId"))
        assists = [_pname(participants, a) for a in evt.get("assistingParticipantIds", [])]
        threat = f"killed by {killer}" + (f" (+{', '.join(assists)})" if assists else "")
        unspent = pf["current_gold"] if pf else 0
        level = pf["level"] if pf else "?"
        total_gold = pf["total_gold"] if pf else 0
        gold_lead = (pf["total_gold"] - of["total_gold"]) if (pf and of) else None
        lead_str = f"{gold_lead:+,}" if gold_lead is not None else "unknown"
        item_state = _inventory_state(events, ppid, ts, unspent, level)
        gold_actionability = _gold_actionability_label(unspent, item_state)

        prev30_kills = [e for e in events if e["type"] == "CHAMPION_KILL" and ts - 30_000 <= e["timestamp"] < ts]
        prev10_kills = [e for e in events if e["type"] == "CHAMPION_KILL" and ts - 10_000 <= e["timestamp"] < ts]
        around10_kills = [e for e in events if e["type"] == "CHAMPION_KILL" and ts - 10_000 <= e["timestamp"] <= ts + 10_000]
        next10_kills = [e for e in events if e["type"] == "CHAMPION_KILL" and ts < e["timestamp"] <= ts + 10_000]
        next60_kills = [e for e in events if e["type"] == "CHAMPION_KILL" and ts < e["timestamp"] <= ts + 60_000]
        same_ts_player_kills = [e for e in events if e["type"] == "CHAMPION_KILL" and e.get("killerId") == ppid and e["timestamp"] == ts]
        pre_player_kills = [e for e in prev30_kills if e.get("killerId") == ppid]
        player_assists_prev30 = [e for e in prev30_kills if ppid in e.get("assistingParticipantIds", [])]
        teamfight_context = len(around10_kills) >= 3

        prev30_meaningful = _meaningful_events(events, ts - 30_000, ts)
        prev10_meaningful = _meaningful_events(events, ts - 10_000, ts)
        next60_meaningful = _meaningful_events(events, ts, ts + 60_000)
        next90_meaningful = _meaningful_events(events, ts, ts + 90_000)
        next120_meaningful = _meaningful_events(events, ts, ts + 120_000)
        next90_for_class = [dict(e, _side=_event_side(e, participants, pt)) for e in next90_meaningful]
        enemy_major_60 = [
            e for e in next60_meaningful
            if _event_side(e, participants, pt) == "enemy"
            and e["type"] == "ELITE_MONSTER_KILL"
            and (e.get("monsterType") == "BARON_NASHOR" or e.get("monsterSubType") == "ELDER_DRAGON" or e.get("monsterType") == "DRAGON")
        ]
        objective_conversion_against = bool(enemy_major_60)
        post_objective_label, post_objective_evt = _recent_major_objective(events, participants, pt, ts)
        trade_context = _trade_context(events, participants, pt, ts)
        context_labels: list[str] = []
        if post_objective_label:
            context_labels.extend(["post_major_objective_fight", post_objective_label])
            if post_objective_label == "post_soul_overfight" and zone == "mid_lane":
                context_labels.append("post_soul_mid_fight")
        ally_nearby, enemy_nearby, ally_names, enemy_names = _nearby_counts(frames, participants, player, ts)
        death_class = _classify_death(
            zone,
            ts,
            unspent,
            teamfight_context,
            post_objective_label,
            prev30_meaningful,
            next90_for_class,
            enemy_major_60,
            ally_nearby,
            enemy_nearby,
            item_state.get("actionability", "unknown"),
            trade_context["pressure_trade"],
        )

        prev30_allied_deaths, prev30_enemy_deaths = _kill_counts(prev30_kills, participants, pt)
        prev10_allied_deaths, prev10_enemy_deaths = _kill_counts(prev10_kills, participants, pt)
        next60_allied_deaths, next60_enemy_deaths = _kill_counts(next60_kills, participants, pt)

        lines.append(f"Death #{i} @ {_ts(ts)}")
        lines.append("Facts:")
        lines.append(f"- Zone: {zone}")
        if same_ts_player_kills:
            victims = ", ".join(_pname(participants, k.get("victimId")) for k in same_ts_player_kills)
            lines.append(f"- Player killed {victims} and died to {killer} at the same timestamp")
        else:
            lines.append(f"- Player {threat}")
        lines.append(f"- Level {level}, total gold {total_gold:,}, unspent gold {unspent:,} ({gold_actionability}), gold lead vs lane opponent {lead_str}")
        if total_gold >= 6000:
            lines.append("- Shutdown risk: high-value target by total gold")
        lines.append("- Inventory context:")
        for inventory_line in _inventory_lines(item_state):
            lines.append(f"  {inventory_line}")
        if post_objective_label and post_objective_evt:
            elapsed = (ts - post_objective_evt["timestamp"]) // 1000
            lines.append(f"- Team had just secured {post_objective_evt.get('name', _objective_name(post_objective_evt))} {elapsed}s earlier")
        if teamfight_context:
            cluster = ", ".join(_kill_desc(e, participants, pt, ppid) for e in around10_kills[:8])
            lines.append(f"- Fight cluster: {len(around10_kills)} champion kill/death event(s) within +/-10s: {cluster}")
        if objective_conversion_against:
            for e in enemy_major_60[:2]:
                elapsed = (e["timestamp"] - ts) // 1000
                lines.append(f"- objective_conversion_against_player_team=true: enemy secured {_objective_name(e)} {elapsed}s later")
        if trade_context["pressure_trade"]:
            ally_trade = "; ".join(_format_meaningful(e, participants, pt, ts) for e in trade_context["ally_gains"][:3])
            enemy_trade = "; ".join(_format_meaningful(e, participants, pt, ts) for e in trade_context["enemy_gains"][:3])
            lines.append(f"- Pressure trade context: ally gains [{ally_trade}] vs enemy gains [{enemy_trade}]")

        lines.append("Pre-death fight cluster - previous 30s:")
        lines.append(f"- Allied deaths: {prev30_allied_deaths}; enemy deaths: {prev30_enemy_deaths}")
        lines.append(f"- Player kills: {len(pre_player_kills)}; player assists: {len(player_assists_prev30)}")
        if prev30_kills:
            for e in prev30_kills[-6:]:
                lines.append(f"- {_kill_desc(e, participants, pt, ppid)}")
        else:
            lines.append("- No champion deaths in previous 30s")
        if prev30_meaningful:
            for e in prev30_meaningful[:4]:
                lines.append(f"- Previous objective/building: {_format_meaningful(e, participants, pt, ts)}")
        else:
            lines.append("- No objectives, towers, or inhibitors taken in previous 30s")
        for state_line in _objective_state_lines(events, participants, pt, ts):
            lines.append(f"- Objective state: {state_line}")
        if context_labels:
            lines.append(f"- Context labels: {', '.join(context_labels)}")

        lines.append("Pre-death fight cluster - previous 10s:")
        lines.append(f"- Immediate allied deaths before player death: {prev10_allied_deaths}; immediate enemy deaths: {prev10_enemy_deaths}")
        if prev10_kills:
            for e in prev10_kills[-6:]:
                lines.append(f"- {_kill_desc(e, participants, pt, ppid)}")
        else:
            lines.append("- No champion deaths in previous 10s")
        lines.append(f"- teamfight_context={str(teamfight_context).lower()} ({len(around10_kills)} kill/death events within +/-10s)")
        lines.append(f"- Nearby at death: allies {ally_nearby}, enemies {enemy_nearby}")
        if ally_names:
            lines.append(f"- Nearby allies: {', '.join(ally_names[:5])}")
        if enemy_names:
            lines.append(f"- Nearby enemies: {', '.join(enemy_names[:5])}")
        enemy_involved = [
            pid for pid in [evt.get("killerId"), *evt.get("assistingParticipantIds", [])]
            if _side(participants, pid, pt) == "enemy"
        ]
        if enemy_nearby == 0 and enemy_involved:
            involved_names = ", ".join(_pname(participants, pid) for pid in enemy_involved)
            lines.append(f"- Position-frame proximity caveat: nearby enemies show 0, but kill/assist data confirms enemy involvement ({involved_names}). Position-frame proximity is approximate and conflicts with kill/assist data.")
        if teamfight_context and (pre_player_kills or same_ts_player_kills):
            lines.append("- Note: player kill near death is treated as fight-cluster evidence, not as chase/side-lane overstay evidence")

        lines.append("At death:")
        lines.append(f"- Killer and assists: {killer}" + (f" (+{', '.join(assists)})" if assists else ""))
        lines.append(f"- Factual type candidate: {death_class}")
        if context_labels:
            lines.append(f"- fight_context: {', '.join(context_labels)}")
        lines.append(f"- objective_conversion_against_player_team={str(objective_conversion_against).lower()}")
        lines.append(f"- allied/enemy jungler state: {'; '.join(_jungler_status(events, participants, pt, ts))}")
        for smite_line in _smite_proximity(frames, participants, player, ts):
            lines.append(f"- {smite_line}")

        lines.append("Position frames around death (timeline-frame approximation):")
        for sample in _position_samples(frames, participants, player, ts):
            lines.append(f"- {sample}")

        lines.append("Next 60s / 90s / 120s:")
        lines.append(f"- Next 10s deaths: {len(next10_kills)}; next 60s allied deaths: {next60_allied_deaths}; next 60s enemy deaths: {next60_enemy_deaths}")
        if next60_meaningful:
            for e in next60_meaningful[:5]:
                lines.append(f"- Next 60s: {_format_meaningful(e, participants, pt, ts)}")
        else:
            lines.append("- Next 60s: no objectives, towers, or inhibitors taken")
        next90_extra = [e for e in next90_meaningful if e not in next60_meaningful]
        if next90_extra:
            for e in next90_extra[:4]:
                lines.append(f"- Next 90s: {_format_meaningful(e, participants, pt, ts)}")
        next120_extra = [e for e in next120_meaningful if e not in next90_meaningful]
        if next120_extra:
            for e in next120_extra[:4]:
                lines.append(f"- Next 120s: {_format_meaningful(e, participants, pt, ts)}")

        player_team_gains = [e for e in _meaningful_events(events, ts - 30_000, ts + 90_000) if _event_side(e, participants, pt) == "ally"]
        enemy_team_gains = [e for e in _meaningful_events(events, ts - 30_000, ts + 90_000) if _event_side(e, participants, pt) == "enemy"]
        lines.append("What was gained for the death:")
        lines.append("- Player team gained: " + ("; ".join(_format_meaningful(e, participants, pt, ts) for e in player_team_gains[:4]) if player_team_gains else "nothing major in -30s to +90s"))
        lines.append("- Enemy team gained: " + ("; ".join(_format_meaningful(e, participants, pt, ts) for e in enemy_team_gains[:4]) if enemy_team_gains else "nothing major in -30s to +90s"))
        if enemy_major_60:
            lines.append("- Net outcome: bad objective conversion against player team")
        elif player_team_gains and not enemy_team_gains:
            lines.append("- Net outcome: likely acceptable or positive trade")
        elif enemy_team_gains and not player_team_gains:
            lines.append("- Net outcome: enemy converted the death into map pressure")
        else:
            lines.append("- Net outcome: mixed or unclear")

        lines.append("Candidate interpretations:")
        if death_class == "late_game_teamfight_death" and post_objective_label:
            lines.append("- late_game_teamfight_death")
            for label in context_labels:
                lines.append(f"- {label}")
            lines.append("- bad_objective_conversion candidate: a major objective was live or soon contestable")
            lines.append("- not enough evidence to call this a chase or isolated overstay")
        elif death_class == "pressure_trade_death":
            lines.append("- pressure_trade_death")
            lines.append("- subtype: exit_failed")
            lines.append("- net_outcome: mixed")
            lines.append("- candidate interpretation: valid pressure trade, exit failed")
        else:
            lines.append(f"- {death_class}")
        if unspent >= 1500:
            if item_state.get("item_data_missing"):
                lines.append("- Unspent gold actionability is provisional because item slots and purchasable upgrades were not evaluated.")
                lines.append(f"- {unspent:,}g unspent, item state unknown. If not six-slotted, this is severe missed spending. If six-slotted, gold may have low immediate actionability; review elixir/swap options and focus on fight selection around Baron/Elder.")
            elif item_state.get("six_slotted"):
                lines.append("- unspent_gold_low_actionability_six_slotted")
                lines.append("- Gold may not represent missed immediate combat power; review elixir, item upgrade, defensive swap availability, and fight/objective decision.")
            else:
                lines.append(f"- Unspent gold appears {_gold_severity(unspent)} and likely actionable because item slots were open.")
        lines.append("- Possible CC catch based on kill cluster, but ability hit data is not available in Riot timeline data.")

        lines.append("Coach questions:")
        if post_objective_label == "post_soul_overfight":
            lines.append("- Should the team reset/set Baron after Soul instead of fighting mid?")
            lines.append("- Was the player first into fog/choke, or already committed with team?")
            lines.append("- Did elixir/swap state matter, or was the main issue fight selection around Baron?")
        elif objective_conversion_against:
            lines.append("- Did this death allow enemy Baron/Elder/Dragon/inhib/Nexus turret?")
            if item_state.get("six_slotted"):
                lines.append("- Since the player was six-slotted, was the issue elixir/swap prep or the fight itself?")
            else:
                lines.append("- Was unspent gold actionable before this objective window?")
        elif death_class == "pressure_trade_death":
            lines.append("- Was this death required to secure the structure/objective trade?")
            lines.append("- Was there a safer exit route after forcing pressure?")
        elif death_class == "acceptable_trade_death":
            lines.append("- Did the team gain enough objective/structure value to justify the death?")
            lines.append("- Could the player exit after securing the value?")
        elif teamfight_context:
            lines.append("- Was the team already committed before the player died?")
            lines.append("- Was the player first visible in an unsafe choke/fog area?")
        else:
            lines.append("- Was this an isolated pick or a pressure play with missing map context?")
            lines.append("- Did nearby objective timers or structure states make the death higher value?")

        lines.append("Confidence:")
        lines.append("- Facts: high confidence for timeline events, gold, deaths, and objective conversions.")
        lines.append("- Interpretation: medium confidence; timeline frames are approximate and ability hits/CC are not exposed.")
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
        recommended_mode = "trade_cross_map" if assessment == "correct_trade" else "objective_setup" if assessment != "unclear_low_confidence" else "avoid_fight"
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
        lines.append(f"  Candidate mode: {recommended_mode}")
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
        recommended_mode = "objective_setup" if upcoming else "trade_cross_map" if "Ally took" in tag else "avoid_fight"
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
        lines.append(f"  Candidate mode: {recommended_mode}")
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
        item_state = _inventory_state(events, ppid, ts_ms, f_now["current_gold"], f_now.get("level", "?"))
        recommended_mode = "reset_and_spend"
        if item_state.get("six_slotted"):
            recommended_mode = "objective_setup"
        if gained_30s:
            lines.append(f"[{f_now['minute']:02d}:00] High unspent gold: {f_now['current_gold']}g")
            lines.append("  Facts:")
            for inventory_line in _inventory_lines(item_state, prefix="    "):
                lines.append(inventory_line)
            for e in gained_30s[:2]:
                elapsed = (e["timestamp"] - ts_ms) // 1000
                team = _side(participants, e.get("killerId"), pt)
                if e["type"] == "BUILDING_KILL":
                    name = f"{e.get('towerType', 'tower').replace('_', ' ').title()} ({e.get('laneType', '?').replace('_', ' ')})"
                    lines.append(f"    -> {_ts(e['timestamp'])}  {team} destroyed {name} (+{elapsed}s)")
                else:
                    name = (e.get("monsterSubType") or e.get("monsterType") or "objective").replace("_", " ").title()
                    lines.append(f"    -> {_ts(e['timestamp'])}  {team} secured {name} (+{elapsed}s)")
            lines.append(f"  Candidate mode: {recommended_mode if not gained_30s else 'trade_cross_map'}")
            if item_state.get("six_slotted"):
                lines.append("  Interpretation: acceptable_greed, unspent_gold_low_actionability_six_slotted")
                lines.append("  Recommendation : Good conversion window. On the next reset, review elixir/swap options rather than treating the gold as automatically missed combat power.")
            elif item_state.get("item_data_missing"):
                lines.append("  Interpretation: acceptable_greed, unspent gold severity provisional because item state is unknown")
                lines.append("  Recommendation : Good conversion window. If not six-slotted, spend on the next reset; if six-slotted, review elixir/swap options.")
            else:
                lines.append("  Interpretation: acceptable_greed")
                lines.append("  Recommendation : Good conversion window. Spend on the next reset before taking another fight.")
            lines.append("  Confidence     : medium")
            lines.append("")
            continue
        contested_90s = [e for e in events if e["type"] == "ELITE_MONSTER_KILL" and e.get("monsterType") == "DRAGON" and abs(e["timestamp"] - ts_ms) <= 90_000]
        upcoming_90s = _objective_spawns_in_window(events, ts_ms, 90_000)
        lines.append(f"[{f_now['minute']:02d}:00] High unspent gold: {f_now['current_gold']}g")
        lines.append("  Facts:")
        for inventory_line in _inventory_lines(item_state, prefix="    "):
            lines.append(inventory_line)
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
            lines.append(f"  Candidate mode: {'objective_setup' if item_state.get('six_slotted') else 'reset_and_spend'}")
            if ally_secured:
                if item_state.get("six_slotted"):
                    lines.append("  Interpretation: team secured nearby objective; unspent_gold_low_actionability_six_slotted")
                    lines.append("  Recommendation : Since inventory was full, review elixir/swap options after the play and focus on the next objective setup.")
                else:
                    lines.append("  Interpretation: team secured nearby objective despite high unspent gold")
                    lines.append("  Recommendation : Since the team secured the objective, focus on spending cleanly after the play.")
                lines.append("  Confidence     : medium")
            else:
                if item_state.get("six_slotted"):
                    lines.append("  Interpretation: unspent_gold_low_actionability_six_slotted; contest issue is more likely fight setup, elixir/swap prep, or objective positioning")
                    lines.append("  Recommendation : Review elixir/defensive swap availability and objective setup rather than assuming raw gold was directly spendable.")
                elif item_state.get("item_data_missing"):
                    lines.append("  Interpretation: high gold severity provisional because item slots and upgrades were not evaluated")
                    lines.append("  Recommendation : If not six-slotted, recall between waves before the spawn; if six-slotted, review elixir/swap options and positioning.")
                else:
                    lines.append("  Interpretation: high gold likely reduced contest power")
                    lines.append("  Recommendation : Recall between waves well before the dragon spawn window, not during it.")
                lines.append("  Confidence     : high")
        elif upcoming_90s:
            for spawn_ts, name in upcoming_90s:
                elapsed = (spawn_ts - ts_ms) // 1000
                lines.append(f"    -> {name} in {elapsed}s at {_ts(spawn_ts)}")
            lines.append(f"  Candidate mode: {'objective_setup' if item_state.get('six_slotted') else 'reset_and_spend'}")
            if item_state.get("six_slotted"):
                lines.append("  Interpretation: unspent_gold_low_actionability_six_slotted before an objective spawn window")
                lines.append("  Recommendation : Review elixir, item upgrade, defensive swap availability, and fight/objective decision.")
            elif item_state.get("item_data_missing"):
                lines.append("  Interpretation: high gold before an objective spawn window, but severity is provisional because item state is unknown")
                lines.append("  Recommendation : If not six-slotted, recall immediately if safe; if six-slotted, review elixir/swap options and objective setup.")
            else:
                lines.append("  Interpretation: high gold before an objective spawn window")
                lines.append("  Recommendation : Recall immediately if safe, or avoid fighting until the gold is spent.")
            lines.append("  Confidence     : medium")
        else:
            lines.append("    No objective clash within 90s - recall timing appears acceptable here")
            lines.append(f"  Candidate mode: {recommended_mode}")
            if item_state.get("six_slotted"):
                lines.append("  Interpretation: low objective pressure; unspent_gold_low_actionability_six_slotted")
                lines.append("  Recommendation : Fine to delay if no elixir/swap is needed, but check shop options before the next objective fight.")
            elif item_state.get("item_data_missing"):
                lines.append("  Interpretation: low objective pressure; unspent severity provisional because item state is unknown")
                lines.append("  Recommendation : If not six-slotted, spending sooner accelerates your power spike; if six-slotted, review elixir/swap options.")
            else:
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
            objective_team = evt.get("killerTeamId") or kid_team
            if evt.get("monsterType") == "DRAGON" and objective_team in dragon_counts:
                dragon_counts[objective_team] += 1
            side_label = (
                "ALLY" if objective_team == pt
                else "ENEMY" if objective_team in dragon_counts
                else _side(participants, kid, pt).upper()
            )
            lines.append(f"[{t}] OBJECTIVE   {side_label} secured {monster}")
        elif etype == "DRAGON_SOUL_GIVEN":
            team_id = evt.get("teamId")
            if team_id in dragon_counts and dragon_counts[team_id] >= 4:
                team_label = "YOUR TEAM" if team_id == pt else "ENEMY TEAM"
                lines.append(f"[{t}] DRAGON SOUL {team_label} achieved dragon soul")
        elif etype == "BUILDING_KILL":
            ttype = evt.get("towerType", "").replace("_", " ")
            lane = evt.get("laneType", "").replace("_", " ")
            actor = _event_side(evt, participants, pt).upper()
            target = _building_victim_side(evt, participants, pt)
            lines.append(f"[{t}] BUILDING    {actor} destroyed {target} {ttype} ({lane})")
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


def _frame_for_participant(frames: dict[int, list[dict]], participant: dict, ts: int) -> dict | None:
    return _nearest(frames.get(participant["participantId"], []), ts)


def _phase_label(ts: int, tag: str = "") -> str:
    return f"{_ts(ts)} {tag}".strip()


def _participant_phase_score(participant: dict, frame: dict | None, events: list[dict], ts: int) -> int:
    kills = sum(1 for e in events if e["type"] == "CHAMPION_KILL" and e.get("killerId") == participant["participantId"] and e["timestamp"] <= ts)
    deaths = sum(1 for e in events if e["type"] == "CHAMPION_KILL" and e.get("victimId") == participant["participantId"] and e["timestamp"] <= ts)
    assists = sum(1 for e in events if e["type"] == "CHAMPION_KILL" and participant["participantId"] in e.get("assistingParticipantIds", []) and e["timestamp"] <= ts)
    gold = frame["total_gold"] if frame else 0
    level = frame["level"] if frame else 1
    cs = frame["cs"] if frame else 0
    return gold + level * 350 + cs * 12 + kills * 500 + assists * 150 - deaths * 250


def _top_phase_candidates(participants: list[dict], frames: dict[int, list[dict]], events: list[dict], team_id: int, ts: int) -> list[tuple[dict, dict | None, int]]:
    candidates = []
    for p in participants:
        if p["teamId"] != team_id:
            continue
        frame = _frame_for_participant(frames, p, ts)
        candidates.append((p, frame, _participant_phase_score(p, frame, events, ts)))
    return sorted(candidates, key=lambda x: x[2], reverse=True)


def _lane_opponent_at_phase(participants: list[dict], player: dict) -> dict | None:
    return _find_opponent(participants, player)


def _tower_events(events: list[dict], participants: list[dict], player_team: int, ts: int, side: str | None = None) -> list[dict]:
    towers = [e for e in events if e["type"] == "BUILDING_KILL" and e["timestamp"] <= ts]
    if side:
        towers = [e for e in towers if _event_side(e, participants, player_team) == side]
    return towers


def _phase_mode(ts: int, player: dict, pframe: dict | None, opponent_frame: dict | None, events: list[dict], participants: list[dict], frames: dict[int, list[dict]]) -> tuple[str, str]:
    pt = player["teamId"]
    baron_alive = ts >= 20 * 60_000 and "alive/contestable" in _objective_state(events, participants, ts, "BARON_NASHOR", 20 * 60_000, 6 * 60_000)
    upcoming = _objective_spawns_in_window(events, ts, 90_000)
    ally_baron = any(
        e["type"] == "ELITE_MONSTER_KILL"
        and e.get("monsterType") == "BARON_NASHOR"
        and _event_side(e, participants, pt) == "ally"
        and 0 <= ts - e["timestamp"] <= 3 * 60_000
        for e in events
    )
    soul_times = _dragon_soul_times(events, participants)
    just_soul = pt in soul_times and 0 <= ts - soul_times[pt] <= 90_000
    exposed = _exposed_structure_state(events, participants, pt, ts)
    if exposed["enemy_nexus_turrets_destroyed"] or exposed["enemy_inhibitors_destroyed"]:
        return "end_game_push", "enemy base is exposed; wave escort and siege matter more than random picks"
    if ally_baron:
        return "siege_with_team", "Baron buff should convert through grouped or two-lane siege"
    if just_soul:
        return "objective_setup", "after Soul, reset map state and set Baron vision instead of taking messy mid fights"
    if upcoming:
        return "objective_setup", f"{upcoming[0][1]} is inside 90s; Teemo should shroom entrances before the fight starts"
    if baron_alive and ts >= 25 * 60_000:
        return "group_for_teamfight", "Baron is live; side pressure is useful only if it creates safe Baron control"
    if player.get("championName", "").lower() == "teemo" and pframe and opponent_frame and pframe["total_gold"] - opponent_frame["total_gold"] >= 1200 and ts < 25 * 60_000:
        return "side_pressure", "Teemo is ahead enough to pull a side-lane answer and convert towers/jungle/objectives"
    if ts < 14 * 60_000:
        return "lane_pressure", "early Teemo job is lane control, plates, and punishing melee windows"
    return "side_pressure", "default mid-game Teemo mode is pressure with exits prepared"


def _win_condition_phase(events: list[dict], participants: list[dict], player: dict, opponent: dict | None, frames: dict[int, list[dict]], ts: int, tag: str = "") -> str:
    pt = player["teamId"]
    pframe = _frame_for_participant(frames, player, ts)
    oframe = _frame_for_participant(frames, opponent, ts) if opponent else None
    candidates = _top_phase_candidates(participants, frames, events, pt, ts)
    primary_p, primary_f, _ = candidates[0] if candidates else (player, pframe, 0)
    secondary_p, secondary_f, _ = candidates[1] if len(candidates) > 1 else (player, pframe, 0)
    mode, mode_reason = _phase_mode(ts, player, pframe, oframe, events, participants, frames)
    gold_lead = (pframe["total_gold"] - oframe["total_gold"]) if pframe and oframe else None
    cs_lead = (pframe["cs"] - oframe["cs"]) if pframe and oframe else None
    player_primary = primary_p["participantId"] == player["participantId"]
    primary = (
        f"{player['championName']} side pressure"
        if player_primary and mode in {"lane_pressure", "side_pressure"}
        else f"{primary_p['championName']} as strongest resource point"
    )
    if mode in {"siege_with_team", "end_game_push"}:
        primary = "team siege and wave escort"
    elif mode == "objective_setup":
        primary = f"{player['championName']} objective setup plus jungler conversion"
    secondary = f"{secondary_p['championName']} follow-up damage/objective conversion"
    play_around = "self through side pressure" if player_primary and mode in {"lane_pressure", "side_pressure"} else "team siege carries" if mode in {"siege_with_team", "end_game_push"} else "jungler/objective setup" if mode == "objective_setup" else f"{primary_p['championName']}"
    reason_bits = []
    if gold_lead is not None:
        reason_bits.append(f"Teemo lane delta: {cs_lead:+} CS, {gold_lead:+,}g vs {opponent['championName'] if opponent else 'lane opponent'}")
    if primary_f:
        reason_bits.append(f"{primary_p['championName']} gold/level: {primary_f['total_gold']:,}g/L{primary_f['level']}")
    recent = _window(events, ts - 90_000, ts)
    recent_kills = len([e for e in recent if e["type"] == "CHAMPION_KILL"])
    recent_structures = len([e for e in recent if e["type"] == "BUILDING_KILL"])
    if recent_kills or recent_structures:
        reason_bits.append(f"recent 90s: {recent_kills} kill(s), {recent_structures} structure(s)")
    return "\n".join([
        f"[{_phase_label(ts, tag)}]",
        f"Candidate win condition: {primary}",
        f"Secondary win condition: {secondary}",
        f"Candidate player jobs: {mode}",
        f"Play around: {play_around}",
        f"Reason: {'; '.join(reason_bits) if reason_bits else mode_reason}",
        f"Player role: {_player_job_for_mode(player, mode)}",
        f"Question to review: {mode_reason}",
        "",
    ])


def _player_job_for_mode(player: dict, mode: str) -> str:
    champ = player.get("championName", "")
    if champ.lower() == "teemo":
        jobs = {
            "lane_pressure": "farm cleanly, punish melee, take plates/tower without donating shutdown",
            "side_pressure": "force side response, shroom exits first, take tier 2/jungle, leave before collapse",
            "objective_setup": "arrive early, shroom entrances/chokes, avoid first face-check, protect jungler access",
            "group_for_teamfight": "fight through shroomed corridors, stay spaced, do not be first visible in river/mid choke",
            "siege_with_team": "hover carries, shroom flanks, escort waves, chip towers rather than chasing",
            "end_game_push": "escort supers, cover flanks, hit exposed structures when defenders answer waves",
        }
        return jobs.get(mode, "create pressure only when it converts into objective or structure value")
    return "play the mode that converts your champion's current gold into the next objective or structure"


def _exposed_structure_state(events: list[dict], participants: list[dict], player_team: int, ts: int) -> dict:
    enemy_towers = [
        e for e in events
        if e["type"] == "BUILDING_KILL"
        and e["timestamp"] <= ts
        and _event_side(e, participants, player_team) == "ally"
    ]
    return {
        "enemy_inhibitors_destroyed": sum(1 for e in enemy_towers if e.get("buildingType") == "INHIBITOR_BUILDING"),
        "enemy_nexus_turrets_destroyed": sum(1 for e in enemy_towers if e.get("towerType") == "NEXUS_TURRET"),
        "enemy_base_turrets_destroyed": sum(1 for e in enemy_towers if e.get("towerType") in {"BASE_TURRET", "NEXUS_TURRET"}),
    }


def _strategic_timestamps(events: list[dict], participants: list[dict], player_team: int) -> list[tuple[int, str]]:
    max_ts = max((e["timestamp"] for e in events), default=0)
    points = [(m * 60_000, "") for m in [10, 14, 20, 25, 30] if m * 60_000 <= max_ts]
    for e in events:
        if e["type"] == "ELITE_MONSTER_KILL" and _event_side(e, participants, player_team) == "ally":
            if e.get("monsterType") == "BARON_NASHOR":
                points.append((e["timestamp"], "Ally Baron"))
            elif e.get("monsterSubType") == "ELDER_DRAGON":
                points.append((e["timestamp"], "Ally Elder"))
    for team_id, soul_ts in _dragon_soul_times(events, participants).items():
        if team_id == player_team:
            points.append((soul_ts, "Ally Soul"))
    for e in events:
        if e["type"] == "BUILDING_KILL" and _event_side(e, participants, player_team) == "ally" and (
            e.get("towerType") in {"BASE_TURRET", "NEXUS_TURRET"} or e.get("buildingType") == "INHIBITOR_BUILDING"
        ):
            points.append((e["timestamp"], "Base pressure"))
    dedup: list[tuple[int, str]] = []
    seen: set[int] = set()
    for ts, tag in sorted(points):
        bucket = ts // 30_000
        if bucket in seen:
            continue
        seen.add(bucket)
        dedup.append((ts, tag))
    return dedup


def _close_windows(events: list[dict], participants: list[dict], player: dict) -> str:
    pt = player["teamId"]
    starts = [
        e for e in events
        if e["type"] == "ELITE_MONSTER_KILL"
        and _event_side(e, participants, pt) == "ally"
        and e.get("monsterType") in {"BARON_NASHOR", "DRAGON"}
        and (e.get("monsterType") == "BARON_NASHOR" or e.get("monsterSubType") == "ELDER_DRAGON")
    ]
    lines: list[str] = []
    for start in starts[:3]:
        ts = start["timestamp"]
        after = _meaningful_events(events, ts, ts + 5 * 60_000)
        structures = [e for e in after if e["type"] == "BUILDING_KILL" and _event_side(e, participants, pt) == "ally"]
        if not structures:
            continue
        peak = max(structures, key=lambda e: (2 if e.get("towerType") == "NEXUS_TURRET" else 1 if e.get("towerType") == "BASE_TURRET" or e.get("buildingType") == "INHIBITOR_BUILDING" else 0, e["timestamp"]))
        deaths_after = [
            e for e in events
            if e["type"] == "CHAMPION_KILL"
            and e.get("victimId") == player["participantId"]
            and ts < e["timestamp"] <= ts + 5 * 60_000
        ]
        exposed = _exposed_structure_state(events, participants, pt, peak["timestamp"])
        pattern = "end_now" if exposed["enemy_nexus_turrets_destroyed"] else "escort_supers" if exposed["enemy_inhibitors_destroyed"] else "two_lane_baron_siege"
        lines.extend([
            f"close_window_started: {_ts(ts)} ally {_objective_name(start)}",
            f"close_window_peak: {_ts(peak['timestamp'])} {_building_name(peak)} destroyed",
            f"structures_taken: {', '.join(_building_name(e) for e in structures[:6])}",
            f"base_state_at_peak: inhibs_destroyed={exposed['enemy_inhibitors_destroyed']}, nexus_turrets_destroyed={exposed['enemy_nexus_turrets_destroyed']}",
            f"player_deaths_during_window: {', '.join(_ts(e['timestamp']) for e in deaths_after) if deaths_after else 'none'}",
            f"recommended_close_pattern: {pattern}",
            "close_window_question: why did the game not end here?",
            "Coaching: once structures are exposed, shift from side-pressure mode to siege/escort mode. The priority is reset, push two lanes, shroom flanks, escort supers, and force defenders to answer waves.",
            "",
        ])
    return "\n".join(lines) if lines else "No clear close window detected from Baron/Elder into structure pressure."


def _team_reaction(events: list[dict], participants: list[dict], player: dict, frames: dict[int, list[dict]]) -> str:
    pt = player["teamId"]
    lines: list[str] = []
    for e in events:
        if e["type"] != "CHAMPION_KILL" or _side(participants, e.get("victimId"), pt) != "enemy":
            continue
        ts = e["timestamp"]
        baron_live = ts >= 20 * 60_000 and "alive/contestable" in _objective_state(events, participants, ts, "BARON_NASHOR", 20 * 60_000, 6 * 60_000)
        upcoming = _objective_spawns_in_window(events, ts, 75_000)
        if not baron_live and not upcoming:
            continue
        objective_zone = "baron_pit" if baron_live else "dragon_pit"
        allies_near = 0
        for p in participants:
            if p["teamId"] != pt:
                continue
            frame = _frame_for_participant(frames, p, ts + 20_000)
            if frame and _zone(frame.get("x"), frame.get("y")) in {objective_zone, "baron_pit", "dragon_pit", "top_river", "bot_river"}:
                allies_near += 1
        reaction = "team_followed" if allies_near >= 3 else "team_partially_followed" if allies_near >= 2 else "team_ignored"
        lines.append(f"[{_ts(ts)}] Enemy death near objective window -> {reaction}")
        lines.append("  Solo queue adaptation: if 2+ allies move, commit to objective setup; if not, take guaranteed map value, camps/waves/vision, then reset instead of dying alone for the correct call.")
        if len(lines) >= 6:
            break
    return "\n".join(lines) if lines else "No obvious enemy-dead objective call found. Default adaptation: ping early, watch ally movement, and take guaranteed value if the team does not move."


def _enemy_threat_plan(events: list[dict], participants: list[dict], player: dict, frames: dict[int, list[dict]]) -> str:
    pt = player["teamId"]
    threats = []
    for p in participants:
        if p["teamId"] == pt:
            continue
        pid = p["participantId"]
        killed_player = sum(1 for e in events if e["type"] == "CHAMPION_KILL" and e.get("killerId") == pid and e.get("victimId") == player["participantId"])
        killed_allies = sum(1 for e in events if e["type"] == "CHAMPION_KILL" and e.get("killerId") == pid and _side(participants, e.get("victimId"), pt) == "ally")
        last_frame = _frame_for_participant(frames, p, 99 * 60_000)
        gold = last_frame["total_gold"] if last_frame else 0
        score = killed_player * 5 + killed_allies * 2 + gold // 3000
        threats.append((score, p, killed_player, killed_allies, gold))
    threats.sort(key=lambda x: x[0], reverse=True)
    lines = []
    for idx, (_, p, killed_player, killed_allies, gold) in enumerate(threats[:3], 1):
        role = p.get("teamPosition") or "?"
        reason = f"{killed_player} player kill(s), {killed_allies} allied kill(s), ~{gold:,}g"
        rule = "do not enter mid/river choke without vision/shrooms/team spacing" if idx == 1 else "track before committing to side or objective setup"
        if role == "JUNGLE":
            rule = "track before Baron/Dragon; objective threat matters even when not directly killing you"
        lines.append(f"Threat #{idx}: {p['championName']} ({role})")
        lines.append(f"  Reason: {reason}")
        lines.append(f"  Rule: {rule}")
        lines.append("  Ability caveat: possible CC/pick pattern from kill clusters, but ability hits are not confirmed by Riot timeline data.")
    return "\n".join(lines)


def _champion_identity_plan(player: dict) -> str:
    if player.get("championName", "").lower() != "teemo":
        return "Champion-specific plan unavailable; use phase/mode recommendations above."
    return "\n".join([
        "Teemo early: farm well, pressure lane, punish melee, take plates/tower without donating shutdown.",
        "Teemo mid: shroom exits first, enemy jungle paths second, force side response, trade top pressure for dragon only when rotation is too late.",
        "Teemo late: shroom objective entrances, avoid first face-check, play around Baron/Elder/Soul, and use side pressure only when it creates objective or structure value.",
        "After inhib/nexus turret pressure: shift from side pressure to siege/escort mode; cover flanks and help finish exposed structures.",
    ])


def _decision_quality_summary(events: list[dict], participants: list[dict], player: dict, opponent: dict | None, frames: dict[int, list[dict]]) -> str:
    ppid = player["participantId"]
    pt = player["teamId"]
    deaths = [e for e in events if e["type"] == "CHAMPION_KILL" and e.get("victimId") == ppid]
    early_frame = _frame_for_participant(frames, player, 14 * 60_000)
    opp_frame = _frame_for_participant(frames, opponent, 14 * 60_000) if opponent else None
    lane = "excellent" if early_frame and opp_frame and early_frame["total_gold"] - opp_frame["total_gold"] >= 1500 else "solid" if early_frame else "unclear"
    ally_objectives = len([e for e in events if e["type"] == "ELITE_MONSTER_KILL" and _event_side(e, participants, pt) == "ally"])
    enemy_objectives = len([e for e in events if e["type"] == "ELITE_MONSTER_KILL" and _event_side(e, participants, pt) == "enemy"])
    close_messy = any(e["timestamp"] >= 30 * 60_000 for e in deaths) and any(
        e["type"] == "BUILDING_KILL" and _event_side(e, participants, pt) == "ally" and e["timestamp"] >= 30 * 60_000
        for e in events
    )
    death_lines = []
    for i, d in enumerate(deaths, 1):
        tc = _trade_context(events, participants, pt, d["timestamp"])
        enemy_major = [e for e in _objective_conversion(events, participants, pt, d["timestamp"], 60_000) if _is_major_enemy_conversion(e)]
        if tc["pressure_trade"]:
            note = "acceptable pressure trade, exit failed"
        elif enemy_major:
            note = "bad outcome because enemy converted objective"
        else:
            note = "review positioning and fight commitment"
        death_lines.append(f"- {_ts(d['timestamp'])}: {note}")
    return "\n".join([
        "Game summary:",
        f"- Lane phase: {lane}",
        "- Side pressure: strong when it created structures/objective pressure" if player.get("championName", "").lower() == "teemo" else "- Side pressure: judge by structure/objective conversion",
        f"- Objective contribution: ally objectives {ally_objectives}, enemy objectives {enemy_objectives}",
        f"- Closing: {'messy' if close_messy else 'not clearly identified as messy'}",
        "- Main improvement: after major wins, reset the map state instead of taking the next fight.",
        "",
        "Death Review Index:",
        *(death_lines or ["- No player deaths recorded."]),
        "",
        "Win condition summary:",
        "- 10 to 20 mins: player pressure is primary if lane delta is large; otherwise play through strongest gold holder.",
        "- 20 to 30 mins: pressure should convert into towers, jungle control, and Baron/Dragon setup.",
        "- After Baron: team siege becomes the win condition, not more isolated side pressure.",
        "- After Soul/Elder: reset and next-objective setup should be priority unless the end is immediate.",
        "",
        "Main player lesson:",
        '"You are good at creating pressure. The next skill is knowing when pressure has already done its job and the correct move is to reset, siege, or force the next objective safely."',
    ])


def _win_condition_analysis(events: list[dict], participants: list[dict], player: dict, opponent: dict | None, frames: dict[int, list[dict]]) -> str:
    pt = player["teamId"]
    sections: list[str] = ["Phase Win Conditions", ""]
    for ts, tag in _strategic_timestamps(events, participants, pt):
        sections.append(_win_condition_phase(events, participants, player, opponent, frames, ts, tag))
    sections.extend([
        "Death Review Index",
        "",
    ])
    for death in [e for e in events if e["type"] == "CHAMPION_KILL" and e.get("victimId") == player["participantId"]]:
        tc = _trade_context(events, participants, pt, death["timestamp"])
        post_label, _ = _recent_major_objective(events, participants, pt, death["timestamp"])
        enemy_conversion = _objective_conversion(events, participants, pt, death["timestamp"], 60_000)
        if tc["pressure_trade"]:
            mode = "cross_map_pressure_trade"
            assessment = "macro call likely valid, exit failed"
        elif post_label == "post_soul_overfight":
            mode = "post_soul_reset_or_baron_setup"
            assessment = "bad if it becomes a messy fight and enemy converts Baron"
        elif enemy_conversion:
            mode = "avoid_fight"
            assessment = "death gave enemy objective conversion"
        else:
            mode = "avoid_fight" if death["timestamp"] >= 25 * 60_000 else "side_pressure"
            assessment = "review whether the fight fit the map state"
        sections.append(f"[{_ts(death['timestamp'])}] Candidate mode(s): {mode}")
        sections.append(f"Candidate interpretation: {assessment}")
        sections.append("Review question: what evidence supports this candidate, and what remains unresolved?")
        sections.append("")
    sections.extend([
        "Close Window Detection",
        "",
        _close_windows(events, participants, player),
        "",
        "Team Reaction / Solo Queue Adaptation",
        "",
        _team_reaction(events, participants, player, frames),
        "",
        "Enemy Threat and Avoidance Plan",
        "",
        _enemy_threat_plan(events, participants, player, frames),
        "",
        "Player Job By Champion Identity",
        "",
        _champion_identity_plan(player),
        "",
        "Decision Quality Summary",
        "",
        _decision_quality_summary(events, participants, player, opponent, frames),
    ])
    return "\n".join(sections)


def _lane_outcome(player: dict, opponent: dict | None, frames: dict[int, list[dict]]) -> str:
    if not opponent:
        return "unclear"
    pf = _at_minute(frames.get(player["participantId"], []), 14) or _nearest(frames.get(player["participantId"], []), 14 * 60_000)
    of = _at_minute(frames.get(opponent["participantId"], []), 14) or _nearest(frames.get(opponent["participantId"], []), 14 * 60_000)
    if not pf or not of:
        return "unclear"
    gold_delta = pf["total_gold"] - of["total_gold"]
    cs_delta = pf["cs"] - of["cs"]
    if gold_delta >= 2500 or cs_delta >= 50:
        return "won hard"
    if gold_delta >= 900 or cs_delta >= 20:
        return "won"
    if gold_delta <= -1500 or cs_delta <= -30:
        return "lost"
    return "even"


def _lane_phase_evidence(player: dict, opponent: dict | None, frames: dict[int, list[dict]], events: list[dict], participants: list[dict], opponent_info: dict | None) -> str:
    pt = player["teamId"]
    ppid = player["participantId"]
    lane_events = [
        e for e in events
        if e["timestamp"] <= 14 * 60_000
        and (
            e["type"] == "CHAMPION_KILL"
            or (e["type"] == "BUILDING_KILL" and e.get("laneType") == "TOP_LANE")
        )
    ]
    lines = [
        f"Lane outcome classification: {_lane_outcome(player, opponent, frames)}",
        "",
        _lane_phase(player, opponent, frames),
        "",
        "Lane-related early events:",
    ]
    if lane_events:
        for e in lane_events[:8]:
            if e["type"] == "CHAMPION_KILL":
                lines.append(f"- {_kill_desc(e, participants, pt, ppid)}")
            else:
                lines.append(f"- {_format_meaningful(e, participants, pt, e['timestamp'])}")
    else:
        lines.append("- No top-lane kill/tower events found before 14:00")
    lines.extend(["", "Lane opponent context:", _opponent_context(opponent, opponent_info)])
    return "\n".join(lines)


def _one_screen_summary(events: list[dict], participants: list[dict], player: dict, opponent: dict | None, frames: dict[int, list[dict]]) -> str:
    pt = player["teamId"]
    close = _close_window_records(events, participants, player)
    main_close = "none detected"
    if close:
        first = close[0]
        main_close = f"{first['start']} into {first['peak']}"
    lane = _lane_outcome(player, opponent, frames)
    ally_structures = len([e for e in events if e["type"] == "BUILDING_KILL" and _event_side(e, participants, pt) == "ally"])
    late_deaths = len([e for e in events if e["type"] == "CHAMPION_KILL" and e.get("victimId") == player["participantId"] and e["timestamp"] >= 30 * 60_000])
    return "\n".join([
        "Game read:",
        f"- Lane phase: {player['championName']} {lane}.",
        f"- Mid game: side pressure / objective conversion evidence includes {ally_structures} allied structure take(s).",
        f"- Late game: {'late deaths/fights need review' if late_deaths else 'no late player deaths found'}; check whether the game shifted from pressure to siege/end-state.",
        f"- Main close window: {main_close}.",
        "- Main review priority: identify when pressure had already created enough map value and whether the next step was reset, siege, objective setup, or end.",
    ])


def _strong_resources(participants: list[dict], frames: dict[int, list[dict]], events: list[dict], team_id: int, ts: int, n: int = 3) -> str:
    picks = _top_phase_candidates(participants, frames, events, team_id, ts)[:n]
    labels = []
    for p, f, _ in picks:
        labels.append(f"{p['championName']} {f['total_gold']:,}g/L{f['level']}" if f else p["championName"])
    return ", ".join(labels) if labels else "unknown"


def _map_state_summary(events: list[dict], participants: list[dict], player_team: int, ts: int) -> str:
    ally_buildings = [e for e in _tower_events(events, participants, player_team, ts, "ally")]
    enemy_buildings = [e for e in _tower_events(events, participants, player_team, ts, "enemy")]
    exposed = _exposed_structure_state(events, participants, player_team, ts)
    return f"ally structures taken {len(ally_buildings)}, enemy structures taken {len(enemy_buildings)}, enemy inhibs down {exposed['enemy_inhibitors_destroyed']}, enemy nexus turrets down {exposed['enemy_nexus_turrets_destroyed']}"


def _candidate_jobs(player: dict, ts: int, events: list[dict], participants: list[dict], frames: dict[int, list[dict]]) -> list[str]:
    pt = player["teamId"]
    jobs = ["unclear"]
    upcoming = _objective_spawns_in_window(events, ts, 90_000)
    exposed = _exposed_structure_state(events, participants, pt, ts)
    ally_baron_recent = any(
        e["type"] == "ELITE_MONSTER_KILL" and e.get("monsterType") == "BARON_NASHOR"
        and _event_side(e, participants, pt) == "ally" and 0 <= ts - e["timestamp"] <= 4 * 60_000
        for e in events
    )
    if exposed["enemy_nexus_turrets_destroyed"]:
        jobs = ["end_game_push", "escort_supers", "shroom_flanks"]
    elif exposed["enemy_inhibitors_destroyed"] or ally_baron_recent:
        jobs = ["siege_with_team", "peel_or_hover_carries", "shroom_flanks"]
    elif upcoming:
        jobs = ["objective_setup", "avoid_facecheck", "reset_or_buy"]
    elif ts < 25 * 60_000:
        jobs = ["side_pressure", "cross_map_trade", "reset_or_buy"]
    else:
        jobs = ["side_pressure", "objective_setup", "avoid_facecheck"]
    return jobs


def _key_game_state_phases(events: list[dict], participants: list[dict], player: dict, frames: dict[int, list[dict]]) -> str:
    pt = player["teamId"]
    enemy_team = 200 if pt == 100 else 100
    lines: list[str] = []
    for ts, tag in _strategic_timestamps(events, participants, pt):
        trigger = tag or f"{_ts(ts)} snapshot"
        lines.append(f"[{_ts(ts)}{(' ' + tag) if tag else ''}]")
        lines.append("Facts:")
        lines.append(f"- Strongest allied resources: {_strong_resources(participants, frames, events, pt, ts)}")
        lines.append(f"- Strongest enemy threats: {_strong_resources(participants, frames, events, enemy_team, ts)}")
        lines.append(f"- Current map state: {_map_state_summary(events, participants, pt, ts)}")
        lines.extend(f"- Objective state: {line}" for line in _objective_state_lines(events, participants, pt, ts))
        lines.append("Context:")
        lines.append(f"- Trigger: {trigger}")
        lines.append(f"- Candidate player jobs: {', '.join(_candidate_jobs(player, ts, events, participants, frames))}")
        lines.append("Review questions:")
        lines.extend(f"- {q}" for q in _phase_review_questions(ts, tag, events, participants, player))
        lines.append("")
    return "\n".join(lines) if lines else "No phase snapshots available."


def _phase_review_questions(ts: int, tag: str, events: list[dict], participants: list[dict], player: dict) -> list[str]:
    pt = player["teamId"]
    upcoming = _objective_spawns_in_window(events, ts, 90_000)
    exposed = _exposed_structure_state(events, participants, pt, ts)
    soul_times = _dragon_soul_times(events, participants)
    baron_live = ts >= 20 * 60_000 and "alive/contestable" in _objective_state(events, participants, ts, "BARON_NASHOR", 20 * 60_000, 6 * 60_000)
    if "Baron" in tag:
        return [
            "Did the team reset and siege cleanly with Baron?",
            f"Did {player['championName']} shift from side-pressure mode to flank-shroom / wave-escort mode?",
        ]
    if "Soul" in tag or (pt in soul_times and abs(ts - soul_times[pt]) <= 60_000):
        return [
            "Should the team reset and set Baron after Soul?",
            "Was the next mid fight necessary while Baron was live?",
        ]
    if "Elder" in tag:
        return [
            "Did Elder convert into the end, or did the team still need wave/structure setup?",
            "Was the player entry necessary, or was the team already committed?",
        ]
    if "Base pressure" in tag or exposed["enemy_nexus_turrets_destroyed"] or exposed["enemy_inhibitors_destroyed"]:
        return [
            "Why did the game not end after base/Nexus turret pressure?",
            "Were waves, death timers, enemy threats, or lack of reset preventing the finish?",
        ]
    if upcoming and player.get("teamPosition") == "TOP" and player.get("championName", "").lower() == "teemo":
        return [
            "Was Dragon realistically contestable from top?",
            "Was Teemo better served trading top pressure, crashing/resetting, or moving early?",
        ]
    if ts == 14 * 60_000:
        return [
            "Could Teemo convert top lead into tier 2, jungle control, or Baron-side pressure without giving shutdown?",
        ]
    if baron_live:
        return [
            "Should Teemo pressure side to create Baron control, or group earlier because enemy threats are growing?",
        ]
    return ["What map evidence supports each candidate job, and what remains unresolved?"]


def _close_window_records(events: list[dict], participants: list[dict], player: dict) -> list[dict]:
    pt = player["teamId"]
    records = []
    starts = [
        e for e in events
        if e["type"] == "ELITE_MONSTER_KILL"
        and _event_side(e, participants, pt) == "ally"
        and (e.get("monsterType") == "BARON_NASHOR" or e.get("monsterSubType") == "ELDER_DRAGON")
    ]
    for start in starts:
        ts = start["timestamp"]
        window_end = ts + 5 * 60_000
        structures = [
            e for e in events
            if e["type"] == "BUILDING_KILL" and ts <= e["timestamp"] <= window_end and _event_side(e, participants, pt) == "ally"
        ]
        if not structures:
            continue
        peak = max(structures, key=lambda e: (2 if e.get("towerType") == "NEXUS_TURRET" else 1 if e.get("towerType") == "BASE_TURRET" or e.get("buildingType") == "INHIBITOR_BUILDING" else 0, e["timestamp"]))
        exposed = _exposed_structure_state(events, participants, pt, peak["timestamp"])
        player_deaths = [e for e in events if e["type"] == "CHAMPION_KILL" and e.get("victimId") == player["participantId"] and ts <= e["timestamp"] <= window_end]
        enemy_deaths = [e for e in events if e["type"] == "CHAMPION_KILL" and _side(participants, e.get("victimId"), pt) == "enemy" and ts <= e["timestamp"] <= window_end]
        ally_deaths = [e for e in events if e["type"] == "CHAMPION_KILL" and _side(participants, e.get("victimId"), pt) == "ally" and ts <= e["timestamp"] <= window_end]
        patterns = ["two_lane_baron_siege", "shroom_flanks", "reset_then_push"]
        if exposed["enemy_inhibitors_destroyed"]:
            patterns.append("escort_supers")
        if exposed["enemy_nexus_turrets_destroyed"]:
            patterns.append("end_now")
        game_end_ts = next((e["timestamp"] for e in events if e["type"] == "GAME_END" and ts <= e["timestamp"] <= window_end), None)
        nexus_kill_ts = next((e["timestamp"] for e in structures if e.get("buildingType") == "NEXUS_BUILDING"), None)
        records.append({
            "start": f"{_ts(ts)} ally {_objective_name(start)}",
            "peak": f"{_ts(peak['timestamp'])} {_building_victim_side(peak, participants, pt)} {_building_name(peak)} destroyed",
            "structures": structures,
            "inhibs": exposed["enemy_inhibitors_destroyed"],
            "nexus": exposed["enemy_nexus_turrets_destroyed"],
            "player_deaths": player_deaths,
            "enemy_deaths": enemy_deaths,
            "ally_deaths": ally_deaths,
            "patterns": patterns,
            "game_ended": bool(game_end_ts or nexus_kill_ts),
        })
    return records


def _close_window_review(events: list[dict], participants: list[dict], player: dict, info: dict) -> str:
    records = _close_window_records(events, participants, player)
    if not records:
        return "No clear close window detected from Baron/Elder into structure pressure."
    lines: list[str] = []
    for r in records:
        lines.append(f"close_window_started: {r['start']}")
        lines.append(f"close_window_peak: {r['peak']}")
        lines.append("Facts:")
        lines.append(f"- structures_taken: {', '.join(_building_victim_side(e, participants, player['teamId']) + ' ' + _building_name(e) for e in r['structures'][:8])}")
        lines.append(f"- inhibitors_down: {r['inhibs']}")
        lines.append(f"- nexus_turrets_down: {r['nexus']}")
        lines.append(f"- player_alive/dead during window: {'dead at ' + ', '.join(_ts(e['timestamp']) for e in r['player_deaths']) if r['player_deaths'] else 'alive/no player death recorded'}")
        lines.append(f"- enemy deaths during window: {len(r['enemy_deaths'])}; allied deaths during window: {len(r['ally_deaths'])}")
        lines.append(f"- game_ended: {'yes' if r['game_ended'] else 'no'}")
        lines.append("Candidate close patterns:")
        lines.append(f"- {', '.join(r['patterns'])}")
        lines.append("Review question:")
        lines.append("- Why did the game continue after Nexus turret/base pressure?")
        lines.append("")
    return "\n".join(lines)


def _death_review_index(events: list[dict], participants: list[dict], player: dict) -> str:
    pt = player["teamId"]
    rows = []
    for d in [e for e in events if e["type"] == "CHAMPION_KILL" and e.get("victimId") == player["participantId"]]:
        ts = d["timestamp"]
        tc = _trade_context(events, participants, pt, d["timestamp"])
        enemy_major = [e for e in _objective_conversion(events, participants, pt, ts, 60_000) if _is_major_enemy_conversion(e)]
        ally_major = [
            e for e in _meaningful_events(events, ts, ts + 60_000)
            if _event_side(e, participants, pt) == "ally"
            and (
                e["type"] == "ELITE_MONSTER_KILL"
                or e.get("buildingType") == "INHIBITOR_BUILDING"
                or e.get("towerType") in {"BASE_TURRET", "NEXUS_TURRET"}
            )
        ]
        post_label, _ = _recent_major_objective(events, participants, pt, d["timestamp"])
        around = [e for e in events if e["type"] == "CHAMPION_KILL" and abs(e["timestamp"] - d["timestamp"]) <= 10_000]
        if enemy_major:
            if post_label == "post_soul_overfight" or len(around) >= 3:
                dtype = "late_game_teamfight_death"
            elif post_label:
                dtype = "post_major_objective_overfight"
            else:
                dtype = "bad_objective_conversion"
            conversion = enemy_major[0]
            elapsed = (conversion["timestamp"] - ts) // 1000
            outcome = f"enemy {_major_conversion_name(conversion)} within {elapsed}s"
            interp = f"{post_label.replace('_', ' ') + ' / ' if post_label else ''}bad objective conversion"
            priority = "high"
            question = "should the team reset or set Baron after Soul instead of fighting mid?" if post_label == "post_soul_overfight" else "could this death have been avoided before the enemy objective conversion?"
        elif ally_major:
            dtype = "objective_fight_death" if any(e["type"] == "ELITE_MONSTER_KILL" for e in ally_major) else "acceptable_trade_death / late_game_teamfight_death"
            conversion = ally_major[0]
            elapsed = (conversion["timestamp"] - ts) // 1000
            outcome = f"ally {_major_conversion_name(conversion)} within {elapsed}s"
            interp = "likely acceptable objective fight death" if conversion["type"] == "ELITE_MONSTER_KILL" else "acceptable or positive trade after structure pressure"
            priority = "low to medium" if conversion["type"] == "ELITE_MONSTER_KILL" else "medium"
            question = "was the player entry/positioning necessary, or was the team already committed?"
        elif post_label:
            dtype = "post_major_objective_overfight"
            outcome = "no enemy major objective conversion found"
            interp = post_label.replace("_", " ")
            priority = "medium"
            question = "did the team need to keep fighting after the major objective?"
        elif tc["pressure_trade"]:
            dtype = "pressure_trade_death"
            outcome = "mixed map outcome"
            interp = "valid pressure trade, exit failed"
            priority = "medium"
            question = "could Teemo exit after the pressure play before enemy collapse?"
        elif len(around) >= 3:
            dtype = "late_game_teamfight_death" if d["timestamp"] >= 25 * 60_000 else "objective_fight_death"
            outcome = "no immediate major objective conversion found"
            interp = "messy teamfight or objective fight"
            priority = "medium"
            question = "was the team already committed, or was this avoidable fight entry?"
        else:
            dtype = "isolated_pick"
            outcome = "no immediate major objective conversion found"
            interp = "needs coach review"
            priority = "medium"
            question = "was this death an isolated pick, or is there missing pressure/objective context?"
        rows.extend([
            f"{_ts(d['timestamp'])}",
            f"- factual_type: {dtype}",
            f"- map_outcome: {outcome}",
            f"- review_priority: {priority}",
            f"- candidate_interpretation: {interp}",
            f"- unresolved_question: {question}",
            "",
        ])
    return "\n".join(rows) if rows else "No player deaths recorded."


def _objective_team_reaction_review(events: list[dict], participants: list[dict], player: dict, frames: dict[int, list[dict]]) -> str:
    pt = player["teamId"]
    lines: list[str] = []
    objectives = [
        e for e in events
        if e["type"] == "ELITE_MONSTER_KILL" and e.get("monsterType") in {"DRAGON", "BARON_NASHOR"}
    ]
    for obj in objectives[:8]:
        ts = obj["timestamp"]
        pf = _frame_for_participant(frames, player, ts)
        player_zone = _zone(pf.get("x"), pf.get("y")) if pf else "unknown"
        allies_near = 0
        for p in participants:
            if p["teamId"] != pt:
                continue
            f = _frame_for_participant(frames, p, ts)
            if f and _zone(f.get("x"), f.get("y")) in {"dragon_pit", "baron_pit", "top_river", "bot_river"}:
                allies_near += 1
        involvement = "secured" if obj.get("killerId") == player["participantId"] else "assisted" if player["participantId"] in obj.get("assistingParticipantIds", []) else "nearby" if player_zone in {"dragon_pit", "baron_pit"} else "absent"
        secured_by = _event_side(obj, participants, pt)
        if secured_by == "ally":
            reaction = "team_secured"
        elif allies_near >= 2:
            reaction = "team_contested_lost"
        elif involvement in {"nearby", "assisted", "secured"} and allies_near <= 1:
            reaction = "player_forced_alone"
        elif player_zone in _TOP_ZONES | {"bot_lane"}:
            reaction = "player_absent_trade_possible"
        elif allies_near <= 1:
            reaction = "team_ignored_enemy_take"
        else:
            reaction = "unclear"
        lines.append(f"[{_ts(ts)}] {_objective_name(obj)}")
        lines.append("Facts:")
        lines.append(f"- secured_by: {secured_by}")
        lines.append(f"- player_location: {player_zone}; player_involvement: {involvement}")
        lines.append(f"- junglers: {'; '.join(_jungler_status(events, participants, pt, ts))}")
        lines.append(f"- nearest strong allied resources: {_strong_resources(participants, frames, events, pt, ts)}")
        lines.append(f"- team_reaction: {reaction}")
        lines.append("Review questions:")
        lines.append("- Was the objective realistically contestable?")
        lines.append("- If the correct call was ignored, did the player adapt safely?")
        lines.append("- Was there a guaranteed cross-map trade instead?")
        lines.append("")
    return "\n".join(lines) if lines else "No major objective events found."


def _enemy_threat_context(events: list[dict], participants: list[dict], player: dict, frames: dict[int, list[dict]]) -> str:
    pt = player["teamId"]
    threats = []
    for p in participants:
        if p["teamId"] == pt:
            continue
        pid = p["participantId"]
        killed_player = sum(1 for e in events if e["type"] == "CHAMPION_KILL" and e.get("killerId") == pid and e.get("victimId") == player["participantId"])
        killed_allies = sum(1 for e in events if e["type"] == "CHAMPION_KILL" and e.get("killerId") == pid and _side(participants, e.get("victimId"), pt) == "ally")
        frame = _frame_for_participant(frames, p, 99 * 60_000)
        item_state = _inventory_state(events, pid, 99 * 60_000, frame["current_gold"] if frame else 0, frame["level"] if frame else "?")
        role = p.get("teamPosition") or "?"
        threat_type = "objective secure" if role == "JUNGLE" else "frontline access" if role in {"TOP", "UTILITY"} else "pick/CC/burst" if p["championName"] in {"Veigar", "Lux", "Morgana", "Ahri"} else "burst/poke"
        score = killed_player * 5 + killed_allies * 2 + (frame["total_gold"] // 3000 if frame else 0)
        threats.append((score, p, killed_player, killed_allies, frame, item_state, threat_type))
    threats.sort(key=lambda x: x[0], reverse=True)
    lines = []
    for _, p, killed_player, killed_allies, frame, item_state, threat_type in threats[:3]:
        lines.append(p["championName"])
        lines.append(f"- role: {p.get('teamPosition') or '?'}")
        lines.append(f"- kills/gold/items: player deaths caused {killed_player}, allied deaths caused {killed_allies}, gold {frame['total_gold']:,}g" if frame else f"- kills/gold/items: player deaths caused {killed_player}, allied deaths caused {killed_allies}, gold unknown")
        lines.append(f"- items: {', '.join(str(i) + ':' + _item_name(i) for i in item_state['items'][:6]) if item_state else 'unknown'}")
        lines.append(f"- threat_type: {threat_type}")
        lines.append("- confidence caveat: ability hits are not available; CC/pick labels are pattern context, not confirmed ability causes.")
        lines.append("- review_question: did the player enter this champion's threat area without vision, spacing, or shroomed corridors?")
        lines.append("")
    return "\n".join(lines)


def _champion_identity_context(player: dict) -> str:
    return _champion_identity_plan(player)


def _coach_handoff_summary(events: list[dict], participants: list[dict], player: dict, opponent: dict | None, frames: dict[int, list[dict]]) -> str:
    pt = player["teamId"]
    close_records = _close_window_records(events, participants, player)
    deaths = [e for e in events if e["type"] == "CHAMPION_KILL" and e.get("victimId") == player["participantId"]]
    pf14 = _at_minute(frames.get(player["participantId"], []), 14) or _nearest(frames.get(player["participantId"], []), 14 * 60_000)
    of14 = (_at_minute(frames.get(opponent["participantId"], []), 14) or _nearest(frames.get(opponent["participantId"], []), 14 * 60_000)) if opponent else None
    evidence = []
    if pf14 and of14:
        evidence.append(f"{player['championName']} lane outcome: {_lane_outcome(player, opponent, frames)} ({pf14['cs'] - of14['cs']:+} CS, {pf14['total_gold'] - of14['total_gold']:+,}g at 14).")
    else:
        evidence.append(f"{player['championName']} lane outcome: {_lane_outcome(player, opponent, frames)}.")
    if close_records:
        evidence.append(f"Close window: {close_records[0]['start']} -> {close_records[0]['peak']}; game_ended={'yes' if close_records[0]['game_ended'] else 'no'}.")
    for d in deaths:
        enemy_major = _objective_conversion(events, participants, pt, d["timestamp"], 60_000)
        post_label, post_evt = _recent_major_objective(events, participants, pt, d["timestamp"])
        trade = _trade_context(events, participants, pt, d["timestamp"])
        if enemy_major:
            evidence.append(f"Death at {_ts(d['timestamp'])}: enemy converted {_major_conversion_name(enemy_major[0])} {(enemy_major[0]['timestamp'] - d['timestamp']) // 1000}s later.")
        elif post_label:
            evidence.append(f"Death at {_ts(d['timestamp'])}: {post_label.replace('_', ' ')} candidate; no enemy major conversion in 60s.")
        elif trade["pressure_trade"]:
            evidence.append(f"Death at {_ts(d['timestamp'])}: pressure trade / exit-failure candidate.")
    review_areas = [
        "Closing after Baron/inhib/nexus turret pressure." if close_records else "Finding or creating a clear close window.",
        "Post-Soul/objective-risk fights that changed objective control.",
        "Exit planning after pressure trades.",
    ]
    questions = [
        "When did the game shift from side pressure to siege/escort?",
        "Should the team reset or set Baron after Soul instead of taking a mid fight?",
        "Could pressure plays have cleaner exits or safer adaptations when team movement was unclear?",
    ]
    return "\n".join([
        "Most useful review areas:",
        *(f"{i}. {area}" for i, area in enumerate(review_areas, 1)),
        "",
        "Evidence highlights:",
        *(f"- {line}" for line in evidence[:5]),
        "",
        "Open questions for coach:",
        *(f"- {q}" for q in questions),
    ])


def build_coaching_report(match_data: dict, timeline_data: dict, puuid: str, game_name: str = "", tag_line: str = "", opponent_info: dict | None = None, output_mode: str = "compact") -> str:
    info = match_data["info"]
    participants = info["participants"]
    player = _find_player(participants, puuid, game_name, tag_line)
    if not player:
        return f"Error: Could not find {game_name}#{tag_line} in this match's participants."
    opponent = _find_opponent(participants, player)
    events = _parse_events(timeline_data)
    frames = _parse_frames(timeline_data)
    label = f"{game_name}#{tag_line}" if game_name else puuid[:16] + "..."
    match_id = match_data.get("metadata", {}).get("matchId") or str(info.get("gameId", ""))
    mode = (output_mode or "compact").lower()
    sections = [
        f"COACHING PACKET - {label}",
        "=" * 60,
        _sep("SECTION 1: MATCH HEADER"),
        _match_header(info, player, participants, match_id, opponent),
        _sep("SECTION 2: ONE-SCREEN SUMMARY"),
        _one_screen_summary(events, participants, player, opponent, frames),
        _sep("SECTION 3: LANE PHASE EVIDENCE"),
        _lane_phase_evidence(player, opponent, frames, events, participants, opponent_info),
        _sep("SECTION 4: KEY GAME STATE PHASES"),
        _key_game_state_phases(events, participants, player, frames),
        _sep("SECTION 5: CLOSE WINDOW DETECTION"),
        _close_window_review(events, participants, player, info),
        _sep("SECTION 6: DEATH REVIEW PACKETS"),
        _deaths(events, participants, player, opponent, frames),
        _sep("DEATH REVIEW INDEX"),
        _death_review_index(events, participants, player),
        _sep("SECTION 7: OBJECTIVE AND TEAM REACTION REVIEW"),
        _objective_team_reaction_review(events, participants, player, frames),
        _sep("SECTION 8: ENEMY THREAT AND AVOIDANCE CONTEXT"),
        _enemy_threat_context(events, participants, player, frames),
        _sep("SECTION 9: CHAMPION IDENTITY CONTEXT"),
        _champion_identity_context(player),
        _sep("SECTION 10: COACH HANDOFF SUMMARY"),
        _coach_handoff_summary(events, participants, player, opponent, frames),
    ]
    dragon_events = _dragon_kills(events)
    shroom_section = _teemo_shrooms(events, participants, player, dragon_events)
    if shroom_section is not None:
        sections.append(_sep("TEEMO SHROOM EVENT CONTEXT"))
        sections.append(shroom_section)
    sections.append(_sep("KEY EVENT TIMELINE"))
    sections.append(_timeline(events, participants, player, info))
    if mode in {"full_debug", "debug", "full"}:
        sections.append(_sep("FULL DEBUG: TACTICAL WINDOW DIAGNOSTICS"))
        sections.append(_decision_windows(events, participants, player, frames))
    report = "\n".join(sections)
    if mode in {"json", "json_export"}:
        return json.dumps({
            "match_id": match_id,
            "player": label,
            "output_mode": "json_export",
            "packet_text": report,
        }, indent=2)
    return report
