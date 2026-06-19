"""Copa do Mundo 2026 — camada de dados e lógica de consulta."""

import json
import re
import time
import unicodedata
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from curl_cffi import requests as cffi_requests
    _CFFI = True
except ImportError:
    import subprocess
    _CFFI = False

CACHE_DIR = Path.home() / ".cache" / "copa2026-discord"
CACHE_TTL = 300

SOFASCORE = "https://api.sofascore.com/api/v1"
FIFA = "https://api.fifa.com/api/v3"
SS_TOURNAMENT = 16
SS_SEASON = 58210
FIFA_COMPETITION = 17
FIFA_SEASON = 285023

BRT = timezone(timedelta(hours=-3))

SS_TO_PT: dict[str, str] = {
    "brazil": "Brasil", "morocco": "Marrocos", "haiti": "Haiti",
    "scotland": "Escócia", "mexico": "México", "south africa": "África do Sul",
    "south korea": "Coreia do Sul", "czechia": "Tchéquia", "canada": "Canadá",
    "bosnia & herzegovina": "Bósnia e Herzegovina", "qatar": "Catar",
    "switzerland": "Suíça", "usa": "EUA", "paraguay": "Paraguai",
    "australia": "Austrália", "türkiye": "Turquia", "germany": "Alemanha",
    "curaçao": "Curação", "côte d'ivoire": "Costa do Marfim",
    "ecuador": "Equador", "netherlands": "Holanda", "sweden": "Suécia",
    "tunisia": "Tunísia", "spain": "Espanha", "cabo verde": "Cabo Verde",
    "belgium": "Bélgica", "egypt": "Egito", "saudi arabia": "Arábia Saudita",
    "uruguay": "Uruguai", "iran": "Irã", "new zealand": "Nova Zelândia",
    "france": "França", "senegal": "Senegal", "iraq": "Iraque",
    "norway": "Noruega", "argentina": "Argentina", "algeria": "Argélia",
    "austria": "Áustria", "jordan": "Jordânia", "portugal": "Portugal",
    "dr congo": "R.D. do Congo", "uzbekistan": "Uzbequistão",
    "colombia": "Colômbia", "england": "Inglaterra", "croatia": "Croácia",
    "ghana": "Gana", "panama": "Panamá", "japan": "Japão",
}

PT_TO_SS: dict[str, str] = {
    "brasil": "brazil", "marrocos": "morocco", "haiti": "haiti",
    "escocia": "scotland", "mexico": "mexico", "africa do sul": "south africa",
    "coreia do sul": "south korea", "coreia": "south korea",
    "republica da coreia": "south korea", "tchequia": "czechia",
    "republica tcheca": "czechia", "republica checa": "czechia", "tchecia": "czechia",
    "canada": "canada", "bosnia e herzegovina": "bosnia & herzegovina",
    "catar": "qatar", "suica": "switzerland", "eua": "usa",
    "estados unidos": "usa", "paraguai": "paraguay", "australia": "australia",
    "turquia": "türkiye", "alemanha": "germany", "curacao": "curaçao",
    "costa do marfim": "côte d'ivoire", "equador": "ecuador",
    "holanda": "netherlands", "paises baixos": "netherlands",
    "suecia": "sweden", "tunisia": "tunisia", "espanha": "spain",
    "cabo verde": "cabo verde", "belgica": "belgium", "egito": "egypt",
    "arabia saudita": "saudi arabia", "uruguai": "uruguay", "ira": "iran",
    "nova zelandia": "new zealand", "franca": "france", "senegal": "senegal",
    "iraque": "iraq", "noruega": "norway", "argentina": "argentina",
    "argelia": "algeria", "austria": "austria", "jordania": "jordan",
    "portugal": "portugal", "rd congo": "dr congo", "congo": "dr congo",
    "uzbequistao": "uzbekistan", "colombia": "colombia",
    "inglaterra": "england", "croacia": "croatia", "gana": "ghana",
    "panama": "panama", "japao": "japan",
}

