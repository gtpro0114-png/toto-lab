"""예측·성적 데이터를 docs/index.html 한 장으로 만든다 (GitHub Pages가 이 파일을 보여줌)."""
import json, os, re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from config import LEAGUES
from collect import load, DATA
from predict import PRED
from model import parse_dt

KST = timezone(timedelta(hours=9))
BANDS = [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01)]
ROOT = os.path.dirname(__file__)


# 베트맨 화면과 맞추기 위한 국내·일본 리그 팀 한글 표기 (영문 이름 속 핵심 단어로 찾음)
KO = {
    "kbo": {"doosan": "두산", "lg": "LG", "kiwoom": "키움", "kt": "KT", "ssg": "SSG", "nc": "NC",
            "lotte": "롯데", "samsung": "삼성", "hanwha": "한화", "kia": "KIA"},
    "kleague": {"ulsan": "울산", "pohang": "포항", "jeonbuk": "전북", "seoul": "서울", "gangwon": "강원",
                "anyang": "안양", "incheon": "인천", "jeju": "제주", "daejeon": "대전", "bucheon": "부천",
                "gimcheon": "김천", "gwangju": "광주", "suwon": "수원", "daegu": "대구", "jeonnam": "전남"},
    "npb": {"giants": "요미우리", "tigers": "한신", "carp": "히로시마", "swallows": "야쿠르트",
            "dragons": "주니치", "baystars": "요코하마", "hawks": "소프트뱅크", "fighters": "니혼햄",
            "marines": "지바롯데", "eagles": "라쿠텐", "buffaloes": "오릭스", "lions": "세이부"},
}


def ko(league, name):
    table = KO.get(league)
    if not table or not name:
        return name
    words = set(re.sub(r"[^a-z0-9 ]", " ", name.lower()).split())
    for k, v in table.items():
        if k in words:
            return v
    return name


def band_of(p):
    for lo, hi in BANDS:
        if lo <= p < hi:
            return f"{int(lo*100)}~{min(100,int(hi*100))}%"
    return "?"


def brier(rec):
    return sum((rec["p"][k] - (1 if rec["result"] == k else 0)) ** 2 for k in range(3))


def summarize(recs):
    n = len(recs)
    if not n:
        return {"n": 0}
    return {"n": n, "hit": sum(r["hit"] for r in recs) / n,
            "exp": sum(r["pick_p"] for r in recs) / n,
            "brier": sum(brier(r) for r in recs) / n}


def run(now=None, log=print):
    now = now or datetime.now(timezone.utc)
    preds = load(PRED, {})
    names = {l["key"]: l["name"] for l in LEAGUES}
    graded = [r for r in preds.values() if r["result"] is not None]

    stats = {}
    for src in ("backtest", "live"):
        rs = [r for r in graded if r["source"] == src]
        by_l = defaultdict(list)
        for r in rs:
            by_l[r["league"]].append(r)
        bands = defaultdict(list)
        band_sport = defaultdict(list)
        for r in rs:
            bands[band_of(r["pick_p"])].append(r)
            band_sport[(r["sport"], band_of(r["pick_p"]))].append(r)
        stats[src] = {
            "all": summarize(rs),
            "leagues": {k: summarize(v) for k, v in by_l.items()},
            "bands": [{"band": band_of(lo), **summarize(bands[band_of(lo)])} for lo, _ in BANDS],
            "band_sport": {f"{s}|{b}": summarize(v) for (s, b), v in band_sport.items()},
        }

    # 모델 vs 해외시장 vs 최종(혼합) 정확도, 그리고 '의견 차이' 가상 배팅 성적 (해외 최고 배당 기준)
    from config import VALUE_GAP, MARKET_WEIGHT, CRAZY_MARGIN
    mk = [r for r in graded if r.get("p_market") and r.get("p_model")]
    def br(r, key):
        return sum((r[key][k] - (1 if r["result"] == k else 0)) ** 2 for k in range(3))
    market_cmp = None
    if mk:
        n = len(mk)
        vb = [(r, k) for r in mk for k in range(3)
              if r.get("best") and r["best"][k] > 1 and r["p_model"][k] - r["p_market"][k] >= VALUE_GAP]
        fav = [(r, max(range(3), key=lambda i: r["p_market"][i])) for r in mk if r.get("best")]
        def roi(bets):
            if not bets:
                return None, 0
            pnl = sum((r["best"][k] - 1) if r["result"] == k else -1 for r, k in bets)
            return pnl / len(bets), len(bets)
        v_roi, v_n = roi(vb)
        f_roi, f_n = roi(fav)
        market_cmp = {"n": n, "model": sum(br(r, "p_model") for r in mk) / n,
                      "market": sum(br(r, "p_market") for r in mk) / n,
                      "final": sum(br(r, "p") for r in mk) / n,
                      "value_roi": v_roi, "value_n": v_n, "fav_roi": f_roi, "fav_n": f_n,
                      "gap": VALUE_GAP, "w": MARKET_WEIGHT}

    # 확률대별 과거 적중률 (과거 검증 + 실시간 합산) → 각 픽 옆에 표시
    calib = defaultdict(lambda: [0, 0])
    for r in graded:
        c = calib[f'{r["league"]}|{band_of(r["pick_p"])}']
        c[0] += r["hit"]; c[1] += 1

    upcoming = []
    for r in preds.values():
        if r["source"] == "live" and r["result"] is None and parse_dt(r["date"]) >= now - timedelta(hours=3):
            c = calib.get(f'{r["league"]}|{band_of(r["pick_p"])}', [0, 0])
            upcoming.append({**r, "home": ko(r["league"], r["home"]), "away": ko(r["league"], r["away"]),
                             "lname": names.get(r["league"], r["league"]),
                             "kst": parse_dt(r["date"]).astimezone(KST).strftime("%m/%d(%a) %H:%M")
                                    .replace("Mon", "월").replace("Tue", "화").replace("Wed", "수")
                                    .replace("Thu", "목").replace("Fri", "금").replace("Sat", "토").replace("Sun", "일"),
                             "calib": c})
    upcoming.sort(key=lambda r: -r["pick_p"])

    recent = sorted([r for r in graded if r["source"] == "live"], key=lambda r: r["date"], reverse=True)[:40]
    recent = [{**r, "home": ko(r["league"], r["home"]), "away": ko(r["league"], r["away"]),
               "lname": names.get(r["league"], r["league"]),
               "kst": parse_dt(r["date"]).astimezone(KST).strftime("%m/%d")} for r in recent]

    payload = {"updated": now.astimezone(KST).strftime("%Y-%m-%d %H:%M"), "names": names,
               "upcoming": upcoming, "recent": recent, "stats": stats,
               "market_cmp": market_cmp, "gap": VALUE_GAP, "crazy": CRAZY_MARGIN}
    with open(os.path.join(ROOT, "site_template.html"), encoding="utf-8") as f:
        html = f.read()
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("__DATA__", blob)
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    with open(os.path.join(ROOT, "docs", "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    log(f"사이트 생성: 예정 경기 {len(upcoming)}개, 채점된 예측 {len(graded)}개")


if __name__ == "__main__":
    run()
