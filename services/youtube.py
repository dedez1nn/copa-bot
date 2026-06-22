"""Busca live ativa ou agendada da CazeTV via YouTube Data API v3."""

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_CAZETV_HANDLE = "CazeTV"
_YT_CHANNELS = "https://www.googleapis.com/youtube/v3/channels"
_YT_SEARCH = "https://www.googleapis.com/youtube/v3/search"
_YT_VIDEOS = "https://www.googleapis.com/youtube/v3/videos"
_BRT = timezone(timedelta(hours=-3))

# Cache do channel ID resolvido em runtime
_resolved_channel_id: str | None = None


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read())
    return data


def _resolve_channel_id(api_key: str) -> str | None:
    """Resolve @CazeTV para o channel ID via channels API."""
    global _resolved_channel_id
    if _resolved_channel_id:
        return _resolved_channel_id
    params = urllib.parse.urlencode({
        "part": "id",
        "forHandle": _CAZETV_HANDLE,
        "key": api_key,
    })
    data = _get(f"{_YT_CHANNELS}?{params}")
    if "error" in data:
        logger.error("[YouTube] erro ao resolver channel ID: %s", data["error"])
        return None
    items = data.get("items", [])
    if not items:
        logger.warning("[YouTube] handle @%s não encontrado", _CAZETV_HANDLE)
        return None
    _resolved_channel_id = items[0]["id"]
    logger.info("[YouTube] channel ID resolvido: @%s → %s", _CAZETV_HANDLE, _resolved_channel_id)
    return _resolved_channel_id