FLAGS: dict[str, str] = {
    "brazil": "🇧🇷", "argentina": "🇦🇷", "france": "🇫🇷", "england": "🇬🇧",
    "germany": "🇩🇪", "spain": "🇪🇸", "portugal": "🇵🇹", "netherlands": "🇳🇱",
    "italy": "🇮🇹", "usa": "🇺🇸", "mexico": "🇲🇽", "canada": "🇨🇦",
    "japan": "🇯🇵", "south korea": "🇰🇷", "australia": "🇦🇺",
    "morocco": "🇲🇦", "senegal": "🇸🇳", "nigeria": "🇳🇬", "ghana": "🇬🇭",
    "egypt": "🇪🇬", "south africa": "🇿🇦", "colombia": "🇨🇴",
    "uruguay": "🇺🇾", "chile": "🇨🇱", "ecuador": "🇪🇨", "paraguay": "🇵🇾",
    "bolivia": "🇧🇴", "venezuela": "🇻🇪", "peru": "🇵🇪", "panama": "🇵🇦",
    "croatia": "🇭🇷", "switzerland": "🇨🇭", "belgium": "🇧🇪",
    "denmark": "🇩🇰", "sweden": "🇸🇪", "norway": "🇳🇴", "austria": "🇦🇹",
    "poland": "🇵🇱", "czechia": "🇨🇿", "slovakia": "🇸🇰", "hungary": "🇭🇺",
    "ukraine": "🇺🇦", "serbia": "🇷🇸", "turkey": "🇹🇷", "türkiye": "🇹🇷",
    "saudi arabia": "🇸🇦", "iran": "🇮🇷", "iraq": "🇮🇶", "qatar": "🇶🇦",
    "jordan": "🇯🇴", "uzbekistan": "🇺🇿", "new zealand": "🇳🇿",
    "haiti": "🇭🇹", "scotland": "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "wales": "🏴󠁧󠁢󠁷󠁬󠁳󠁿",
    "tunisia": "🇹🇳", "algeria": "🇩🇿", "côte d'ivoire": "🇨🇮",
    "dr congo": "🇨🇩", "cabo verde": "🇨🇻", "curaçao": "🇨🇼",
    "bosnia & herzegovina": "🇧🇦",
}

KNOCKOUT_ROUNDS = {
    6: "Oitavas de final", 5: "Oitavas de final",
    27: "Quartas de final", 28: "Semifinal",
    29: "Decisão 3º lugar", 30: "Final",
}


def _norm(s: str) -> str:
    return unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode()


def _get_sofascore(url: str, timeout: int = 8) -> dict | None:
    if _CFFI:
        try:
            r = cffi_requests.get(url, impersonate="chrome110", timeout=timeout)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
    else:
        try:
            import subprocess
            result = subprocess.run(
                ["curl", "-s", f"--max-time", str(timeout),
                 "-H", "User-Agent: Mozilla/5.0", "-H", "Accept: application/json", url],
                capture_output=True, text=True, timeout=timeout + 2,
            )
            if result.stdout.strip():
                return json.loads(result.stdout)
        except Exception:
            pass
    return None


def _get_fifa(url: str, timeout: int = 8) -> dict | None:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _cached(key: str, fetcher, url: str, ttl: int = CACHE_TTL) -> dict | None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{key}.json"
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        with path.open() as f:
            return json.load(f)
    data = fetcher(url)
    if data:
        with path.open("w") as f:
            json.dump(data, f)
    return data


def _load_fifa_matches() -> list[dict]:
    url = (f"{FIFA}/calendar/matches"
           f"?idCompetition={FIFA_COMPETITION}&idSeason={FIFA_SEASON}&count=200&language=pt")
    data = _cached("fifa_matches", _get_fifa, url, ttl=3600)
    return (data or {}).get("Results", [])


def _load_ss_events() -> list[dict]:
    events: list[dict] = []
    rounds = list(range(1, 4)) + [5, 6, 27, 28, 29, 30]
    for r in rounds:
        url = (f"{SOFASCORE}/unique-tournament/{SS_TOURNAMENT}"
               f"/season/{SS_SEASON}/events/round/{r}")
        data = _cached(f"ss_round_{r}", _get_sofascore, url, ttl=120)
        events.extend((data or {}).get("events", []))
    return events


