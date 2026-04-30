from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import sqlite3, time, json, os
from datetime import datetime

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB     = "cs2_stats.db"
CONFIG = "config.json"

# ─── CONFIG ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    """Reload config from disk on every call (cheap for small file)."""
    if not os.path.exists(CONFIG):
        return {"allowed_steam_ids": []}
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except Exception:
        return {"allowed_steam_ids": []}

def save_config(cfg: dict):
    with open(CONFIG, "w") as f:
        json.dump(cfg, f, indent=2)

def is_allowed(steam_id: str) -> bool:
    """Returns True if the Steam ID is whitelisted (or whitelist is empty = allow all)."""
    cfg = load_config()
    allowed = cfg.get("allowed_steam_ids", [])
    if not allowed:          # empty list → allow everything (setup mode)
        return True
    return steam_id in allowed

# ─── SCHEMA ──────────────────────────────────────────────────────────────────

def init_db():
    with sqlite3.connect(DB) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS matches (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id        TEXT UNIQUE,
            map_name        TEXT,
            mode            TEXT,
            start_time      INTEGER,
            end_time        INTEGER,
            duration        INTEGER,
            kills           INTEGER DEFAULT 0,
            deaths          INTEGER DEFAULT 0,
            assists         INTEGER DEFAULT 0,
            mvps            INTEGER DEFAULT 0,
            score           INTEGER DEFAULT 0,
            hs_kills        INTEGER DEFAULT 0,
            total_damage    INTEGER DEFAULT 0,
            rounds_played   INTEGER DEFAULT 0,
            rounds_won      INTEGER DEFAULT 0,
            rounds_lost     INTEGER DEFAULT 0,
            result          TEXT,
            team_final      TEXT,
            ct_score        INTEGER DEFAULT 0,
            t_score         INTEGER DEFAULT 0,
            ct_kills        INTEGER DEFAULT 0,
            t_kills         INTEGER DEFAULT 0,
            ct_deaths       INTEGER DEFAULT 0,
            t_deaths        INTEGER DEFAULT 0,
            ct_rounds_won   INTEGER DEFAULT 0,
            t_rounds_won    INTEGER DEFAULT 0,
            player_name     TEXT,
            steam_id        TEXT
        );
        CREATE TABLE IF NOT EXISTS rounds (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id        TEXT,
            round_number    INTEGER,
            start_time      INTEGER,
            end_time        INTEGER,
            player_team     TEXT,
            kills           INTEGER DEFAULT 0,
            hs_kills        INTEGER DEFAULT 0,
            damage          INTEGER DEFAULT 0,
            start_equip     INTEGER DEFAULT 0,
            start_money     INTEGER DEFAULT 0,
            eco_type        TEXT,
            survived        INTEGER DEFAULT 0,
            result          TEXT,
            win_reason      TEXT,
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        );
        CREATE TABLE IF NOT EXISTS weapon_stats (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id    TEXT,
            weapon_name TEXT,
            kills       INTEGER DEFAULT 0,
            hs_kills    INTEGER DEFAULT 0,
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time     INTEGER,
            end_time       INTEGER,
            duration       INTEGER DEFAULT 0,
            matches_played INTEGER DEFAULT 0,
            date           TEXT
        );
        """)
    _migrate()

def _migrate():
    new_cols = {
        "matches": [
            ("ct_kills",      "INTEGER DEFAULT 0"),
            ("t_kills",       "INTEGER DEFAULT 0"),
            ("ct_deaths",     "INTEGER DEFAULT 0"),
            ("t_deaths",      "INTEGER DEFAULT 0"),
            ("ct_rounds_won", "INTEGER DEFAULT 0"),
            ("t_rounds_won",  "INTEGER DEFAULT 0"),
        ],
        "rounds": [
            ("player_team", "TEXT"),
            ("start_equip", "INTEGER DEFAULT 0"),
            ("start_money", "INTEGER DEFAULT 0"),
            ("eco_type",    "TEXT"),
            ("survived",    "INTEGER DEFAULT 0"),
        ],
    }
    with sqlite3.connect(DB) as c:
        for table, cols in new_cols.items():
            existing = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            for col, typedef in cols:
                if col not in existing:
                    try:
                        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
                    except Exception:
                        pass

init_db()

# ─── STATE ───────────────────────────────────────────────────────────────────

S = {
    "match_id": None, "round": 0,
    "map_phase": None, "round_phase": None,
    "prev_r_kills": 0, "prev_r_hs": 0, "prev_r_dmg": 0,
    "match_start": None, "round_start": None,
    "session_start": None, "session_id": None,
    "active_weapon": None, "last_seen": None,
    "player_team": None,
    "round_start_equip": 0, "round_start_money": 0,
    # Live HUD values
    "live_kills": 0, "live_deaths": 0, "live_health": 100,
    "live_money": 0, "live_round_dmg": 0,
    # Cumulative damage tracker for real-time ADR
    "cumulative_damage": 0,
    # Track which steam_id this session belongs to
    "session_steam_id": None,
}

def eco_type(equip: int, rnd: int) -> str:
    if rnd in (1, 16): return "pistol"
    if equip < 1500:   return "eco"
    if equip < 3000:   return "force"
    if equip < 5000:   return "semi"
    return "full"

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

# ─── GSI ─────────────────────────────────────────────────────────────────────

@app.post("/gsi")
async def gsi(req: Request):
    try:
        data = await req.json()
    except Exception:
        return {"ok": False}

    # ── Steam ID filtering ────────────────────────────────────────────────────
    provider    = data.get("provider", {})
    player      = data.get("player", {})
    steam_id    = provider.get("steamid", player.get("steamid", ""))

    # Ignore spectator data: the provider steamid is the local player,
    # player steamid may differ if spectating. Only process if they match.
    player_sid = player.get("steamid", steam_id)
    if player_sid and player_sid != steam_id:
        # We're spectating someone else — discard entirely
        return {"ok": False, "reason": "spectating"}

    # Whitelist check
    if not is_allowed(steam_id):
        return {"ok": False, "reason": "not_whitelisted"}

    # ── Parse payload ─────────────────────────────────────────────────────────
    now         = int(time.time())
    S["last_seen"] = now

    map_d       = data.get("map", {})
    round_d     = data.get("round", {})
    p_stats     = player.get("match_stats", {})
    p_state     = player.get("state", {})
    weapons     = player.get("weapons", {})

    map_name    = map_d.get("name", "")
    map_phase   = map_d.get("phase", "")
    map_mode    = map_d.get("mode", "")
    map_round   = map_d.get("round", 0)
    round_phase = round_d.get("phase", "")

    kills   = p_stats.get("kills", 0)
    deaths  = p_stats.get("deaths", 0)
    assists = p_stats.get("assists", 0)
    mvps    = p_stats.get("mvps", 0)
    score   = p_stats.get("score", 0)

    r_kills = p_state.get("round_kills", 0)
    r_hs    = p_state.get("round_killhs", 0)
    r_dmg   = p_state.get("round_totaldmg", 0)
    equip   = p_state.get("equip_value", 0)
    money   = p_state.get("money", 0)
    health  = p_state.get("health", 100)

    player_name = player.get("name", "")
    player_team = player.get("team", "")

    # Live HUD
    S["player_team"]    = player_team
    S["live_kills"]     = kills
    S["live_deaths"]    = deaths
    S["live_health"]    = health
    S["live_money"]     = money
    S["live_round_dmg"] = r_dmg

    # Active weapon
    for w in weapons.values():
        if w.get("state") == "active":
            S["active_weapon"] = w.get("name", "").replace("weapon_", "")
            break

    # ── Session ───────────────────────────────────────────────────────────────
    if S["session_start"] is None:
        S["session_start"]   = now
        S["session_id"]      = now
        S["session_steam_id"] = steam_id
        with db() as c:
            c.execute("INSERT INTO sessions (start_time, date) VALUES (?,?)",
                      (now, datetime.now().strftime("%Y-%m-%d")))

    # ── Match start ───────────────────────────────────────────────────────────
    if map_phase == "live" and S["match_id"] is None and map_name:
        mid = f"{steam_id}_{map_name}_{now}"
        S["match_id"]         = mid
        S["match_start"]      = now
        S["cumulative_damage"] = 0
        with db() as c:
            c.execute("""INSERT OR IGNORE INTO matches
                (match_id,map_name,mode,start_time,player_name,steam_id)
                VALUES (?,?,?,?,?,?)""",
                (mid, map_name, map_mode, now, player_name, steam_id))

    if S["match_id"]:
        # ── Round start ───────────────────────────────────────────────────────
        if round_phase == "live" and S["round_phase"] != "live":
            S["round_start"]       = now
            S["prev_r_kills"]      = 0
            S["prev_r_hs"]         = 0
            S["prev_r_dmg"]        = 0
            S["round"]             = map_round
            S["round_start_equip"] = equip
            S["round_start_money"] = money

        # ── Kill detection ────────────────────────────────────────────────────
        if round_phase == "live" and r_kills > S["prev_r_kills"]:
            new_k  = r_kills - S["prev_r_kills"]
            new_hs = max(0, r_hs - S["prev_r_hs"])
            weapon = S["active_weapon"] or "unknown"
            with db() as c:
                row = c.execute(
                    "SELECT id FROM weapon_stats WHERE match_id=? AND weapon_name=?",
                    (S["match_id"], weapon)).fetchone()
                if row:
                    c.execute("UPDATE weapon_stats SET kills=kills+?,hs_kills=hs_kills+? WHERE id=?",
                              (new_k, new_hs, row["id"]))
                else:
                    c.execute("INSERT INTO weapon_stats (match_id,weapon_name,kills,hs_kills) VALUES (?,?,?,?)",
                              (S["match_id"], weapon, new_k, new_hs))
            col = "ct_kills" if player_team == "CT" else "t_kills"
            with db() as c:
                c.execute(f"UPDATE matches SET {col}={col}+? WHERE match_id=?",
                          (new_k, S["match_id"]))

        # ── Round end ─────────────────────────────────────────────────────────
        if round_phase == "over" and S["round_phase"] != "over":
            win_team = round_d.get("win_team", "")
            cur_team = S["player_team"] or player_team
            result   = "win" if win_team == cur_team else "loss"
            survived = 1 if health > 0 else 0
            etype    = eco_type(S["round_start_equip"], map_round)

            # Accumulate damage for real-time ADR
            S["cumulative_damage"] += r_dmg

            with db() as c:
                c.execute("""INSERT INTO rounds
                    (match_id,round_number,start_time,end_time,player_team,
                     kills,hs_kills,damage,start_equip,start_money,
                     eco_type,survived,result,win_reason)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (S["match_id"], map_round,
                     S["round_start"] or now, now, cur_team,
                     r_kills, r_hs, r_dmg,
                     S["round_start_equip"], S["round_start_money"],
                     etype, survived, result, win_team))

            if result == "win":
                col = "ct_rounds_won" if cur_team == "CT" else "t_rounds_won"
                with db() as c:
                    c.execute(f"UPDATE matches SET {col}={col}+1 WHERE match_id=?",
                              (S["match_id"],))
            if survived == 0:
                col = "ct_deaths" if cur_team == "CT" else "t_deaths"
                with db() as c:
                    c.execute(f"UPDATE matches SET {col}={col}+1 WHERE match_id=?",
                              (S["match_id"],))

        S["prev_r_kills"] = r_kills
        S["prev_r_hs"]    = r_hs
        S["prev_r_dmg"]   = r_dmg

        # Continuous match update — also sync cumulative damage for ADR mid-match
        ct = map_d.get("team_ct", {}).get("score", 0)
        t  = map_d.get("team_t",  {}).get("score", 0)
        with db() as c:
            c.execute("""UPDATE matches SET kills=?,deaths=?,assists=?,mvps=?,score=?,
                ct_score=?,t_score=?,rounds_played=?,total_damage=? WHERE match_id=?""",
                (kills, deaths, assists, mvps, score,
                 ct, t, ct+t, S["cumulative_damage"], S["match_id"]))

        # ── Match end ─────────────────────────────────────────────────────────
        if map_phase == "gameover" and S["map_phase"] != "gameover":
            ct  = map_d.get("team_ct", {}).get("score", 0)
            t   = map_d.get("team_t",  {}).get("score", 0)
            my  = ct if player_team == "CT" else t
            opp = t  if player_team == "CT" else ct
            result = "win" if my > opp else ("loss" if my < opp else "tie")
            dur = now - (S["match_start"] or now)

            with db() as c:
                hs  = c.execute("SELECT COALESCE(SUM(hs_kills),0) FROM weapon_stats WHERE match_id=?",
                                (S["match_id"],)).fetchone()[0]
                rw  = c.execute("SELECT COUNT(*) FROM rounds WHERE match_id=? AND result='win'",
                                (S["match_id"],)).fetchone()[0]
                rl  = c.execute("SELECT COUNT(*) FROM rounds WHERE match_id=? AND result='loss'",
                                (S["match_id"],)).fetchone()[0]
                c.execute("""UPDATE matches SET end_time=?,duration=?,result=?,hs_kills=?,
                    rounds_won=?,rounds_lost=?,team_final=? WHERE match_id=?""",
                    (now, dur, result, hs, rw, rl, player_team, S["match_id"]))
                c.execute("""UPDATE sessions SET end_time=?,duration=?,matches_played=matches_played+1
                    WHERE start_time=?""",
                    (now, now - (S["session_start"] or now), S["session_id"]))

            S["match_id"] = S["match_start"] = None
            S["cumulative_damage"] = 0

    S["map_phase"]   = map_phase
    S["round_phase"] = round_phase
    return {"ok": True}

