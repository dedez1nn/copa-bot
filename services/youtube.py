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
        return json.loads(r.read())


def _search(api_key: str, event_type: str, max_results: int = 1) -> list:
    params = urllib.parse.urlencode({
        "part": "snippet",
        "channelId": _CAZETV_CHANNEL_ID,
        "type": "video",
        "eventType": event_type,
        "maxResults": max_results,
        "key": api_key,
    })
    return _get(f"{_YT_SEARCH}?{params}").get("items", [])


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
        if ts:
            result[item["id"]] = datetime.fromisoformat(ts.replace("Z", "+00:00"))
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
        return None

    try:
        # 1. Live ativa agora — CazeTV costuma começar ~2h antes do jogo
        items = _search(api_key, "live")
        if items:
            video_id = items[0]["id"]["videoId"]
            return f"https://www.youtube.com/watch?v={video_id}"

        # 2. Streams agendadas — escolhe a mais próxima do horário do jogo
        items = _search(api_key, "upcoming", max_results=5)
        if not items:
            return None

        video_ids = [i["id"]["videoId"] for i in items]
        starts = _batch_scheduled_starts(api_key, video_ids)

        if not starts:
            # Sem dados de horário, usa o primeiro resultado
            best_id = video_ids[0]
            horario = None
        elif game_ts:
            game_dt = datetime.fromtimestamp(game_ts, tz=timezone.utc)
            best_id = min(starts, key=lambda vid: abs((starts[vid] - game_dt).total_seconds()))
            horario = _fmt_horario(starts[best_id])
        else:
            best_id = min(starts, key=lambda vid: starts[vid])
            horario = _fmt_horario(starts[best_id])

        url = f"https://www.youtube.com/watch?v={best_id}"
        return f"{url} (prevista às {horario})" if horario else url

    except Exception:
        logger.warning("Falha ao buscar live da CazeTV na YouTube API")
        return None
