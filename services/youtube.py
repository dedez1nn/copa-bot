"""Busca live ativa ou agendada da CazeTV via YouTube Data API v3."""

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_CAZETV_CHANNEL_ID = "UCZiYbVptd3PVPf4f6eR6UaQ"
_YT_SEARCH = "https://www.googleapis.com/youtube/v3/search"
_YT_VIDEOS = "https://www.googleapis.com/youtube/v3/videos"
_BRT = timezone(timedelta(hours=-3))


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read())
    return data


def _search(api_key: str, event_type: str, max_results: int = 1) -> list:
    params = urllib.parse.urlencode({
        "part": "snippet",
        "channelId": _CAZETV_CHANNEL_ID,
        "type": "video",
        "eventType": event_type,
        "maxResults": max_results,
        "key": api_key,
    })
    data = _get(f"{_YT_SEARCH}?{params}")
    items = data.get("items", [])
    logger.info("[YouTube] search eventType=%s → %d resultado(s)", event_type, len(items))
    for it in items:
        title = it.get("snippet", {}).get("title", "?")
        vid = it.get("id", {}).get("videoId", "?")
        logger.info("[YouTube]   • %s | id=%s", title, vid)
    if "error" in data:
        logger.error("[YouTube] erro na API: %s", data["error"])
    return items


def _batch_scheduled_starts(api_key: str, video_ids: list[str]) -> dict[str, datetime]:
    """Retorna {video_id: scheduled_start_datetime} para uma lista de IDs."""
    params = urllib.parse.urlencode({
        "part": "liveStreamingDetails",
        "id": ",".join(video_ids),
        "key": api_key,
    })
    items = _get(f"{_YT_VIDEOS}?{params}").get("items", [])
    result = {}
    for item in items:
        ts = item.get("liveStreamingDetails", {}).get("scheduledStartTime")
        vid = item["id"]
        if ts:
            result[vid] = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            logger.info("[YouTube] scheduledStart %s → %s", vid, ts)
        else:
            logger.info("[YouTube] scheduledStart %s → não disponível", vid)
    return result


def _fmt_horario(dt: datetime) -> str:
    return dt.astimezone(_BRT).strftime("%H:%M BRT")


def get_cazetv_live(game_ts: int | None = None) -> str | None:
    """Retorna URL da live ativa ou agendada da CazeTV.

    Se game_ts for fornecido, o fallback escolhe a stream agendada
    mais próxima do horário do jogo (a CazeTV começa ~2h antes).
    Retorna None sem API key ou sem nada encontrado.
    """
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        logger.warning("[YouTube] YOUTUBE_API_KEY não configurada — link da CazeTV desativado")
        return None

    logger.info("[YouTube] iniciando busca (game_ts=%s)", game_ts)

    try:
        # 1. Live ativa agora
        items = _search(api_key, "live")
        if items:
            video_id = items[0]["id"]["videoId"]
            url = f"https://www.youtube.com/watch?v={video_id}"
            logger.info("[YouTube] live ativa encontrada: %s", url)
            return url

        logger.info("[YouTube] nenhuma live ativa — buscando agendadas")

        # 2. Streams agendadas
        items = _search(api_key, "upcoming", max_results=5)
        if not items:
            logger.info("[YouTube] nenhuma stream agendada encontrada")
            return None

        video_ids = [i["id"]["videoId"] for i in items]
        starts = _batch_scheduled_starts(api_key, video_ids)

        if not starts:
            best_id = video_ids[0]
            horario = None
            logger.info("[YouTube] sem scheduledStartTime — usando primeiro resultado: %s", best_id)
        elif game_ts:
            game_dt = datetime.fromtimestamp(game_ts, tz=timezone.utc)
            best_id = min(starts, key=lambda vid: abs((starts[vid] - game_dt).total_seconds()))
            horario = _fmt_horario(starts[best_id])
            logger.info("[YouTube] stream mais próxima do jogo: %s (%s)", best_id, horario)
        else:
            best_id = min(starts, key=lambda vid: starts[vid])
            horario = _fmt_horario(starts[best_id])
            logger.info("[YouTube] próxima stream agendada: %s (%s)", best_id, horario)

        url = f"https://www.youtube.com/watch?v={best_id}"
        result = f"{url} (prevista às {horario})" if horario else url
        logger.info("[YouTube] retornando: %s", result)
        return result

    except Exception as e:
        logger.exception("[YouTube] falha ao buscar live da CazeTV: %s", e)
        return None
