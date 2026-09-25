"""설정: 수집할 리그와 모델 옵션.
베트맨 프로토 승부식 대상경기 공지(2026년 9월 회차들)를 기준으로 골랐습니다.

mode
  "model"  : ESPN 공개 데이터로 경기 결과를 쌓아 내 모델 확률 계산 + 해외 배당(있으면) 혼합
  "market" : 무료 결과 데이터가 없는 리그 → 해외 배당(The Odds API)의 시장 확률만 사용
"""

LEAGUES = [
    # ---- 축구 (베트맨: 90분 결과 기준) ----
    {"key": "epl",        "name": "프리미어리그", "sport": "soccer", "mode": "model", "espn": "soccer/eng.1",          "odds": "soccer_epl",                     "start": "2025-08-01"},
    {"key": "champ",      "name": "챔피언십",     "sport": "soccer", "mode": "model", "espn": "soccer/eng.2",          "odds": "soccer_efl_champ",               "start": "2025-08-01"},
    {"key": "laliga",     "name": "라리가",       "sport": "soccer", "mode": "model", "espn": "soccer/esp.1",          "odds": "soccer_spain_la_liga",           "start": "2025-08-01"},
    {"key": "seriea",     "name": "세리에A",      "sport": "soccer", "mode": "model", "espn": "soccer/ita.1",          "odds": "soccer_italy_serie_a",           "start": "2025-08-01"},
    {"key": "bundesliga", "name": "분데스리가",   "sport": "soccer", "mode": "model", "espn": "soccer/ger.1",          "odds": "soccer_germany_bundesliga",      "start": "2025-08-01"},
    {"key": "ligue1",     "name": "리그1",        "sport": "soccer", "mode": "model", "espn": "soccer/fra.1",          "odds": "soccer_france_ligue_one",        "start": "2025-08-01"},
    {"key": "eredivisie", "name": "에레디비시",   "sport": "soccer", "mode": "model", "espn": "soccer/ned.1",          "odds": "soccer_netherlands_eredivisie",  "start": "2025-08-01"},
    {"key": "mls",        "name": "MLS",          "sport": "soccer", "mode": "model", "espn": "soccer/usa.1",          "odds": "soccer_usa_mls",                 "start": "2025-02-15"},
    {"key": "j1",         "name": "J1리그",       "sport": "soccer", "mode": "model", "espn": "soccer/jpn.1",          "odds": "soccer_japan_j_league",          "start": "2025-02-10"},
    {"key": "ucl",        "name": "챔피언스리그", "sport": "soccer", "mode": "model", "espn": "soccer/uefa.champions", "odds": "soccer_uefa_champs_league",      "start": "2025-09-01"},
    {"key": "kleague",    "name": "K리그1",       "sport": "soccer", "mode": "market",                                 "odds": "soccer_korea_kleague1",          "scores": True},
    # ---- 야구 (베트맨: 연장 포함) ----
    {"key": "mlb",        "name": "MLB",          "sport": "baseball", "mode": "model", "espn": "baseball/mlb",        "odds": "baseball_mlb",                   "start": "2026-03-20"},
    {"key": "kbo",        "name": "KBO",          "sport": "baseball", "mode": "market",                               "odds": "baseball_kbo"},
    {"key": "npb",        "name": "NPB",          "sport": "baseball", "mode": "market",                               "odds": "baseball_npb"},
    # ---- 농구 (베트맨: 연장 포함) ----
    {"key": "nba",        "name": "NBA",          "sport": "basketball", "mode": "model", "espn": "basketball/nba",    "odds": "basketball_nba",                 "start": "2025-10-20"},
]
# 무료 공개 데이터·배당이 모두 없어 빠진 것: K리그2, J2, KBL, WKBL, V리그, 아시안게임 등 국제대회

LOOKAHEAD_DAYS = 3        # 앞으로 며칠치 경기를 예측할지
REFRESH_PAST_DAYS = 3     # 매번 다시 확인할 최근 며칠 (늦게 확정되는 결과 반영)
HALF_LIFE_DAYS = 120      # 오래된 경기 가중치가 절반이 되는 기간
MIN_GAMES_FOR_BACKTEST = 40
REQUEST_SLEEP = 0.25

# ---------- 해외 배당 — The Odds API 무료 키(월 500크레딧) ----------
# GitHub 저장소 Settings → Secrets → Actions 에 ODDS_API_KEY 로 넣습니다.
ODDS_REGION = "eu"          # 유럽 배당사(피나클 포함). 리그당 1크레딧
ODDS_RUN_HOURS_UTC = [22]   # 배당은 하루 한 번(한국시간 오전 7시)만 받음 — 경기 일정 조회는 무료라 매번
SCORES_EVERY_DAYS = 2       # K리그 결과는 이틀에 한 번(2크레딧)
ODDS_WINDOW_HOURS = 30     # 30시간 안에 경기가 있는 리그만 배당 조회 (크레딧 절약)
CREDIT_FLOOR = 25           # 남은 크레딧이 이 밑이면 유료 호출 중단
MARKET_WEIGHT = 0.7         # 최종 확률 = 해외시장 70% + 내 모델 30%
VALUE_GAP = 0.05