# ─── CONFIG API ──────────────────────────────────────────────────────────────

@app.get("/api/config")
def get_config():
    return load_config()

@app.post("/api/config/add_id")
async def add_steam_id(req: Request):
    body = await req.json()
    sid  = str(body.get("steam_id", "")).strip()
    if not sid:
        return JSONResponse({"error": "steam_id manquant"}, status_code=400)
    cfg = load_config()
    if sid not in cfg["allowed_steam_ids"]:
        cfg["allowed_steam_ids"].append(sid)
        save_config(cfg)
    return {"ok": True, "allowed_steam_ids": cfg["allowed_steam_ids"]}

@app.post("/api/config/remove_id")
async def remove_steam_id(req: Request):
    body = await req.json()
    sid  = str(body.get("steam_id", "")).strip()
    cfg  = load_config()
    cfg["allowed_steam_ids"] = [x for x in cfg["allowed_steam_ids"] if x != sid]
    save_config(cfg)
    return {"ok": True, "allowed_steam_ids": cfg["allowed_steam_ids"]}

# ─── STATS API ────────────────────────────────────────────────────────────────

@app.get("/api/stats")
def api_stats():
    with db() as c:
        r = c.execute("""SELECT
            COUNT(*) total_matches,
            COALESCE(SUM(kills),0)         total_kills,
            COALESCE(SUM(deaths),0)        total_deaths,
            COALESCE(SUM(assists),0)       total_assists,
            COALESCE(SUM(hs_kills),0)      total_hs,
            COALESCE(SUM(mvps),0)          total_mvps,
            COALESCE(SUM(total_damage),0)  total_damage,
            COALESCE(SUM(rounds_played),0) total_rounds,
            COALESCE(SUM(duration),0)      match_time,
            COALESCE(SUM(CASE WHEN result='win'  THEN 1 ELSE 0 END),0) wins,
            COALESCE(SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END),0) losses,
            COALESCE(SUM(CASE WHEN result='tie'  THEN 1 ELSE 0 END),0) ties,
            COALESCE(SUM(ct_kills),0)      total_ct_kills,
            COALESCE(SUM(t_kills),0)       total_t_kills,
            COALESCE(SUM(ct_deaths),0)     total_ct_deaths,
            COALESCE(SUM(t_deaths),0)      total_t_deaths,
            ROUND(AVG(score),1)            avg_score
            FROM matches WHERE result IS NOT NULL""").fetchone()
        st   = c.execute("SELECT COALESCE(SUM(duration),0) FROM sessions").fetchone()[0]
        surv = c.execute("SELECT COUNT(*) total, COALESCE(SUM(survived),0) surv FROM rounds").fetchone()
        best_kd  = c.execute("""SELECT ROUND(CAST(kills AS FLOAT)/NULLIF(deaths,0),2), map_name
            FROM matches WHERE result IS NOT NULL AND deaths>0
            ORDER BY CAST(kills AS FLOAT)/deaths DESC LIMIT 1""").fetchone()
        best_adr = c.execute("""SELECT ROUND(CAST(total_damage AS FLOAT)/NULLIF(rounds_played,0),1), map_name
            FROM matches WHERE result IS NOT NULL AND rounds_played>0 AND total_damage>0
            ORDER BY CAST(total_damage AS FLOAT)/rounds_played DESC LIMIT 1""").fetchone()

        d = dict(r)
        d["session_time"]  = st
        d["kd"]            = round(d["total_kills"] / max(d["total_deaths"], 1), 2)
        d["hs_pct"]        = round(d["total_hs"] / max(d["total_kills"], 1) * 100, 1)
        d["adr"]           = round(d["total_damage"] / max(d["total_rounds"], 1), 1)
        d["win_rate"]      = round(d["wins"] / max(d["total_matches"], 1) * 100, 1)
        d["kpr"]           = round(d["total_kills"] / max(d["total_rounds"], 1), 2)
        d["dpr"]           = round(d["total_deaths"] / max(d["total_rounds"], 1), 2)
        d["survival_rate"] = round(surv["surv"] / max(surv["total"], 1) * 100, 1)
        d["best_kd"]       = best_kd[0]  if best_kd  else None
        d["best_kd_map"]   = best_kd[1]  if best_kd  else None
        d["best_adr"]      = best_adr[0] if best_adr else None
        d["best_adr_map"]  = best_adr[1] if best_adr else None
        d["ct_kd"]         = round(d["total_ct_kills"] / max(d["total_ct_deaths"], 1), 2)
        d["t_kd"]          = round(d["total_t_kills"]  / max(d["total_t_deaths"],  1), 2)
        return d

