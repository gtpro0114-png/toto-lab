"""ESPN 공개 스코어보드에서 경기 일정·결과를 받아 data/games.json 에 쌓습니다 (API 키 불필요)."""
import json, os, time, urllib.request, urllib.error
from datetime import date, datetime, timedelta, timezone
from config import LEAGUES, LOOKAHEAD_DAYS, REFRESH_PAST_DAYS, REQUEST_SLEEP

DATA = os.path.join(os.path.dirname(__file__), "data")
GAMES = os.path.join(DATA, "games.json")
FETCHED = os.path.join(DATA, "fetched_days.json")
BASE = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={d}&limit=300"


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9"}
ALT = ("://site.api.espn.com", "://site.web.api.espn.com")   # 한쪽 주소가 막히면 다른 주소로


def fetch_json(url):
    urls = [url, url.replace(*ALT)] if ALT[0] in url else [url]
    last = None
    for attempt in range(3):
        blocked = 0
        for u in urls:
            try:
                req = urllib.request.Request(u, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=25) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (401, 403, 404):
                    blocked += 1
            except Exception as e:  # 네트워크 오류는 잠시 쉬고 재시도
                last = e
        if blocked == len(urls):
            break   # 막힌 주소는 재시도해도 소용없음
        time.sleep(1.5 * (attempt + 1))
    raise last


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _probable_era(comp):
    """야구 선발투수 이름과 평균자책점. 없으면 (None, None)."""
    for pr in comp.get("probables") or []:
        ath = pr.get("athlete") or {}
        name = ath.get("displayName") or ath.get("shortName")
        era = None
        for st in pr.get("statistics") or []:
            if str(st.get("abbreviation", "")).upper() == "ERA" or str(st.get("name", "")).upper() == "ERA":
                era = _num(st.get("displayValue"))
        return name, era
    return None, None


def parse_events(js, league):
    out = []
    for ev in js.get("events") or []:
        try:
            comp = (ev.get("competitions") or [{}])[0]
            st = (comp.get("status") or ev.get("status") or {}).get("type") or {}
            teams = {c.get("homeAway"): c for c in comp.get("competitors") or []}
            h, a = teams.get("home"), teams.get("away")
            if not h or not a:
                continue
            g = {
                "id": f'{league["key"]}-{ev["id"]}',
                "league": league["key"],
                "sport": league["sport"],
                "date": ev.get("date") or comp.get("date"),
                "state": st.get("state"),               # pre / in / post
                "completed": bool(st.get("completed")),
                "status": st.get("name"),
                "home": h["team"].get("displayName"),
                "away": a["team"].get("displayName"),
                "home_id": h["team"].get("id"),
                "away_id": a["team"].get("id"),
                "hs": _num(h.get("score")) if st.get("completed") else None,
                "as": _num(a.get("score")) if st.get("completed") else None,
            }
            if league["sport"] == "baseball":
                g["home_sp"], g["home_era"] = _probable_era(h)
                g["away_sp"], g["away_era"] = _probable_era(a)
            out.append(g)
        except Exception:
            continue
    return out


def match_days(lg, start, end, log):
    """ESPN 축구 리그 달력에서 경기 있는 날짜만 뽑는다. 실패하면 None(→ 매일 조회)."""
    days = set()
    try:
        for q in ("", f"&dates={start.strftime('%Y%m%d')}"):
            js = fetch_json(BASE.format(path=lg["espn"], d="").replace("dates=&", "") + q)
            L = (js.get("leagues") or [{}])[0]
            if not L.get("calendarIsWhitelist"):
                return None
            for c in L.get("calendar") or []:
                ds = c if isinstance(c, str) else (c.get("startDate") or "")
                try:
                    d = date.fromisoformat(ds[:10])
                except ValueError:
                    continue
                if start <= d <= end:
                    days.add(d)
            time.sleep(REQUEST_SLEEP)
    except Exception as e:
        log(f"[{lg['key']}] 달력 조회 실패({e}) → 매일 조회로 전환")
        return None
    return days


def run(today=None, log=print):
    os.makedirs(DATA, exist_ok=True)
    games = {g["id"]: g for g in load(GAMES, [])}
    fetched = load(FETCHED, {})
    today = today or datetime.now(timezone.utc).date()
    n_req = 0
    for lg in LEAGUES:
        if not lg.get("espn"):
            continue   # 시장 전용 리그는 odds.py 가 처리
        done = set(fetched.get(lg["key"], []))
        start = date.fromisoformat(lg["start"])
        end = today + timedelta(days=LOOKAHEAD_DAYS)
        fresh = today - timedelta(days=REFRESH_PAST_DAYS)
        allowed = match_days(lg, start, end, log) if lg["sport"] == "soccer" else None
        days = sorted(allowed) if allowed else [start + timedelta(days=i) for i in range((end - start).days + 1)]
        ok = err = streak = 0
        for d in days:
            key = d.strftime("%Y%m%d")
            if key in done and d < fresh:
                continue
            try:
                js = fetch_json(BASE.format(path=lg["espn"], d=key))
                n_req += 1
                for g in parse_events(js, lg):
                    old = games.get(g["id"])
                    # 선발투수 정보가 경기 후 사라지는 경우 대비: 기존 값 유지
                    if old and lg["sport"] == "baseball":
                        for k in ("home_sp", "home_era", "away_sp", "away_era"):
                            if g.get(k) is None and old.get(k) is not None:
                                g[k] = old[k]
                    games[g["id"]] = g
                if d < fresh:
                    done.add(key)
                ok += 1
                streak = 0
            except Exception as e:
                err += 1
                streak += 1
                if err <= 3:
                    log(f"[{lg['key']}] {key} 수집 실패: {e}")
                if streak >= 5:   # 리그 주소가 틀렸거나 서버가 막힌 경우 — 이 리그는 이번 실행에서 건너뜀
                    log(f"[{lg['key']}] 연속 5번 실패 → 이 리그 중단 (config.py 의 espn 주소 확인)")
                    break
            time.sleep(REQUEST_SLEEP)
        fetched[lg["key"]] = sorted(done)
        log(f"[{lg['key']}] 요청 {ok}건 성공, {err}건 실패")
        save(GAMES, sorted(games.values(), key=lambda g: (g["date"] or "", g["id"])))
        save(FETCHED, fetched)
    log(f"총 경기 {len(games)}개 저장 (이번 요청 {n_req}건)")


if __name__ == "__main__":
    run()