def _search(api_key: str, channel_id: str, event_type: str, max_results: int = 1) -> list:
    params = urllib.parse.urlencode({
        "part": "snippet",
        "channelId": channel_id,
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


def _search_recent(api_key: str, channel_id: str, max_results: int = 10) -> list:
    """Busca vídeos recentes do canal (sem filtro eventType)."""
    params = urllib.parse.urlencode({
        "part": "snippet",
        "channelId": channel_id,
        "type": "video",
        "order": "date",
        "maxResults": max_results,
        "key": api_key,
    })
    data = _get(f"{_YT_SEARCH}?{params}")
    items = data.get("items", [])
    logger.info("[YouTube] search recent → %d resultado(s)", len(items))
    for it in items:
        title = it.get("snippet", {}).get("title", "?")
        vid = it.get("id", {}).get("videoId", "?")
        logger.info("[YouTube]   • %s | id=%s", title, vid)
    if "error" in data:
        logger.error("[YouTube] erro na API (recent): %s", data["error"])
    return items


def _get_live_details(api_key: str, video_ids: list[str]) -> dict[str, dict]:
    """Retorna {video_id: liveStreamingDetails} para os IDs."""
    params = urllib.parse.urlencode({
        "part": "liveStreamingDetails,snippet",
        "id": ",".join(video_ids),
        "key": api_key,
    })
    items = _get(f"{_YT_VIDEOS}?{params}").get("items", [])
    result = {}
    for item in items:
        vid = item["id"]
        details = item.get("liveStreamingDetails", {})
        title = item.get("snippet", {}).get("title", "?")
        actual_start = details.get("actualStartTime")
        actual_end = details.get("actualEndTime")
        scheduled = details.get("scheduledStartTime")
        logger.info(
            "[YouTube] liveDetails %s | title=%s | actualStart=%s | actualEnd=%s | scheduled=%s",
            vid, title, actual_start, actual_end, scheduled,
        )
        if details:
            result[vid] = details
    return result


def _fmt_horario(dt: datetime) -> str:
    return dt.astimezone(_BRT).strftime("%H:%M BRT")


def get_cazetv_live(game_ts: int | None = None) -> str | None:
    """Retorna URL da live ativa ou agendada da CazeTV.

    Fluxo:
    1. Resolve channel ID via @CazeTV handle
    2. Busca com eventType=live → retorna se encontrado
    3. Busca com eventType=upcoming → retorna mais próxima do jogo
    4. Fallback: busca vídeos recentes e checa liveStreamingDetails
    """
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        logger.warning("[YouTube] YOUTUBE_API_KEY não configurada — link da CazeTV desativado")
        return None

    logger.info("[YouTube] iniciando busca (game_ts=%s)", game_ts)

    try:
        channel_id = _resolve_channel_id(api_key)
        if not channel_id:
            return None

        # 1. Live ativa agora
        items = _search(api_key, channel_id, "live")
        if items:
            video_id = items[0]["id"]["videoId"]
            url = f"https://www.youtube.com/watch?v={video_id}"
            logger.info("[YouTube] live ativa encontrada: %s", url)
            return url

        logger.info("[YouTube] nenhuma live ativa — buscando agendadas")

        # 2. Streams agendadas
        items = _search(api_key, channel_id, "upcoming", max_results=5)
        if items:
            video_ids = [i["id"]["videoId"] for i in items]
            details = _get_live_details(api_key, video_ids)
            starts: dict[str, datetime] = {}
            for vid, d in details.items():
                ts = d.get("scheduledStartTime")
                if ts:
                    starts[vid] = datetime.fromisoformat(ts.replace("Z", "+00:00"))

            if starts and game_ts:
                game_dt = datetime.fromtimestamp(game_ts, tz=timezone.utc)
                best_id = min(starts, key=lambda v: abs((starts[v] - game_dt).total_seconds()))
                horario = _fmt_horario(starts[best_id])
                logger.info("[YouTube] stream agendada mais próxima: %s (%s)", best_id, horario)
            elif starts:
                best_id = min(starts, key=lambda v: starts[v])
                horario = _fmt_horario(starts[best_id])
                logger.info("[YouTube] próxima stream agendada: %s (%s)", best_id, horario)
            else:
                best_id = video_ids[0]
                horario = None
                logger.info("[YouTube] sem scheduledStartTime — usando primeiro resultado: %s", best_id)

            url = f"https://www.youtube.com/watch?v={best_id}"
            result = f"{url} (prevista às {horario})" if horario else url
            logger.info("[YouTube] retornando (upcoming): %s", result)
            return result

        # 3. Fallback: vídeos recentes com liveStreamingDetails
        logger.info("[YouTube] nenhuma upcoming — verificando vídeos recentes")
        recent = _search_recent(api_key, channel_id, max_results=10)
        if not recent:
            logger.info("[YouTube] nenhum vídeo recente encontrado")
            return None

        video_ids = [i["id"]["videoId"] for i in recent]
        details = _get_live_details(api_key, video_ids)

        now_utc = datetime.now(timezone.utc)
        game_dt = datetime.fromtimestamp(game_ts, tz=timezone.utc) if game_ts else now_utc

        # Prioridade: ao vivo agora (sem actualEndTime) > agendada mais próxima
        live_now = [
            vid for vid, d in details.items()
            if d.get("actualStartTime") and not d.get("actualEndTime")
        ]
        if live_now:
            best_id = live_now[0]
            url = f"https://www.youtube.com/watch?v={best_id}"
            logger.info("[YouTube] live ativa (fallback recent): %s", url)
            return url

        scheduled_vids: dict[str, datetime] = {}
        for vid, d in details.items():
            ts = d.get("scheduledStartTime")
            if ts and not d.get("actualEndTime"):
                scheduled_vids[vid] = datetime.fromisoformat(ts.replace("Z", "+00:00"))

        if scheduled_vids:
            best_id = min(scheduled_vids, key=lambda v: abs((scheduled_vids[v] - game_dt).total_seconds()))
            horario = _fmt_horario(scheduled_vids[best_id])
            url = f"https://www.youtube.com/watch?v={best_id}"
            result = f"{url} (prevista às {horario})"
            logger.info("[YouTube] retornando (fallback agendada): %s", result)
            return result

        logger.info("[YouTube] nenhuma stream encontrada")
        return None

    except Exception as e:
        logger.exception("[YouTube] falha ao buscar live da CazeTV: %s", e)
        return None