@app.get("/api/matches")
def api_matches(limit: int = 50):
    with db() as c:
        rows = c.execute("""SELECT * FROM matches WHERE result IS NOT NULL
            ORDER BY start_time DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]

@app.get("/api/maps")
def api_maps():
    with db() as c:
        rows = c.execute("""SELECT map_name,
            COUNT(*) played,
            COALESCE(SUM(CASE WHEN result='win'  THEN 1 ELSE 0 END),0) wins,
            COALESCE(SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END),0) losses,
            COALESCE(SUM(CASE WHEN result='tie'  THEN 1 ELSE 0 END),0) ties,
            ROUND(AVG(CAST(kills AS FLOAT)/NULLIF(deaths,0)),2)                       avg_kd,
            ROUND(AVG(CAST(total_damage AS FLOAT)/NULLIF(rounds_played,0)),1)         avg_adr,
            ROUND(AVG(CAST(hs_kills AS FLOAT)/NULLIF(kills,0)*100),1)                 avg_hs,
            COALESCE(SUM(duration),0) total_time,
            COALESCE(SUM(ct_kills),0)      ct_kills,
            COALESCE(SUM(t_kills),0)       t_kills,
            COALESCE(SUM(ct_rounds_won),0) ct_rw,
            COALESCE(SUM(t_rounds_won),0)  t_rw
            FROM matches WHERE result IS NOT NULL AND map_name!=''
            GROUP BY map_name ORDER BY played DESC""").fetchall()
        return [dict(r) for r in rows]

@app.get("/api/weapons")
def api_weapons():
    with db() as c:
        rows = c.execute("""SELECT weapon_name,
            SUM(kills) total_kills, SUM(hs_kills) total_hs
            FROM weapon_stats GROUP BY weapon_name ORDER BY total_kills DESC""").fetchall()
        return [dict(r) for r in rows]

@app.get("/api/performance")
def api_performance():
    with db() as c:
        rows = c.execute("""SELECT start_time, kills, deaths, assists, hs_kills,
            total_damage, rounds_played, map_name, result, mode,
            CASE WHEN deaths>0 THEN ROUND(CAST(kills AS FLOAT)/deaths,2) ELSE kills END kd,
            CASE WHEN rounds_played>0 AND total_damage>0
                 THEN ROUND(CAST(total_damage AS FLOAT)/rounds_played,1) ELSE 0 END adr,
            ROUND(CAST(hs_kills AS FLOAT)/NULLIF(kills,0)*100,1) hs_pct
            FROM matches WHERE result IS NOT NULL
            ORDER BY start_time ASC LIMIT 60""").fetchall()
        return [dict(r) for r in rows]

@app.get("/api/modes")
def api_modes():
    with db() as c:
        rows = c.execute("""SELECT mode, COUNT(*) played,
            COALESCE(SUM(CASE WHEN result='win' THEN 1 ELSE 0 END),0) wins
            FROM matches WHERE result IS NOT NULL AND mode!=''
            GROUP BY mode ORDER BY played DESC""").fetchall()
        return [dict(r) for r in rows]

@app.get("/api/multikills")
def api_multikills():
    with db() as c:
        r = c.execute("""SELECT
            COALESCE(SUM(CASE WHEN kills=1 THEN 1 ELSE 0 END),0) k1,
            COALESCE(SUM(CASE WHEN kills=2 THEN 1 ELSE 0 END),0) k2,
            COALESCE(SUM(CASE WHEN kills=3 THEN 1 ELSE 0 END),0) k3,
            COALESCE(SUM(CASE WHEN kills=4 THEN 1 ELSE 0 END),0) k4,
            COALESCE(SUM(CASE WHEN kills>=5 THEN 1 ELSE 0 END),0) ace
            FROM rounds WHERE kills > 0""").fetchone()
        return dict(r) if r else {}

@app.get("/api/economy")
def api_economy():
    with db() as c:
        rows = c.execute("""SELECT eco_type,
            COUNT(*) rounds,
            COALESCE(SUM(CASE WHEN result='win' THEN 1 ELSE 0 END),0) wins,
            ROUND(AVG(damage),1) avg_dmg,
            ROUND(AVG(kills),2)  avg_kills
            FROM rounds WHERE eco_type IS NOT NULL
            GROUP BY eco_type""").fetchall()
        return [dict(r) for r in rows]

@app.get("/api/sides")
def api_sides():
    with db() as c:
        r = c.execute("""SELECT
            COALESCE(SUM(ct_kills),0)      ct_kills,
            COALESCE(SUM(t_kills),0)       t_kills,
            COALESCE(SUM(ct_deaths),0)     ct_deaths,
            COALESCE(SUM(t_deaths),0)      t_deaths,
            COALESCE(SUM(ct_rounds_won),0) ct_rw,
            COALESCE(SUM(t_rounds_won),0)  t_rw
            FROM matches WHERE result IS NOT NULL""").fetchone()
        rnd = c.execute("""SELECT
            COALESCE(SUM(CASE WHEN player_team='CT' THEN 1 ELSE 0 END),0)                ct_rounds,
            COALESCE(SUM(CASE WHEN player_team='T'  THEN 1 ELSE 0 END),0)                t_rounds,
            COALESCE(SUM(CASE WHEN player_team='CT' THEN damage ELSE 0 END),0)           ct_dmg,
            COALESCE(SUM(CASE WHEN player_team='T'  THEN damage ELSE 0 END),0)           t_dmg,
            COALESCE(SUM(CASE WHEN player_team='CT' AND survived=1 THEN 1 ELSE 0 END),0) ct_surv,
            COALESCE(SUM(CASE WHEN player_team='T'  AND survived=1 THEN 1 ELSE 0 END),0) t_surv
            FROM rounds""").fetchone()
        d = dict(r)
        d.update(dict(rnd))
        d["ct_kd"]        = round(d["ct_kills"] / max(d["ct_deaths"], 1), 2)
        d["t_kd"]         = round(d["t_kills"]  / max(d["t_deaths"],  1), 2)
        d["ct_wr"]        = round(d["ct_rw"]    / max(d["ct_rounds"], 1) * 100, 1)
        d["t_wr"]         = round(d["t_rw"]     / max(d["t_rounds"],  1) * 100, 1)
        d["ct_adr"]       = round(d["ct_dmg"]   / max(d["ct_rounds"], 1), 1)
        d["t_adr"]        = round(d["t_dmg"]    / max(d["t_rounds"],  1), 1)
        d["ct_surv_rate"] = round(d["ct_surv"]  / max(d["ct_rounds"], 1) * 100, 1)
        d["t_surv_rate"]  = round(d["t_surv"]   / max(d["t_rounds"],  1) * 100, 1)
        return d

@app.get("/api/streaks")
def api_streaks():
    with db() as c:
        rows = c.execute("""SELECT result FROM matches WHERE result IS NOT NULL
            ORDER BY start_time DESC LIMIT 50""").fetchall()
        results = [r["result"] for r in rows]
        if not results:
            return {"current": 0, "type": None, "best_win": 0, "best_loss": 0}
        cur_type = results[0]
        cur = 0
        for r in results:
            if r == cur_type: cur += 1
            else: break
        bw = bl = tmp = 0
        tmp_t = results[0]
        for r in results:
            if r == tmp_t: tmp += 1
            else: tmp = 1; tmp_t = r
            if tmp_t == "win":  bw = max(bw, tmp)
            if tmp_t == "loss": bl = max(bl, tmp)
        return {"current": cur, "type": cur_type, "best_win": bw, "best_loss": bl}

@app.get("/api/form")
def api_form(n: int = 15):
    with db() as c:
        rows = c.execute("""SELECT result, map_name, kills, deaths,
            CASE WHEN deaths>0 THEN ROUND(CAST(kills AS FLOAT)/deaths,2) ELSE kills END kd,
            start_time FROM matches WHERE result IS NOT NULL
            ORDER BY start_time DESC LIMIT ?""", (n,)).fetchall()
        return [dict(r) for r in rows]

@app.get("/api/live")
def api_live():
    return {
        "in_game":       S["match_id"] is not None,
        "round":         S["round"],
        "active_weapon": S["active_weapon"],
        "player_team":   S["player_team"],
        "kills":         S["live_kills"],
        "deaths":        S["live_deaths"],
        "health":        S["live_health"],
        "money":         S["live_money"],
        "round_dmg":     S["live_round_dmg"],
        "last_seen":     S["last_seen"],
    }

@app.get("/api/rounds/{match_id}")
def api_round_detail(match_id: str):
    with db() as c:
        rows = c.execute("SELECT * FROM rounds WHERE match_id=? ORDER BY round_number",
                         (match_id,)).fetchall()
        return [dict(r) for r in rows]

@app.get("/")
def root():
    return FileResponse("dashboard.html")