def _load_standings() -> list[dict]:
    url = (f"{SOFASCORE}/unique-tournament/{SS_TOURNAMENT}"
           f"/season/{SS_SEASON}/standings/total")
    data = _cached("standings", _get_sofascore, url, ttl=120)
    return (data or {}).get("standings", [])


def _load_lineups(event_id: int) -> dict | None:
    return _get_sofascore(f"{SOFASCORE}/event/{event_id}/lineups")


def _load_incidents(event_id: int) -> dict | None:
    return _get_sofascore(f"{SOFASCORE}/event/{event_id}/incidents")


def _load_fifa_live(match_id: str) -> dict | None:
    url = f"{FIFA}/live/football/{match_id}?language=pt"
    return _get_fifa(url)


_FIFA_STATUS = {0: "finished", 1: "notstarted", 3: "inprogress"}


def _build_matches(fifa: list[dict], ss: list[dict]) -> list[dict]:
    ss_map: dict[tuple, dict] = {}
    for e in ss:
        h = _norm((e.get("homeTeam") or {}).get("name", ""))
        a = _norm((e.get("awayTeam") or {}).get("name", ""))
        ss_map[(h, a)] = e
        ss_map[(a, h)] = e

    matches = []
    for m in fifa:
        home_obj = m.get("Home") or {}
        away_obj = m.get("Away") or {}
        home_pt_raw = (home_obj.get("TeamName") or [{}])[0].get("Description", "?")
        away_pt_raw = (away_obj.get("TeamName") or [{}])[0].get("Description", "?")
        home_en = PT_TO_SS.get(_norm(home_pt_raw), home_pt_raw.lower())
        away_en = PT_TO_SS.get(_norm(away_pt_raw), away_pt_raw.lower())
        home_pt = SS_TO_PT.get(home_en, home_pt_raw)
        away_pt = SS_TO_PT.get(away_en, away_pt_raw)

        ss_ev = ss_map.get((_norm(home_en), _norm(away_en)))
        ss_id = ss_ev["id"] if ss_ev else None
        status = (ss_ev or {}).get("status", {}).get("type")
        h_score = ((ss_ev or {}).get("homeScore") or {}).get("current")
        a_score = ((ss_ev or {}).get("awayScore") or {}).get("current")

        if not ss_ev:
            fifa_status_code = m.get("MatchStatus")
            status = _FIFA_STATUS.get(fifa_status_code, "notstarted")
            if home_obj.get("Score") is not None:
                h_score = home_obj["Score"]
            if away_obj.get("Score") is not None:
                a_score = away_obj["Score"]

        ts = (ss_ev or {}).get("startTimestamp", 0)
        if not ts:
            try:
                ts = int(datetime.strptime(m.get("Date", ""), "%Y-%m-%dT%H:%M:%SZ")
                         .replace(tzinfo=timezone.utc).timestamp())
            except Exception:
                ts = 0

        matches.append({
            "home_pt": home_pt, "away_pt": away_pt,
            "home_en": home_en, "away_en": away_en,
            "date_ts": ts, "status": status or "notstarted",
            "home_score": h_score, "away_score": a_score,
            "group": (m.get("GroupName") or [{}])[0].get("Description", ""),
            "stage": (m.get("StageName") or [{}])[0].get("Description", ""),
            "ss_id": ss_id,
            "fifa_id": m.get("IdMatch"),
        })
    return matches


def _ts(ts: int) -> str:
    if not ts:
        return "?"
    return datetime.fromtimestamp(ts, tz=BRT).strftime("%d/%m %H:%M")


def _score(m: dict) -> str:
    s = m["status"]
    h, a = m["home_score"], m["away_score"]
    if s == "notstarted":
        return "x"
    if s == "inprogress":
        return f"{h or 0}-{a or 0} 🔴"
    if h is not None and a is not None:
        return f"{h}-{a}"
    return "?"


def _resolve(query: str) -> str:
    q = _norm(query)
    if q in PT_TO_SS:
        return PT_TO_SS[q]
    for k, v in PT_TO_SS.items():
        if q in _norm(k) or _norm(k) in q:
            return v
    for en in SS_TO_PT:
        if q in _norm(en):
            return en
    return query.lower()


def _match_team(m: dict, en: str) -> bool:
    qn = _norm(en)
    return qn in _norm(m["home_en"]) or qn in _norm(m["away_en"])


