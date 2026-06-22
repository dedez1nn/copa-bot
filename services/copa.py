"""Copa do Mundo 2026 — camada de dados (fonte: FIFA API)."""

import json
import logging
import re
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".cache" / "copa2026-discord"
CACHE_TTL = 300

FIFA = "https://api.fifa.com/api/v3"
FIFA_COMPETITION = 17
FIFA_SEASON = 285023

BRT = timezone(timedelta(hours=-3))

EN_TO_PT: dict[str, str] = {
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

PT_TO_EN: dict[str, str] = {
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
    "usa": "🇺🇸", "mexico": "🇲🇽", "canada": "🇨🇦",
    "japan": "🇯🇵", "south korea": "🇰🇷", "australia": "🇦🇺",
    "morocco": "🇲🇦", "senegal": "🇸🇳", "ghana": "🇬🇭",
    "egypt": "🇪🇬", "south africa": "🇿🇦", "colombia": "🇨🇴",
    "uruguay": "🇺🇾", "ecuador": "🇪🇨", "paraguay": "🇵🇾", "panama": "🇵🇦",
    "croatia": "🇭🇷", "switzerland": "🇨🇭", "belgium": "🇧🇪",
    "denmark": "🇩🇰", "sweden": "🇸🇪", "norway": "🇳🇴", "austria": "🇦🇹",
    "czechia": "🇨🇿", "turkey": "🇹🇷", "türkiye": "🇹🇷",
    "saudi arabia": "🇸🇦", "iran": "🇮🇷", "iraq": "🇮🇶", "qatar": "🇶🇦",
    "jordan": "🇯🇴", "uzbekistan": "🇺🇿", "new zealand": "🇳🇿",
    "haiti": "🇭🇹", "scotland": "🏴󠁧󠁢󠁳󠁣󠁴󠁿",
    "tunisia": "🇹🇳", "algeria": "🇩🇿", "côte d'ivoire": "🇨🇮",
    "dr congo": "🇨🇩", "cabo verde": "🇨🇻", "curaçao": "🇨🇼",
    "bosnia & herzegovina": "🇧🇦",
}

_FIFA_STATUS = {0: "finished", 1: "notstarted", 3: "inprogress"}


def _norm(s: str) -> str:
    return unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode()


def _get_fifa(url: str, timeout: int = 8) -> dict | None:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        logger.warning("[fifa] HTTP %s ao acessar %s", e.code, url)
    except urllib.error.URLError as e:
        logger.warning("[fifa] erro de rede ao acessar %s: %s", url, e.reason)
    except TimeoutError:
        logger.warning("[fifa] timeout ao acessar %s", url)
    except json.JSONDecodeError as e:
        logger.warning("[fifa] JSON inválido em %s: %s", url, e)
    except Exception as e:
        logger.warning("[fifa] erro inesperado em %s: %s", url, e)
    return None


def _cached(key: str, url: str, ttl: int = CACHE_TTL) -> dict | None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{key}.json"
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        with path.open() as f:
            return json.load(f)
    data = _get_fifa(url)
    if data:
        with path.open("w") as f:
            json.dump(data, f)
    return data


def _load_fifa_matches() -> list[dict]:
    url = (f"{FIFA}/calendar/matches"
           f"?idCompetition={FIFA_COMPETITION}&idSeason={FIFA_SEASON}&count=200&language=pt")
    data = _cached("fifa_matches", url, ttl=3600)
    return (data or {}).get("Results", [])


def _load_fifa_live(match_id: str) -> dict | None:
    url = f"{FIFA}/live/football/{match_id}?language=pt"
    return _get_fifa(url)


def _load_fifa_timeline(m: dict) -> list[dict] | None:
    stage_id = m.get("stage_id")
    match_id = m.get("fifa_id")
    if not stage_id or not match_id:
        logger.warning("[fifa] timeline sem stage_id/fifa_id para %s x %s (stage=%s match=%s)",
                       m.get("home_pt"), m.get("away_pt"), stage_id, match_id)
        return None
    url = (f"{FIFA}/timelines/{FIFA_COMPETITION}/{FIFA_SEASON}"
           f"/{stage_id}/{match_id}?language=pt")
    data = _get_fifa(url)
    if data is None:
        return None
    events = data.get("Event")
    if events is None:
        logger.warning("[fifa] timeline sem campo 'Event' para %s x %s — chaves: %s",
                       m.get("home_pt"), m.get("away_pt"), list(data.keys()))
    return events or None


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


def _build_matches(fifa: list[dict]) -> list[dict]:
    matches = []
    for m in fifa:
        home_obj = m.get("Home") or {}
        away_obj = m.get("Away") or {}
        home_pt_raw = (home_obj.get("TeamName") or [{}])[0].get("Description", "?")
        away_pt_raw = (away_obj.get("TeamName") or [{}])[0].get("Description", "?")
        home_en = PT_TO_EN.get(_norm(home_pt_raw), home_pt_raw.lower())
        away_en = PT_TO_EN.get(_norm(away_pt_raw), away_pt_raw.lower())
        home_pt = EN_TO_PT.get(home_en, home_pt_raw)
        away_pt = EN_TO_PT.get(away_en, away_pt_raw)

        status = _FIFA_STATUS.get(m.get("MatchStatus"), "notstarted")
        h_score = home_obj.get("Score")
        a_score = away_obj.get("Score")

        try:
            ts = int(datetime.strptime(m.get("Date", ""), "%Y-%m-%dT%H:%M:%SZ")
                     .replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            ts = 0

        matches.append({
            "home_pt": home_pt, "away_pt": away_pt,
            "home_en": home_en, "away_en": away_en,
            "date_ts": ts, "status": status,
            "home_score": h_score, "away_score": a_score,
            "group": (m.get("GroupName") or [{}])[0].get("Description", ""),
            "stage": (m.get("StageName") or [{}])[0].get("Description", ""),
            "fifa_id": m.get("IdMatch"),
            "stage_id": m.get("IdStage"),
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
    if q in PT_TO_EN:
        return PT_TO_EN[q]
    for k, v in PT_TO_EN.items():
        if q in _norm(k) or _norm(k) in q:
            return v
    for en in EN_TO_PT:
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
_cache_ts: float = 0


def _refresh_cache() -> None:
    global _matches_cache, _cache_ts
    if _matches_cache is None or (time.time() - _cache_ts) > 120:
        fifa = _load_fifa_matches()
        _matches_cache = _build_matches(fifa)
        _cache_ts = time.time()


def get_jogos_rodada() -> list[dict]:
    """Retorna os jogos da 'rodada atual': janela de 7 dias centrada em hoje.

    Janela: ontem até +5 dias. Se vazia, expande para o próximo lote de jogos
    (máx. 7 dias a partir da data mais próxima no futuro) ou último lote passado.
    """
    _refresh_cache()
    now = time.time()
    matches = _matches_cache or []
    if not matches:
        return []

    today = datetime.fromtimestamp(now, tz=BRT).date()

    def _date(m):
        return datetime.fromtimestamp(m["date_ts"], tz=BRT).date()

    # Janela principal: ontem → hoje + 5
    w_start = today - timedelta(days=1)
    w_end   = today + timedelta(days=5)
    in_window = [m for m in matches if w_start <= _date(m) <= w_end]
    if in_window:
        return sorted(in_window, key=lambda m: m["date_ts"])

    # Sem jogos na janela — próximos jogos futuros
    future = [m for m in matches if _date(m) >= today]
    if future:
        pivot = _date(min(future, key=lambda m: m["date_ts"]))
        return sorted(
            [m for m in matches if pivot <= _date(m) <= pivot + timedelta(days=6)],
            key=lambda m: m["date_ts"],
        )

    # Copa encerrada — últimos jogos disputados
    past = sorted(matches, key=lambda m: -m["date_ts"])
    pivot = _date(past[0])
    return sorted(
        [m for m in matches if pivot - timedelta(days=6) <= _date(m) <= pivot],
        key=lambda m: m["date_ts"],
    )


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


def get_group_matches(letter: str) -> list[dict]:
    _refresh_cache()
    group_name = f"Group {letter.upper()}"
    return sorted(
        [m for m in (_matches_cache or [])
         if m["group"] == group_name or m["group"].startswith(f"Grupo {letter.upper()}")],
        key=lambda x: x["date_ts"],
    )


def get_scorers() -> list[dict]:
    _refresh_cache()
    finished = [m for m in (_matches_cache or []) if m["status"] == "finished" and m.get("fifa_id")]
    scorers: dict[str, dict] = {}
    for m in finished:
        data = _load_fifa_live(m["fifa_id"])
        if not data:
            continue
        home = data.get("HomeTeam") or {}
        away = data.get("AwayTeam") or {}
        pmap = _player_map_fifa(home, away)
        for side_pt, side in [(m["home_pt"], home), (m["away_pt"], away)]:
            for g in (side.get("Goals") or []):
                if g.get("Type") == 3:
                    continue
                pid = str(g.get("IdPlayer") or "")
                if not pid:
                    continue
                name = pmap.get(pid, f"ID:{pid}")
                if pid not in scorers:
                    scorers[pid] = {"name": name, "team": side_pt, "goals": 0}
                scorers[pid]["goals"] += 1
    return sorted(scorers.values(), key=lambda x: -x["goals"])
