"""Busca live ativa ou agendada da CazeTV via YouTube Data API v3."""

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_CAZETV_CHANNEL_ID = "UCZiYbVptd3PVPf4f6eR6UaQ"
_YT_SEARCH  = "https://www.googleapis.com/youtube/v3/search"
_YT_VIDEOS  = "https://www.googleapis.com/youtube/v3/videos"
_BRT = timezone(timedelta(hours=-3))


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def _search(api_key: str, event_type: str) -> list:
    params = urllib.parse.urlencode({
        "part": "snippet",
        "channelId": _CAZETV_CHANNEL_ID,
        "type": "video",
        "eventType": event_type,
        "maxResults": 1,
        "key": api_key,
    })
    return _get(f"{_YT_SEARCH}?{params}").get("items", [])


def _scheduled_start(api_key: str, video_id: str) -> str | None:
    """Retorna horário agendado no formato 'HH:MM BRT', ou None se não disponível."""
    params = urllib.parse.urlencode({
        "part": "liveStreamingDetails",
        "id": video_id,
        "key": api_key,
    })
    items = _get(f"{_YT_VIDEOS}?{params}").get("items", [])
    if not items:
        return None
    ts = items[0].get("liveStreamingDetails", {}).get("scheduledStartTime")
    if not ts:
        return None
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(_BRT)
    return dt.strftime("%H:%M BRT")


def get_cazetv_live() -> str | None:
    """Retorna URL da live ativa ou agendada da CazeTV.
    - Ao vivo: URL simples
    - Agendada: 'URL (prevista às HH:MM BRT)'
    - Sem API key ou nada encontrado: None
    """
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        return None

    try:
        # 1. Live ativa agora
        items = _search(api_key, "live")
        if items:
            video_id = items[0]["id"]["videoId"]
            return f"https://www.youtube.com/watch?v={video_id}"

        # 2. Próxima transmissão agendada
        items = _search(api_key, "upcoming")
        if items:
            video_id = items[0]["id"]["videoId"]
            url = f"https://www.youtube.com/watch?v={video_id}"
            horario = _scheduled_start(api_key, video_id)
            return f"{url} (prevista às {horario})" if horario else url

    except Exception:
        logger.warning("Falha ao buscar live da CazeTV na YouTube API")

    return None
