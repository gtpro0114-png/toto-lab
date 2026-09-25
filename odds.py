"""The Odds API 연동.
1) 모든 리그: 해외 배당 → 공제를 뺀 '시장 확률'로 바꿔 data/odds.json 에 저장
2) 시장 전용 리그(KBO·NPB·K리그): 경기 일정(무료 호출)으로 경기 목록을 만들고,
   결과를 주는 리그(K리그)는 결과까지 받아 채점에 쓴다.
"""
import os, re, json, statistics, unicodedata, urllib.request
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from config import (LEAGUES, ODDS_REGION, ODDS_RUN_HOURS_UTC, LOOKAHEAD_DAYS,
                    SCORES_EVERY_DAYS, CREDIT_FLOOR, ODDS_WINDOW_HOURS, MARKET_ODDS_HOURS_UTC)
from collect import load, save, DATA, GAMES
from model import parse_dt

ODDS = os.path.join(DATA, "odds.json")
STATE = os.path.join(DATA, "odds_state.json")
API = "https://api.the-odds-api.com/v4"
STOP = {"fc", "cf", "afc", "sc", "ac", "club", "the", "de", "cd", "ud", "fk", "sv", "calcio", "ssc", "rc", "sd", "us", "cfc"}


class Budget:
    def __init__(self):
        self.left = None

    def get(self, url, paid):
        if paid and self.left is not None and self.left < CREDIT_FLOOR:
            raise RuntimeError(f"남은 크레딧 {self.left} — 한도 보호로 중단")
        req = urllib.request.Request(url, headers={"User-Agent": "toto-lab"})
        with urllib.request.urlopen(req, timeout=30) as r:
            rem = r.headers.get("x-requests-remaining")
            if rem is not None:
                try:
                    self.left = int(float(rem))
                except ValueError:
                    pass
            return json.loads(r.read().decode("utf-8"))


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(t for t in s.split() if t not in STOP)


def sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / len(ta | tb)
    return max(SequenceMatcher(None, a, b).ratio(), jac, 0.9 if (a in b or b in a) else 0)


def devig(ev, soccer):
    """배당사별 배당 → 공제 뺀 확률. 여러 배당사의 중앙값을 시장 확률로."""
    per_book, best, pinnacle = [], [0.0, 0.0, 0.0], None
    for bk in ev.get("bookmakers", []):
        for m in bk.get("markets", []):
            if m.get("key") != "h2h":
                continue
            price = [None, None, None]
            for o in m.get("outcomes", []):
                if o.get("name") == ev["home_team"]: price[0] = o.get("price")
                elif o.get("name") == ev["away_team"]: price[2] = o.get("price")
                elif str(o.get("name", "")).lower() == "draw": price[1] = o.get("price")
            idx = [0, 1, 2] if soccer else [0, 2]
            if any(not price[i] or price[i] <= 1 for i in idx):
                continue
            inv = [1 / price[i] if i in idx else 0.0 for i in range(3)]
            s = sum(inv)
            fair = [x / s for x in inv]
            per_book.append(fair)
            for i in idx:
                best[i] = max(best[i], price[i])
            if bk.get("key") == "pinnacle":
                pinnacle = [round(x, 4) for x in fair]
    if not per_book:
        return None
    med = [statistics.median(b[i] for b in per_book) for i in range(3)]
    s = sum(med)
    return {"market": [round(x / s, 4) for x in med], "pinnacle": pinnacle,
            "best": [round(x, 2) for x in best], "n_books": len(per_book)}


def market_game(lg, ev):
    return {"id": f'{lg["key"]}-{ev["id"]}', "league": lg["key"], "sport": lg["sport"],
            "date": ev["commence_time"], "state": "pre", "completed": False, "status": "SCHEDULED",
            "home": ev["home_team"], "away": ev["away_team"], "hs": None, "as": None, "source": "odds"}