def flag(team_en: str) -> str:
    return FLAGS.get(team_en.lower(), "🏳️")


def is_brazil_match(m: dict) -> bool:
    return "brazil" in (_norm(m["home_en"]), _norm(m["away_en"]))


_matches_cache: list[dict] | None = None
_standings_cache: list[dict] | None = None
_cache_ts: float = 0


def _refresh_cache() -> None:
    global _matches_cache, _standings_cache, _cache_ts
    if _matches_cache is None or (time.time() - _cache_ts) > 120:
        fifa = _load_fifa_matches()
        ss = _load_ss_events()
        _matches_cache = _build_matches(fifa, ss)
        _standings_cache = _load_standings()
        _cache_ts = time.time()


def get_jogos_rodada() -> list[dict]:
    _refresh_cache()
    now = time.time()
    janela = 48 * 3600
    resultado = []
    for m in (_matches_cache or []):
        ts = m["date_ts"]
        status = m["status"]
        if status == "inprogress":
            resultado.append(m)
        elif status == "finished" and (now - ts) <= janela:
            resultado.append(m)
        elif status == "notstarted" and 0 <= (ts - now) <= janela:
            resultado.append(m)
    return sorted(resultado, key=lambda m: m["date_ts"])


def get_jogos_hoje() -> list[dict]:
    _refresh_cache()
    now = time.time()
    hoje = datetime.fromtimestamp(now, tz=BRT).date()
    return [
        m for m in (_matches_cache or [])
        if m["status"] == "notstarted"
        and datetime.fromtimestamp(m["date_ts"], tz=BRT).date() == hoje
    ]


def get_vs_match(t1q: str, t2q: str) -> list[dict]:
    _refresh_cache()
    t1, t2 = _resolve(t1q), _resolve(t2q)
    return [m for m in (_matches_cache or []) if _match_team(m, t1) and _match_team(m, t2)]


def get_team_matches(team_query: str) -> list[dict]:
    _refresh_cache()
    t_en = _resolve(team_query)
    return [m for m in (_matches_cache or []) if _match_team(m, t_en)]


def get_group_data(letter: str) -> tuple[dict | None, list[dict]]:
    _refresh_cache()
    group_name = f"Group {letter.upper()}"
    standings = _standings_cache or []
    sg = next(
        (s for s in standings if group_name in (s.get("tournament") or {}).get("name", "")),
        None,
    )
    gm = [
        m for m in (_matches_cache or [])
        if m["group"] == group_name or m["group"].startswith(f"Grupo {letter.upper()}")
    ]
    return sg, sorted(gm, key=lambda x: x["date_ts"])


def _player_map_fifa(home: dict, away: dict) -> dict[str, str]:
    pmap: dict[str, str] = {}
    for p in (home.get("Players") or []) + (away.get("Players") or []):
        pid = p.get("IdPlayer")
        if not pid:
            continue
        names = p.get("ShortName") or p.get("PlayerName") or []
        for locale in ("pt-BR", "en-GB"):
            for item in names:
                if item.get("Locale") == locale:
                    pmap[str(pid)] = item.get("Description", "?")
                    break
            if str(pid) in pmap:
                break
        if str(pid) not in pmap and names:
            pmap[str(pid)] = (names[0] or {}).get("Description", "?")
    return pmap


def get_scorers() -> list[dict]:
    _refresh_cache()
    finished = [m for m in (_matches_cache or []) if m["status"] == "finished" and m["ss_id"]]
    scorers: dict[int, dict] = {}
    for m in finished:
        data = _load_incidents(m["ss_id"])
        for inc in (data or {}).get("incidents", []):
            if inc.get("incidentType") != "goal" or inc.get("isOwnGoal"):
                continue
            player = inc.get("player") or {}
            pid = player.get("id")
            if not pid:
                continue
            if pid not in scorers:
                team_name = (inc.get("team") or {}).get("name", "?")
                scorers[pid] = {
                    "name": player.get("shortName") or player.get("name", "?"),
                    "team": SS_TO_PT.get(team_name.lower(), team_name),
                    "goals": 0,
                }
            scorers[pid]["goals"] += 1
    return sorted(scorers.values(), key=lambda x: -x["goals"])
