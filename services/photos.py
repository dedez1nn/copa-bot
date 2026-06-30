"""Fotos de jogadores via API-Football (api-sports).

Mantém a lógica de dados na FIFA API; aqui só resolvemos a foto de cada jogador:
  1. busca o id do jogador por sobrenome em /players/profiles (plano Free permite);
  2. baixa a foto do CDN público media.api-sports.io (não consome cota da API).

Cota do plano Free: 100 req/dia — por isso o mapa nome→id é cacheado em disco.
"""

import json
import logging
import os
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

from services.copa import CACHE_DIR, EN_TO_PT, PT_TO_EN, _norm

logger = logging.getLogger(__name__)

_API_BASE = "https://v3.football.api-sports.io"
_PHOTO_URL = "https://media.api-sports.io/football/players/{id}.png"

_ID_CACHE = CACHE_DIR / "apifootball_ids_v2.json"   # v2: nova heurística de matching
_PHOTO_CACHE = CACHE_DIR / "player_photos"

# Sufixos de nome que não ajudam a identificar (e poluem a busca por sobrenome).
_SUFFIXES = {"junior", "jr", "filho", "neto", "sobrinho", "i", "ii", "iii"}

# Nacionalidade (en da API-Football) → nome interno (en) usado no projeto.
# Só os casos em que Title(en) != nacionalidade da API.
_NAT_FIX = {
    "usa": "usa", "united states": "usa", "south korea": "south korea",
    "korea republic": "south korea", "ivory coast": "côte d'ivoire",
    "czech republic": "czechia", "dr congo": "dr congo",
    "congo dr": "dr congo", "cape verde": "cabo verde",
    "bosnia and herzegovina": "bosnia & herzegovina", "turkey": "türkiye",
}


def _key() -> str | None:
    return os.getenv("APIFOOTBALL_KEY")


def _load_id_cache() -> dict:
    if _ID_CACHE.exists():
        try:
            return json.loads(_ID_CACHE.read_text())
        except Exception:
            return {}
    return {}


def _save_id_cache(cache: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _ID_CACHE.write_text(json.dumps(cache))
    except Exception:
        logger.warning("[photos] falha ao salvar cache de ids")


def _nat_to_en(nat: str | None) -> str:
    if not nat:
        return ""
    n = _norm(nat)
    return _NAT_FIX.get(n, n)


def _name_tokens(name: str) -> list[str]:
    return [t for t in _norm(name).replace(".", " ").split() if t.isalpha()]


def _score(fifa_tokens: list[str], cand_name: str, cand_nat: str | None,
           team_en: str) -> int:
    """Pontua um candidato: nacionalidade vale muito; cada token de nome em comum, 1."""
    cand_tokens = set(_name_tokens(cand_name))
    overlap = sum(1 for t in fifa_tokens if t in cand_tokens)
    nat = 10 if _nat_to_en(cand_nat) == _norm(team_en) else 0
    return nat + overlap


def _api_get(path: str) -> dict | None:
    key = _key()
    if not key:
        return None
    try:
        req = urllib.request.Request(_API_BASE + path, headers={"x-apisports-key": key})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        logger.warning("[photos] erro na API-Football %s: %s", path, e)
        return None


def _resolve_id(name: str, team_en: str, cache: dict) -> int | None:
    """Resolve o id do jogador (com cache). team_en ajuda a desambiguar.

    Só guarda no cache resultados positivos: falhas transitórias (sem chave,
    cota estourada, erro de rede) NÃO são cacheadas, para não "envenenar" o
    cache com None e impedir novas tentativas.
    """
    ck = f"{_norm(name)}|{team_en}"
    if cache.get(ck):
        return cache[ck]

    toks = _name_tokens(name)
    if not toks:
        return None
    term = toks[-1]  # busca pelo sobrenome (cobre o jogador certo no topo)
    # tokens úteis para pontuar (sem sufixos como "junior"); se sobrar nada, usa todos
    fifa_tokens = [t for t in toks if t not in _SUFFIXES] or toks

    data = _api_get("/players/profiles?search=" + urllib.parse.quote(term))
    if not data or data.get("errors"):
        logger.warning("[photos] busca falhou para %r (sem chave/cota/erro)", name)
        return None  # transitório — não cacheia, tenta de novo na próxima

    results = data.get("response") or []
    chosen, best = None, -1
    for x in results:
        p = x.get("player") or {}
        cand = f"{p.get('firstname') or ''} {p.get('lastname') or ''}"
        sc = _score(fifa_tokens, cand, p.get("nationality"), team_en)
        if sc > best:
            best, chosen = sc, p.get("id")

    if chosen:
        cache[ck] = chosen
        _save_id_cache(cache)
    return chosen


def _photo_bytes(pid: int) -> bytes | None:
    _PHOTO_CACHE.mkdir(parents=True, exist_ok=True)
    path = _PHOTO_CACHE / f"{pid}.png"
    if path.exists() and path.stat().st_size > 0:
        return path.read_bytes()
    try:
        req = urllib.request.Request(_PHOTO_URL.format(id=pid),
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read()
        if data:
            path.write_bytes(data)
            return data
    except Exception as e:
        logger.warning("[photos] falha ao baixar foto %s: %s", pid, e)
    return None


def player_photo(name: str, team_pt: str) -> bytes | None:
    """Retorna os bytes PNG da foto do jogador, ou None se indisponível."""
    if not _key():
        return None
    team_en = PT_TO_EN.get(_norm(team_pt), _norm(team_pt))
    cache = _load_id_cache()
    pid = _resolve_id(name, team_en, cache)
    if not pid:
        return None
    return _photo_bytes(pid)
