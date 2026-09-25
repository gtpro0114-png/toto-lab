"""예측 생성·채점. 앞으로 열릴 경기는 실시간 예측, 이미 끝난 경기는 '그 시점 이전 데이터만으로' 과거 검증."""
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math
from config import LEAGUES, LOOKAHEAD_DAYS, HALF_LIFE_DAYS, MIN_GAMES_FOR_BACKTEST, MARKET_WEIGHT, AUTO_CALIBRATE
from collect import load, save, DATA, GAMES
from model import Ratings, predict, parse_dt

PRED = os.path.join(DATA, "predictions.json")
LABELS = {"soccer": ["홈 승", "무", "원정 승"], "basketball": ["홈 승", "", "원정 승"], "baseball": ["홈 승", "", "원정 승"]}


def result_index(g):
    if g["hs"] > g["as"]:
        return 0
    if g["hs"] < g["as"]:
        return 2
    return 1 if g["sport"] == "soccer" else None


def fit_scale(recs):
    """승·패 두 갈래 종목: 모델 확률을 더 과감하게(>1) 또는 덜 과감하게(<1) 조정할 배율을 과거 검증으로 찾는다."""
    pts = [(r["p_model"][0] if r.get("p_model") else r["p"][0], 1 if r["result"] == 0 else 0) for r in recs]
    pts = [(min(0.99, max(0.01, p)), y) for p, y in pts]
    if len(pts) < 200:
        return 1.0
    best, best_b = 1.0, None
    for i in range(30, 251, 5):
        a = i / 100
        b = 0.0
        for p, y in pts:
            q = 1 / (1 + math.exp(-a * math.log(p / (1 - p))))
            b += (q - y) ** 2
        if best_b is None or b < best_b:
            best, best_b = a, b
    return best


def apply_scale(pr, a):
    if not pr or a == 1.0:
        return pr
    p = min(0.99, max(0.01, pr["p"][0]))
    q = 1 / (1 + math.exp(-a * math.log(p / (1 - p))))
    return {**pr, "p": [q, 0.0, 1 - q]}


def make_record(g, pr, source, odds=None):
    pm = [round(x, 4) for x in pr["p"]] if pr else None
    if odds and pm:  # 최종 확률 = 해외시장과 내 모델을 섞은 값
        p = [round(MARKET_WEIGHT * odds["market"][i] + (1 - MARKET_WEIGHT) * pm[i], 4) for i in range(3)]
    elif odds:       # 시장 전용 리그
        p = list(odds["market"])
    else:
        p = pm
    pr = pr or {}
    idx = max(range(3), key=lambda i: p[i])
    rec = {"id": g["id"], "league": g["league"], "sport": g["sport"], "date": g["date"],
           "home": g["home"], "away": g["away"], "p": p, "pick": idx,
           "pick_label": LABELS[g["sport"]][idx], "pick_p": p[idx], "exp": pr.get("exp"),
           "top": pr.get("top"), "source": source, "result": None, "hit": None,
           "p_model": pm, "p_market": odds["market"] if odds else None,
           "pinnacle": odds.get("pinnacle") if odds else None,
           "best": odds["best"] if odds else None, "n_books": odds["n_books"] if odds else 0}
    if g["sport"] == "baseball":
        rec["sp"] = [g.get("home_sp"), g.get("away_sp")]
        rec["era"] = [g.get("home_era"), g.get("away_era")]
    return rec


def run(now=None, log=print):
    now = now or datetime.now(timezone.utc)
    games = load(GAMES, [])
    preds = load(PRED, {})
    odds = load(os.path.join(DATA, "odds.json"), {})
    by_league = defaultdict(list)
    for g in games:
        if g.get("date"):
            by_league[g["league"]].append(g)

    new_live = new_bt = graded = 0
    for lg in LEAGUES:
        lst = by_league.get(lg["key"], [])
        done = sorted(((parse_dt(g["date"]), g) for g in lst
                       if g.get("completed") and g.get("hs") is not None and g.get("as") is not None),
                      key=lambda x: x[0])

        # 1) 과거 검증: 예측 기록이 없는 완료 경기 → 그날 이전 경기만으로 계산 (날짜별로 묶어 속도 확보)
        cache = {}
        for i, (t, g) in enumerate(done if lg["mode"] == "model" else []):
            if g["id"] in preds:
                continue
            day = t.date()
            if day not in cache:
                cutoff = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
                r = Ratings(done, cutoff, HALF_LIFE_DAYS)
                cache[day] = r if r.n_games >= MIN_GAMES_FOR_BACKTEST else None
            r = cache[day]
            if r is None:
                continue
            pr = predict(g, r)
            if pr:
                preds[g["id"]] = make_record(g, pr, "backtest")
                new_bt += 1

        # 2) 실시간 예측: 아직 시작 안 한 경기 (시작 전까지는 매번 최신 데이터로 갱신)
        scale = 1.0
        if AUTO_CALIBRATE and lg["sport"] in ("baseball", "basketball") and lg["mode"] == "model":
            scale = fit_scale([r for r in preds.values() if r["league"] == lg["key"]
                               and r["source"] == "backtest" and r["result"] is not None])
            if scale != 1.0:
                log(f"[{lg['key']}] 확률 자동 보정 배율 {scale:.2f} (1보다 크면 더 과감하게, 작으면 덜 과감하게)")
        r_now = Ratings(done, now, HALF_LIFE_DAYS)
        horizon = now + timedelta(days=LOOKAHEAD_DAYS)
        for g in lst:
            t = parse_dt(g["date"])
            if g.get("state") == "pre" and now <= t <= horizon and not g.get("completed"):
                if "POSTPONED" in str(g.get("status", "")).upper():
                    continue
                old = preds.get(g["id"])
                if old and old["source"] != "live":
                    continue
                pr = apply_scale(predict(g, r_now), scale) if lg["mode"] == "model" else None
                o = odds.get(g["id"])
                if pr or o:
                    preds[g["id"]] = make_record(g, pr, "live", o)
                    new_live += 1

    # 3) 채점
    gmap = {g["id"]: g for g in games}
    for pid, rec in preds.items():
        g = gmap.get(pid)
        if rec["result"] is None and g and g.get("completed") and g.get("hs") is not None:
            ri = result_index(g)
            if ri is None:
                continue
            rec["result"] = ri
            rec["hit"] = int(ri == rec["pick"])
            rec["score"] = f'{int(g["hs"])}:{int(g["as"])}'
            graded += 1
    # 오래된 미채점 실시간 예측(취소·연기 경기) 정리
    for pid in [k for k, v in preds.items() if v["result"] is None and v["source"] == "live"
                and parse_dt(v["date"]) < now - timedelta(days=5)]:
        del preds[pid]

    save(PRED, preds)
    log(f"실시간 예측 {new_live}건, 과거 검증 추가 {new_bt}건, 채점 {graded}건, 전체 {len(preds)}건")
    return preds


if __name__ == "__main__":
    run()