def run(now=None, log=print):
    key = os.environ.get("ODDS_API_KEY")
    now = now or datetime.now(timezone.utc)
    if not key:
        log("해외 배당: ODDS_API_KEY 없음 → 건너뜀 (KBO·NPB·K리그는 키가 있어야 표시됩니다)")
        return
    odds_time = now.hour in ODDS_RUN_HOURS_UTC or bool(os.environ.get("FORCE_ODDS"))
    games = {g["id"]: g for g in load(GAMES, [])}
    store = load(ODDS, {})
    state = load(STATE, {})
    horizon = now + timedelta(days=LOOKAHEAD_DAYS)
    b = Budget()
    try:
        active = {s["key"] for s in b.get(f"{API}/sports?apiKey={key}", paid=False) if s.get("active")}
    except Exception as e:
        log(f"해외 배당: 종목 목록 실패 {e}")
        return

    # 시장 전용 리그 먼저 (베트맨 국내 경기 우선)
    order = sorted(LEAGUES, key=lambda l: 0 if l["mode"] == "market" else 1)
    for lg in order:
        okey = lg.get("odds")
        if not okey:
            continue
        if okey not in active:
            log(f"[{lg['key']}] 해외 배당: 지금은 시즌 중이 아님(비활성) → 건너뜀")
            continue
        try:
            # (1) 시장 전용 리그: 일정 조회(무료)로 경기 목록 생성
            if lg["mode"] == "market":
                evs = b.get(f"{API}/sports/{okey}/events?apiKey={key}", paid=False)
                log(f"[{lg['key']}] 일정 {len(evs)}경기 확인")
                for ev in evs:
                    gid = f'{lg["key"]}-{ev["id"]}'
                    if gid not in games and parse_dt(ev["commence_time"]) <= horizon:
                        games[gid] = market_game(lg, ev)
                # 결과 (K리그만 제공)
                last = state.get(f"scores-{lg['key']}")
                due = not last or (now - parse_dt(last)).days >= SCORES_EVERY_DAYS
                if lg.get("scores") and due and odds_time:
                    res = b.get(f"{API}/sports/{okey}/scores?apiKey={key}&daysFrom=3", paid=True)
                    n = 0
                    for ev in res:
                        gid = f'{lg["key"]}-{ev["id"]}'
                        if not ev.get("completed") or not ev.get("scores"):
                            continue
                        sc = {s["name"]: s.get("score") for s in ev["scores"]}
                        try:
                            hs, as_ = float(sc[ev["home_team"]]), float(sc[ev["away_team"]])
                        except (KeyError, TypeError, ValueError):
                            continue
                        g = games.get(gid) or market_game(lg, ev)
                        g.update({"state": "post", "completed": True, "status": "FINAL", "hs": hs, "as": as_})
                        games[gid] = g
                        n += 1
                    state[f"scores-{lg['key']}"] = now.isoformat(timespec="minutes")
                    log(f"[{lg['key']}] 결과 {n}경기 반영")

            # (2) 배당: 앞으로 열릴 경기가 있을 때만 (유료 1크레딧)
            this_time = odds_time or (lg["mode"] == "market" and now.hour in MARKET_ODDS_HOURS_UTC)
            if not this_time:
                continue
            window = now + timedelta(hours=ODDS_WINDOW_HOURS)
            todo = [g for g in games.values() if g["league"] == lg["key"] and g.get("state") == "pre"
                    and g.get("date") and now <= parse_dt(g["date"]) <= window]
            if not todo:
                continue
            evs = b.get(f"{API}/sports/{okey}/odds?apiKey={key}&regions={ODDS_REGION}&markets=h2h&oddsFormat=decimal", paid=True)
            matched = 0
            for ev in evs:
                target = None
                if lg["mode"] == "market":
                    target = games.get(f'{lg["key"]}-{ev["id"]}')
                else:
                    t = parse_dt(ev["commence_time"])
                    best_s = 0.0
                    for g in todo:
                        if abs((parse_dt(g["date"]) - t).total_seconds()) > 6 * 3600:
                            continue
                        s = min(sim(g["home"], ev["home_team"]), sim(g["away"], ev["away_team"]))
                        if s > best_s:
                            target, best_s = g, s
                    if best_s < 0.5:
                        target = None
                if target:
                    d = devig(ev, lg["sport"] == "soccer")
                    if d:
                        d["ts"] = now.isoformat(timespec="minutes")
                        store[target["id"]] = d
                        matched += 1
            log(f"[{lg['key']}] 해외 배당 {len(evs)}경기 중 {matched}경기 연결 (남은 크레딧 {b.left})")
        except Exception as e:
            log(f"[{lg['key']}] 해외 배당 처리 실패: {e}")
            if "한도 보호" in str(e):
                break

    save(GAMES, sorted(games.values(), key=lambda g: (g["date"] or "", g["id"])))
    save(ODDS, store)
    save(STATE, state)


if __name__ == "__main__":
    run()
