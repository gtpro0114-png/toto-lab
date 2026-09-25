"""종목별 확률 모델. 입력은 과거 경기 목록, 출력은 홈승/무/원정승 확률."""
import math
from collections import defaultdict
from datetime import datetime

SOCCER_PRIOR = 4      # 경기 수가 적은 팀은 리그 평균 쪽으로 당겨줌 (가상 경기 수)
NBA_PRIOR = 6
MLB_PRIOR = 12
NBA_SD = 12.5         # NBA 점수차 표준편차 (대략값)
MLB_HOME = 1.02       # 야구 홈 득점 배율


def parse_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def pois(l, n):
    out, p = [], math.exp(-l)
    out.append(p)
    for k in range(1, n + 1):
        p = p * l / k
        out.append(p)
    return out


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


class Ratings:
    """기준 시점 이전 완료 경기로 팀별 가중 득실 통계를 만든다."""

    def __init__(self, timed_games, at, half_life):
        self.team = defaultdict(lambda: [0.0, 0.0, 0.0])   # 득점합, 실점합, 가중 경기수
        self.home_for = self.away_for = self.n = 0.0
        self.n_games = 0
        # timed_games: (시각, 경기) 목록 — 완료 경기만, 시각순
        for t, g in timed_games:
            if t >= at:
                break
            w = 0.5 ** (((at - t).total_seconds() / 86400) / half_life)
            h, a = self.team[g["home"]], self.team[g["away"]]
            h[0] += w * g["hs"]; h[1] += w * g["as"]; h[2] += w
            a[0] += w * g["as"]; a[1] += w * g["hs"]; a[2] += w
            self.home_for += w * g["hs"]; self.away_for += w * g["as"]; self.n += w
            self.n_games += 1

    def league(self):
        if self.n <= 0:
            return None
        return self.home_for / self.n, self.away_for / self.n

    def per_game(self, name, avg, prior):
        gf, ga, n = self.team.get(name, [0.0, 0.0, 0.0])
        return (gf + prior * avg) / (n + prior), (ga + prior * avg) / (n + prior)


def predict(g, r):
    lg = r.league()
    if not lg:
        return None
    lh, la = lg
    avg = (lh + la) / 2
    sport = g["sport"]
    if sport == "soccer":
        hf, ha = r.per_game(g["home"], avg, SOCCER_PRIOR)
        af, aa = r.per_game(g["away"], avg, SOCCER_PRIOR)
        lam_h = (hf / avg) * (aa / avg) * lh
        lam_a = (af / avg) * (ha / avg) * la
        ph, pdraw, pa, top = _matrix(lam_h, lam_a, 10, extras=False)
        return {"p": [ph, pdraw, pa], "exp": [round(lam_h, 2), round(lam_a, 2)], "top": top}
    if sport == "basketball":
        hf, ha = r.per_game(g["home"], avg, NBA_PRIOR)
        af, aa = r.per_game(g["away"], avg, NBA_PRIOR)
        hca = max(0.0, min(5.0, lh - la))
        eh = (hf + aa) / 2 + hca / 2
        ea = (af + ha) / 2 - hca / 2
        ph = phi((eh - ea) / NBA_SD)
        return {"p": [ph, 0.0, 1 - ph], "exp": [round(eh, 1), round(ea, 1)]}
    if sport == "baseball":
        hf, ha = r.per_game(g["home"], avg, MLB_PRIOR)
        af, aa = r.per_game(g["away"], avg, MLB_PRIOR)
        lg_era = avg * 0.92   # 비자책점 제외 근사

        def pitch(ra, era):
            base = ra / avg
            if era is None:
                return base
            era_adj = 0.5 * era + 0.5 * lg_era      # 시즌 초·표본 적은 투수 과신 방지
            return 0.6 * (era_adj / lg_era) + 0.4 * base

        lam_h = hf * pitch(aa, g.get("away_era")) * MLB_HOME
        lam_a = af * pitch(ha, g.get("home_era")) / MLB_HOME
        ph, _, pa, top = _matrix(lam_h, lam_a, 25, extras=True)
        return {"p": [ph, 0.0, pa], "exp": [round(lam_h, 2), round(lam_a, 2)]}
    return None


def _matrix(lh, la, n, extras):
    a, b = pois(lh, n), pois(la, n)
    tot = sum(a) * sum(b)
    ph = pd = pa = 0.0
    cells = []
    for i in range(n + 1):
        for j in range(n + 1):
            p = a[i] * b[j] / tot
            cells.append((p, i, j))
            if i > j: ph += p
            elif i < j: pa += p
            else: pd += p
    top = [f"{i}:{j}" for p, i, j in sorted(cells, reverse=True)[:3]]
    if extras:  # 연장 포함 종목은 무승부를 승/패 비율로 나눔
        s = ph / (ph + pa)
        return ph + pd * s, 0.0, pa + pd * (1 - s), top
    return ph, pd, pa, top
