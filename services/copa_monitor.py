"""Monitoramento ao vivo e de escalações — Copa 2026 Discord (fonte: FIFA API)."""

import asyncio
import logging
import time
from datetime import datetime

import discord

from services.copa import (
    BRT, EN_TO_PT, FLAGS,
    _load_fifa_live, _load_fifa_timeline, _player_map_fifa,
    get_jogos_rodada, is_brazil_match, flag, team_color,
)
from services import youtube

logger = logging.getLogger(__name__)

_watch: dict[str, dict] = {}
_first_tick = True

LINEUP_INTERVAL_BRAZIL = 10
LINEUP_INTERVAL_OTHER = 60
LINEUP_WINDOW_SECS = 3600
VAR_WINDOW_SECS = 600

_POS_FIFA = {0: "GK", 1: "DEF", 2: "MEI", 3: "ATA"}

_STAT_LABELS: dict[int, str] = {
    1: "Posse de bola (%)",
    2: "Chutes",
    3: "Chutes no gol",
    4: "Escanteios",
    5: "Faltas",
    6: "Impedimentos",
    7: "Cartões amarelos",
    8: "Cartões vermelhos",
    9: "Defesas",
    10: "Passes",
    11: "Precisão de passes (%)",
}


def _state(key: str) -> dict:
    if key not in _watch:
        _watch[key] = {
            "announced_60": False,
            "announced_30": False,
            "kicked_off": False,
            "primed": False,
            "ht_sent": False,
            "2ht_sent": False,
            "final_sent": False,
            "lineup_sent": False,
            "last_lineup_check": 0.0,
            "last_period": None,
            "seen_goals": set(),
            "seen_cards": set(),
            "seen_red_players": set(),
            "goal_count": {"home": 0, "away": 0},
            "last_status": None,
            "suspended": False,
            "pending_goals": {},
            "pending_cards": {},
            "seen_timeline_events": set(),
            "home_team_id": None,
            "away_team_id": None,
            "pmap": {},
            "kickoff_notified": False,
            "h_score": 0,
            "a_score": 0,
            "shootout_announced": False,
            "shootout_home": 0,
            "shootout_away": 0,
        }
    return _watch[key]


# ── Formatadores de seção ─────────────────────────────────────────────────────

def _goals_text(goals_raw: list[tuple[str, dict]], pmap: dict, period: int | None = None) -> str:
    # Period 11 = pênaltis convertidos na disputa; não entram na lista de gols do jogo.
    filtered = [
        (t, g) for t, g in goals_raw
        if g.get("Period") != 11 and (period is None or g.get("Period") == period)
    ]
    if not filtered:
        return ""
    lines = []
    for team_pt, g in sorted(filtered, key=lambda x: (x[1].get("Period", 0), x[1].get("Minute", 0))):
        player = pmap.get(str(g.get("IdPlayer") or ""), "?")
        minute = g.get("Minute", "?")
        gtype = g.get("Type", 2)
        extra = " *(contra)*" if gtype == 3 else (" *(pen.)*" if gtype == 4 else "")
        lines.append(f"⚽ **{player}{extra}** {minute}' — *{team_pt}*")
    return "\n".join(lines)


def _reds_text(cards_raw: list[tuple[str, dict]], pmap: dict) -> str:
    reds = [(t, b) for t, b in cards_raw if b.get("Card") == 2]
    if not reds:
        return ""
    lines = []
    for team_pt, b in sorted(reds, key=lambda x: x[1].get("Minute", 0)):
        player = pmap.get(str(b.get("IdPlayer") or ""), "?")
        minute = b.get("Minute", "?")
        lines.append(f"🟥 **{player}** {minute}' — *{team_pt}*")
    return "\n".join(lines)


def _subs_text(home_data: dict, away_data: dict, pmap: dict, home_pt: str, away_pt: str) -> str:
    subs = []
    for team_pt, side in [(home_pt, home_data), (away_pt, away_data)]:
        for s in (side.get("Substitutions") or []):
            subs.append((team_pt, s))
    if not subs:
        return ""
    lines = []
    for team_pt, s in sorted(subs, key=lambda x: x[1].get("Minute", 0)):
        p_on = pmap.get(str(s.get("IdPlayerOn") or s.get("IdPlayerSubstitute") or ""), "?")
        p_off = pmap.get(str(s.get("IdPlayerOff") or s.get("IdPlayer") or ""), "?")
        minute = s.get("Minute", "?")
        lines.append(f"↕️ **{p_on}** ← {p_off}  {minute}' — *{team_pt}*")
    return "\n".join(lines)


def _stats_text(stats: list, home_pt: str, away_pt: str) -> str:
    if not stats:
        return ""
    lines = [f"`{'Estatística':<24} {home_pt[:10]:>10}  {away_pt[:10]:<10}`"]
    for s in stats:
        label = _STAT_LABELS.get(s.get("Type", 0)) or s.get("Name") or s.get("Type")
        if not label:
            continue
        hv = s.get("HomeValue", "—")
        av = s.get("AwayValue", "—")
        lines.append(f"`{str(label):<24} {str(hv):>10}  {str(av):<10}`")
    return "\n".join(lines) if len(lines) > 1 else ""


def _live_field(live_url: str) -> tuple[str, str]:
    """Retorna (name, value) para o campo de transmissão."""
    if " (prevista" in live_url:
        url_part, rest = live_url.split(" (prevista às ", 1)
        time_str = rest.rstrip(")")
        return "📺 Transmissão", f"[CazeTV — prevista às {time_str}]({url_part})"
    return "📺 Transmissão", f"[CazeTV — ao vivo]({live_url})"


# ── Embeds públicos ───────────────────────────────────────────────────────────

def build_pre_game_embed(m: dict, mins: int, live_url: str | None = None) -> discord.Embed:
    hf = flag(m["home_en"])
    af = flag(m["away_en"])
    hora = datetime.fromtimestamp(m["date_ts"], tz=BRT).strftime("%H:%M")
    grupo = m.get("group") or m.get("stage") or ""
    is_brazil = is_brazil_match(m)

    embed = discord.Embed(
        title=f"⏰ Em {mins} minuto{'s' if mins != 1 else ''}!",
        description=f"**{hf} {m['home_pt']}  ×  {m['away_pt']} {af}**",
        color=0x009C3B if is_brazil else 0x3B82F6,
    )
    embed.add_field(name="🕐 Horário", value=f"**{hora} BRT**", inline=True)
    if grupo:
        embed.add_field(name="🏆 Fase", value=grupo, inline=True)
    if live_url:
        fname, fval = _live_field(live_url)
        embed.add_field(name=fname, value=fval, inline=False)
    embed.set_footer(text="Copa do Mundo FIFA™ 2026")
    embed.timestamp = discord.utils.utcnow()
    return embed


def build_kickoff_embed(m: dict) -> discord.Embed:
    hf = flag(m["home_en"])
    af = flag(m["away_en"])
    grupo = m.get("group") or m.get("stage") or ""
    is_brazil = is_brazil_match(m)

    embed = discord.Embed(
        title="🔔 Apito Inicial!",
        description=f"**{hf} {m['home_pt']}  0 — 0  {m['away_pt']} {af}**",
        color=0x009C3B if is_brazil else 0x2ECC71,
    )
    if grupo:
        embed.add_field(name="🏆 Fase", value=grupo, inline=True)
    embed.set_footer(text="Copa do Mundo FIFA™ 2026")
    embed.timestamp = discord.utils.utcnow()
    return embed


def build_ht_embed(m: dict, h_score: int, a_score: int) -> discord.Embed:
    hf = flag(m["home_en"])
    af = flag(m["away_en"])

    embed = discord.Embed(
        title="🔔 Fim do 1º Tempo",
        description=f"**{hf} {m['home_pt']}  {h_score} — {a_score}  {m['away_pt']} {af}**",
        color=0xFFA500,
    )
    embed.set_footer(text="Copa do Mundo FIFA™ 2026")
    embed.timestamp = discord.utils.utcnow()
    return embed


def build_2ht_embed(m: dict, h_score: int, a_score: int, data: dict) -> discord.Embed:
    hf = flag(m["home_en"])
    af = flag(m["away_en"])
    home_data = data.get("HomeTeam") or {}
    away_data = data.get("AwayTeam") or {}
    pmap = _player_map_fifa(home_data, away_data)
    home_pt, away_pt = m["home_pt"], m["away_pt"]

    all_goals = [(home_pt, g) for g in (home_data.get("Goals") or [])] + \
                [(away_pt, g) for g in (away_data.get("Goals") or [])]
    all_cards = [(home_pt, b) for b in (home_data.get("Bookings") or [])] + \
                [(away_pt, b) for b in (away_data.get("Bookings") or [])]

    is_brazil = is_brazil_match(m)
    embed = discord.Embed(
        title="🔔 2º Tempo Iniciado",
        description=(
            f"**{hf} {m['home_pt']}  {h_score} — {a_score}  {m['away_pt']} {af}**\n"
            f"*Placar ao intervalo*"
        ),
        color=0x009C3B if is_brazil else 0xFFA500,
    )

    goals = _goals_text(all_goals, pmap, period=3)
    if goals:
        embed.add_field(name="⚽ Gols — 1º Tempo", value=goals, inline=False)

    reds = _reds_text(all_cards, pmap)
    if reds:
        embed.add_field(name="🟥 Cartões Vermelhos", value=reds, inline=False)

    subs = _subs_text(home_data, away_data, pmap, home_pt, away_pt)
    if subs:
        embed.add_field(name="↕️ Substituições no Intervalo", value=subs, inline=False)

    embed.set_footer(text="Copa do Mundo FIFA™ 2026")
    embed.timestamp = discord.utils.utcnow()
    return embed


def build_final_embed(m: dict, h_score: int, a_score: int, data: dict) -> discord.Embed:
    hf = flag(m["home_en"])
    af = flag(m["away_en"])
    home_data = data.get("HomeTeam") or {}
    away_data = data.get("AwayTeam") or {}
    pmap = _player_map_fifa(home_data, away_data)
    home_pt, away_pt = m["home_pt"], m["away_pt"]

    all_goals = [(home_pt, g) for g in (home_data.get("Goals") or [])] + \
                [(away_pt, g) for g in (away_data.get("Goals") or [])]
    all_cards = [(home_pt, b) for b in (home_data.get("Bookings") or [])] + \
                [(away_pt, b) for b in (away_data.get("Bookings") or [])]

    is_brazil = is_brazil_match(m)
    if is_brazil:
        br_score = h_score if m["home_en"] == "brazil" else a_score
        op_score = a_score if m["home_en"] == "brazil" else h_score
        color = 0x009C3B if br_score > op_score else (0xFFD700 if br_score == op_score else 0xE8473F)
    else:
        color = 0x2C3E50

    grupo = m.get("group") or m.get("stage") or ""
    embed = discord.Embed(
        title="🏁 Fim de Jogo",
        description=f"**{hf} {m['home_pt']}  {h_score} — {a_score}  {m['away_pt']} {af}**",
        color=color,
    )
    if grupo:
        embed.add_field(name="🏆 Fase", value=grupo, inline=True)

    goals = _goals_text(all_goals, pmap)
    if goals:
        embed.add_field(name="⚽ Gols", value=goals, inline=False)

    reds = _reds_text(all_cards, pmap)
    if reds:
        embed.add_field(name="🟥 Cartões Vermelhos", value=reds, inline=False)

    subs = _subs_text(home_data, away_data, pmap, home_pt, away_pt)
    if subs:
        embed.add_field(name="↕️ Substituições", value=subs, inline=False)

    raw_stats = (data.get("Statistics") or data.get("MatchStatistics") or [])
    stats = _stats_text(raw_stats, home_pt, away_pt)
    if stats:
        embed.add_field(name="📊 Estatísticas", value=stats, inline=False)

    embed.set_footer(text="Copa do Mundo FIFA™ 2026")
    embed.timestamp = discord.utils.utcnow()
    return embed


# ── Embed de escalação ────────────────────────────────────────────────────────

def _fmt_player(p: dict, pmap: dict[str, str]) -> str:
    num = p.get("ShirtNumber", "?")
    pid = str(p.get("IdPlayer") or "")
    name = pmap.get(pid, "?")
    pos = _POS_FIFA.get(p.get("Position"), "?")
    return f"`{num:>2}` **{name}** • {pos}"


def build_lineup_embed(m: dict, data: dict) -> discord.Embed | None:
    home_data = data.get("HomeTeam") or {}
    away_data = data.get("AwayTeam") or {}
    home_xi = [p for p in (home_data.get("Players") or []) if p.get("Status") == 1]
    away_xi = [p for p in (away_data.get("Players") or []) if p.get("Status") == 1]

    if not home_xi and not away_xi:
        return None

    pmap = _player_map_fifa(home_data, away_data)
    home_form = home_data.get("Tactics") or ""
    away_form = away_data.get("Tactics") or ""
    hora = datetime.fromtimestamp(m["date_ts"], tz=BRT).strftime("%H:%M")
    grupo = m.get("group") or m.get("stage") or ""
    is_brazil = is_brazil_match(m)
    hf = flag(m["home_en"])
    af = flag(m["away_en"])

    embed = discord.Embed(
        title=f"📋 Escalação — {hf} {m['home_pt']} x {m['away_pt']} {af}",
        description=f"**{grupo}** • {hora} BRT",
        color=0x009C3B if is_brazil else 0xFFD700,
    )

    home_label = f"🏠 {hf} {m['home_pt']}"
    away_label = f"✈️ {af} {m['away_pt']}"
    if home_form:
        home_label += f" • {home_form}"
    if away_form:
        away_label += f" • {away_form}"

    embed.add_field(
        name=home_label,
        value="\n".join(_fmt_player(p, pmap) for p in home_xi) or "—",
        inline=True,
    )
    embed.add_field(
        name=away_label,
        value="\n".join(_fmt_player(p, pmap) for p in away_xi) or "—",
        inline=True,
    )
    embed.set_footer(text="Escalação confirmada · via FIFA")
    embed.timestamp = discord.utils.utcnow()
    return embed


# ── Envio ─────────────────────────────────────────────────────────────────────

async def _send_all(bot: discord.Client, channels: list[tuple[int, int]], **kwargs) -> None:
    for guild_id, channel_id in channels:
        ch = bot.get_channel(channel_id)
        if ch is None:
            continue
        try:
            await ch.send(**kwargs)
        except Exception:
            logger.exception("Falha ao enviar para canal %s (guild %s)", channel_id, guild_id)


def _event_embed(msg: str, team_en: str) -> discord.Embed:
    """Embed mínimo com cor lateral do time para notificações de evento."""
    return discord.Embed(description=msg, color=team_color(team_en))


# ── Verificação ao vivo via FIFA ──────────────────────────────────────────────

async def _check_fifa_live(bot, channels, m: dict, st: dict) -> None:
    label = f"{m['home_pt']} x {m['away_pt']}"
    logger.info("[live] verificando %s (id=%s)", label, m["fifa_id"])
    data = await asyncio.to_thread(_load_fifa_live, m["fifa_id"])
    if not data:
        st["fifa_was_blocked"] = True
        logger.warning("[live] API bloqueada para %s", label)
        return

    home = data.get("HomeTeam") or {}
    away = data.get("AwayTeam") or {}
    period = data.get("Period")
    match_status = data.get("MatchStatus")
    h_score = home.get("Score", 0) or 0
    a_score = away.get("Score", 0) or 0
    home_pt = m["home_pt"]
    away_pt = m["away_pt"]
    pmap = _player_map_fifa(home, away)
    st["pmap"] = pmap
    if not st["home_team_id"]:
        st["home_team_id"] = home.get("IdTeam")
        st["away_team_id"] = away.get("IdTeam")

    total_goals = len(home.get("Goals") or []) + len(away.get("Goals") or [])
    total_bookings = len(home.get("Bookings") or []) + len(away.get("Bookings") or [])
    logger.info("[live] %s | período=%s status=%s placar=%s-%s gols_api=%d cartões_api=%d",
                label, period, match_status, h_score, a_score, total_goals, total_bookings)

    if st.get("fifa_was_blocked"):
        st["fifa_was_blocked"] = False
        for side in (home, away):
            for g in (side.get("Goals") or []):
                st["seen_goals"].add((g.get("IdPlayer"), g.get("Minute"), g.get("Period"), g.get("Type")))
            for b in (side.get("Bookings") or []):
                st["seen_cards"].add((b.get("IdPlayer"), b.get("Minute"), b.get("Card")))
                if b.get("Card") == 2:
                    st["seen_red_players"].add(b.get("IdPlayer"))
        st["goal_count"] = {"home": len(home.get("Goals") or []), "away": len(away.get("Goals") or [])}
        st["last_period"] = period
        if period is not None and period > 3:
            st["ht_sent"] = True
        if period is not None and period >= 4:
            st["2ht_sent"] = True
        return

    home_goals = home.get("Goals") or []
    away_goals = away.get("Goals") or []
    all_goals_raw = [(home_pt, g) for g in home_goals] + [(away_pt, g) for g in away_goals]
    current_goal_keys: set = {
        (g.get("IdPlayer"), g.get("Minute"), g.get("Period"), g.get("Type"))
        for _, g in all_goals_raw
    }
    cur_counts = {"home": len(home_goals), "away": len(away_goals)}
    prev_counts = st["goal_count"]

    all_cards_raw = [(home_pt, b) for b in (home.get("Bookings") or [])] + \
                    [(away_pt, b) for b in (away.get("Bookings") or [])]
    current_red_players: set = {b.get("IdPlayer") for _, b in all_cards_raw if b.get("Card") == 2}
    current_yellow_players: set = {b.get("IdPlayer") for _, b in all_cards_raw if b.get("Card") == 1}

    def _same_goal(k, pid, period, gtype):
        return k[0] == pid and k[2] == period and k[3] == gtype

    # -- novos gols --
    for team_pt, g in all_goals_raw:
        gkey = (g.get("IdPlayer"), g.get("Minute"), g.get("Period"), g.get("Type"))
        if gkey in st["seen_goals"]:
            continue
        pid, _gmin, period, gtype = gkey
        # Pênaltis convertidos na disputa (Period 11) são anunciados pela timeline
        # (Type 41) com placar próprio — aqui só registramos para não notificar como gol.
        if period == 11:
            st["seen_goals"].add(gkey)
            continue
        side = "home" if team_pt == home_pt else "away"
        same_identity = any(_same_goal(k, pid, period, gtype) for k in st["seen_goals"])
        increased = cur_counts[side] > prev_counts.get(side, 0)
        # Mesmo jogador/período/tipo já visto e o total não subiu → correção de dado
        # (minuto reajustado), não um gol novo. Registra sem notificar e migra o pending.
        if same_identity and not increased:
            st["seen_goals"].add(gkey)
            for pk in list(st["pending_goals"]):
                if pk not in current_goal_keys and _same_goal(pk, pid, period, gtype):
                    st["pending_goals"][gkey] = st["pending_goals"].pop(pk)
                    break
            logger.info("[live] correção de gol (re-key) ignorada: %s %s", team_pt, gkey)
            continue
        logger.info("[live] novo gol detectado: %s %s", team_pt, gkey)
        st["seen_goals"].add(gkey)
        player = pmap.get(str(pid or ""), "?")
        minute = g.get("Minute", "?")
        extra = " (contra)" if gtype == 3 else (" (pen)" if gtype == 4 else "")
        st["pending_goals"][gkey] = {
            "ts": time.time(), "player": player, "team_pt": team_pt,
            "minute": minute, "extra": extra, "side": side,
            "team_score_at_announce": cur_counts[side],
        }
        team_en_scorer = m["home_en"] if side == "home" else m["away_en"]
        await _send_all(
            bot, channels,
            embed=_event_embed(
                f"⚽ **GOL! {player}{extra}** ({team_pt}) — {minute}'\n"
                f"**{home_pt} {h_score}-{a_score} {away_pt}**",
                team_en_scorer,
            ),
        )

    # -- novos cartões vermelhos (dedup por jogador: cada um leva no máx. 1) --
    for team_pt, b in all_cards_raw:
        if b.get("Card") != 2:
            continue
        pid = b.get("IdPlayer")
        if pid in st["seen_red_players"]:
            continue
        st["seen_red_players"].add(pid)
        logger.info("[live] vermelho detectado: %s jogador=%s", team_pt, pid)
        player = pmap.get(str(pid or ""), "?")
        minute = b.get("Minute", "?")
        st["pending_cards"][pid] = {
            "ts": time.time(), "player": player, "team_pt": team_pt, "minute": minute,
        }
        team_en_card = m["home_en"] if team_pt == home_pt else m["away_en"]
        await _send_all(bot, channels,
                        embed=_event_embed(f"🟥 **{player}** ({team_pt}) — {minute}'", team_en_card))

    # -- VAR: gol removido --
    now_var = time.time()
    for gkey, info in list(st["pending_goals"].items()):
        if gkey in current_goal_keys:
            if now_var - info["ts"] >= VAR_WINDOW_SECS:
                del st["pending_goals"][gkey]
            continue
        # gol sumiu da API
        del st["pending_goals"][gkey]
        pid, _gmin, period, gtype = gkey
        replacement = next((k for k in current_goal_keys if _same_goal(k, pid, period, gtype)), None)
        if replacement:
            # mesmo gol com minuto/dado corrigido — segue valendo, sem anular
            st["seen_goals"].add(replacement)
            continue
        # confirma anulação só se o placar do time realmente caiu
        if cur_counts[info["side"]] >= info["team_score_at_announce"]:
            logger.info("[live] gol removido sem queda de placar — ignorado: %s", gkey)
            continue
        team_en_var = m["home_en"] if info["side"] == "home" else m["away_en"]
        await _send_all(
            bot, channels,
            embed=_event_embed(
                f"🚫 **Gol anulado pelo VAR!** {info['player']}{info['extra']} "
                f"({info['team_pt']}) — {info['minute']}'\n"
                f"**{home_pt} {h_score}-{a_score} {away_pt}**",
                team_en_var,
            ),
        )

    # -- VAR: vermelho revertido / revisto para amarelo --
    for pid, info in list(st["pending_cards"].items()):
        if pid in current_red_players:
            if now_var - info["ts"] >= VAR_WINDOW_SECS:
                del st["pending_cards"][pid]
            continue
        # o vermelho desse jogador sumiu da API
        del st["pending_cards"][pid]
        st["seen_red_players"].discard(pid)
        team_en_var = m["home_en"] if info["team_pt"] == home_pt else m["away_en"]
        if pid in current_yellow_players:
            msg = (f"🟨 **Vermelho revisto para amarelo pelo VAR!** {info['player']} "
                   f"({info['team_pt']}) — {info['minute']}'")
        else:
            msg = (f"↩️ **Vermelho revertido pelo VAR!** {info['player']} "
                   f"({info['team_pt']}) — {info['minute']}'")
        await _send_all(bot, channels, embed=_event_embed(msg, team_en_var))

    st["goal_count"] = cur_counts

    # -- transições de período --
    last = st["last_period"]
    if period != last:
        logger.info("[live] transição de período: %s → %s (%s)", last, period, label)
        if last == 3 and not st["ht_sent"]:
            st["ht_sent"] = True
            await _send_all(bot, channels, embed=build_ht_embed(m, h_score, a_score))

        if period == 4 and not st["2ht_sent"]:
            st["2ht_sent"] = True
            await _send_all(bot, channels, embed=build_2ht_embed(m, h_score, a_score, data))

        if period == 11 and not st["shootout_announced"]:
            st["shootout_announced"] = True
            await _send_all(
                bot, channels,
                embed=discord.Embed(
                    title="🥅 Disputa de Pênaltis",
                    description=(
                        "**DAMAS E CABALLEROS, DEVO INFORMAR-LHES QUE TEREMOS "
                        "DECISION DE PENAL**"
                    ),
                    color=0xFFD700,
                ),
            )

        st["last_period"] = period

    st["h_score"] = h_score
    st["a_score"] = a_score

    if not st["final_sent"] and match_status == 0 and st["kicked_off"]:
        st["final_sent"] = True
        await _send_all(bot, channels, embed=build_final_embed(m, h_score, a_score, data))


# ── Verificação de timeline ───────────────────────────────────────────────────

def _team_from_event(m: dict, st: dict, team_id) -> tuple[str, str, str]:
    """Retorna (nome_pt, flag_emoji, team_en) do time com base no IdTeam do evento."""
    if team_id and team_id == st.get("home_team_id"):
        return m["home_pt"], flag(m["home_en"]), m["home_en"]
    if team_id and team_id == st.get("away_team_id"):
        return m["away_pt"], flag(m["away_en"]), m["away_en"]
    return "?", "", ""


_TL_TYPE_NAMES: dict[int, str] = {
    1: "Assistência", 2: "Amarelo", 3: "Vermelho", 5: "Substituição",
    6: "Pênalti", 7: "Kickoff", 8: "Fim período", 12: "Chute a gol",
    15: "Impedimento", 16: "Escanteio", 18: "Falta", 26: "Fim de jogo",
    34: "Gol contra", 41: "Pênalti convertido", 57: "Gol evitado",
    60: "Pênalti perdido", 71: "VAR", 78: "Reinício",
    79: "Cara ou coroa", 83: "Atraso",
}


async def _check_fifa_timeline(bot, channels, m: dict, st: dict) -> None:
    label = f"{m['home_pt']} x {m['away_pt']}"
    logger.info("[timeline] verificando %s (id=%s stage=%s)", label, m.get("fifa_id"), m.get("stage_id"))
    events = await asyncio.to_thread(_load_fifa_timeline, m)
    if events is None:
        logger.warning("[timeline] sem dados para %s", label)
        return

    novos = [e for e in events if e.get("EventId") and e["EventId"] not in st["seen_timeline_events"]]
    logger.info("[timeline] %s | total=%d vistos=%d novos=%d",
                label, len(events), len(st["seen_timeline_events"]), len(novos))
    if not novos:
        logger.info("[timeline] %s | nenhum evento novo", label)

    for event in events:
        eid = event.get("EventId")
        if not eid or eid in st["seen_timeline_events"]:
            continue
        st["seen_timeline_events"].add(eid)
        etype_name = _TL_TYPE_NAMES.get(event.get("Type"), f"type={event.get('Type')}")
        logger.info("[timeline] NOVO evento: %s | %s min=%s eid=%s",
                    label, etype_name, event.get("MatchMinute"), eid)

        etype = event.get("Type")
        minute = event.get("MatchMinute", "?")
        team_id = event.get("IdTeam")
        player_id = str(event.get("IdPlayer") or "")
        team_name, team_flag, team_en = _team_from_event(m, st, team_id)
        pmap = st.get("pmap") or {}

        def _send_event(msg: str) -> None:
            return _send_all(bot, channels, embed=_event_embed(msg, team_en))

        if etype == 1:  # Assistência
            sub_id = str(event.get("IdSubPlayer") or "")
            assister = pmap.get(sub_id, "") if sub_id else ""
            if assister:
                msg = f"🅰️ **Assistência de {assister}!** ({team_flag} {team_name}) — {minute}'"
            else:
                msg = f"🅰️ **Assistência!** {team_flag} {team_name} — {minute}'"
            await _send_event(msg)

        elif etype == 2:  # Cartão amarelo
            player = pmap.get(player_id, "") if player_id else ""
            if player:
                msg = f"🟨 **Cartão amarelo!** {player} ({team_flag} {team_name}) — {minute}'"
            elif team_name != "?":
                msg = f"🟨 **Cartão amarelo!** {team_flag} {team_name} — {minute}'"
            else:
                msg = f"🟨 **Cartão amarelo!** — {minute}'"
            await _send_event(msg)

        elif etype == 5:  # Substituição
            sub_id = str(event.get("IdSubPlayer") or "")
            p_in = pmap.get(player_id, "?") if player_id else "?"
            p_out = pmap.get(sub_id, "?") if sub_id else "?"
            await _send_event(f"↕️ **Substituição!** {p_in} ← {p_out} ({team_flag} {team_name}) — {minute}'")

        elif etype == 7:  # Kickoff / reinício de período
            try:
                raw_min = str(minute).split("+")[0].strip()
                minute_int = int(raw_min) if raw_min.isdigit() else 0
            except (ValueError, TypeError):
                minute_int = 0

            if minute_int >= 46:
                # Minuto 46+: é início do 2º tempo SÓ SE o kickoff já foi notificado.
                # Se kickoff_notified == False, o bot iniciou durante acréscimo — não faz nada.
                if st["kickoff_notified"] and not st["2ht_sent"]:
                    logger.info("[timeline] 2º tempo detectado (min=%s) — %s", minute, label)
                    st["2ht_sent"] = True
                    h = st.get("h_score", 0)
                    a = st.get("a_score", 0)
                    data2 = await asyncio.to_thread(_load_fifa_live, m["fifa_id"])
                    if data2:
                        await _send_all(bot, channels, embed=build_2ht_embed(m, h, a, data2))
                    else:
                        hf_ = flag(m["home_en"])
                        af_ = flag(m["away_en"])
                        embed2 = discord.Embed(
                            title="🔔 2º Tempo Iniciado",
                            description=f"**{hf_} {m['home_pt']}  {h} — {a}  {m['away_pt']} {af_}**",
                            color=0x009C3B if is_brazil_match(m) else 0xFFA500,
                        )
                        embed2.set_footer(text="Copa do Mundo FIFA™ 2026")
                        embed2.timestamp = discord.utils.utcnow()
                        await _send_all(bot, channels, embed=embed2)
                else:
                    logger.info("[timeline] Type7 min=%s ignorado (kickoff_notified=%s 2ht_sent=%s)",
                                minute, st["kickoff_notified"], st["2ht_sent"])
            else:
                # Minuto < 46: é o kickoff real do jogo
                if not st["kickoff_notified"]:
                    st["kickoff_notified"] = True
                    await _send_all(bot, channels, embed=build_kickoff_embed(m))

        elif etype == 6:  # Pênalti marcado (árbitro)
            await _send_event(f"🎯 **Pênalti!** {team_flag} **{team_name}** — {minute}'")

        elif etype in (41, 60):  # Disputa de pênaltis: 41=convertido, 60=defendido/perdido
            player = pmap.get(player_id, "") if player_id else ""
            is_home = bool(team_id) and team_id == st.get("home_team_id")
            if etype == 41:
                st["shootout_home" if is_home else "shootout_away"] += 1
            sh = st.get("shootout_home", 0)
            sa = st.get("shootout_away", 0)
            quem = f"**{player}**" if player else f"{team_flag} **{team_name}**"
            emoji = "🟢" if etype == 41 else "❌"
            verbo = "converteu" if etype == 41 else "perdeu"
            placar = f"**{m['home_pt']} {sh} — {sa} {m['away_pt']}** *(pênaltis)*"
            await _send_event(
                f"{emoji} **Pênalti** — {quem} {verbo}! ({team_flag} {team_name})\n{placar}"
            )

        elif etype == 12:  # Chute a gol
            player = pmap.get(player_id, "") if player_id else ""
            if player:
                msg = f"🥅 **Chute a gol!** {player} ({team_flag} {team_name}) — {minute}'"
            elif team_name != "?":
                msg = f"🥅 **Chute a gol!** {team_flag} {team_name} — {minute}'"
            else:
                msg = f"🥅 **Chute a gol!** — {minute}'"
            await _send_event(msg)

        elif etype == 15:  # Impedimento
            player = pmap.get(player_id, "") if player_id else ""
            if player:
                msg = f"🚩 **Impedimento!** {player} ({team_flag} {team_name}) — {minute}'"
            elif team_name != "?":
                msg = f"🚩 **Impedimento!** {team_flag} {team_name} — {minute}'"
            else:
                msg = f"🚩 **Impedimento!** — {minute}'"
            await _send_event(msg)

        elif etype == 16:  # Escanteio
            player = pmap.get(player_id, "") if player_id else ""
            if player:
                msg = f"🚩 **Escanteio!** {player} ({team_flag} {team_name}) — {minute}'"
            elif team_name != "?":
                msg = f"🚩 **Escanteio!** {team_flag} {team_name} — {minute}'"
            else:
                msg = f"🚩 **Escanteio!** — {minute}'"
            await _send_event(msg)

        elif etype == 18:  # Falta
            player = pmap.get(player_id, "") if player_id else ""
            if player:
                msg = f"⚠️ **Falta!** {player} ({team_flag} {team_name}) — {minute}'"
            elif team_name != "?":
                msg = f"⚠️ **Falta!** {team_flag} {team_name} — {minute}'"
            else:
                msg = f"⚠️ **Falta!** — {minute}'"
            await _send_event(msg)

        elif etype == 57:  # Gol evitado
            player = pmap.get(player_id, "") if player_id else ""
            if player:
                msg = f"🧤 **Gol evitado!** {player} ({team_flag} {team_name}) — {minute}'"
            elif team_name != "?":
                msg = f"🧤 **Gol evitado!** {team_flag} {team_name} — {minute}'"
            else:
                msg = f"🧤 **Gol evitado!** — {minute}'"
            await _send_event(msg)

        elif etype == 83:  # Atraso / pausa
            desc = next(
                (d.get("Description") for d in (event.get("EventDescription") or [])
                 if d.get("Locale") == "pt-BR"),
                "Jogo interrompido",
            )
            await _send_all(bot, channels, embed=_event_embed(f"⏸️ **{desc}** — {minute}'", team_en))


# ── Verificação de escalação ──────────────────────────────────────────────────

async def check_lineup(m: dict) -> discord.Embed | None:
    if not m.get("fifa_id"):
        return None
    data = await asyncio.to_thread(_load_fifa_live, m["fifa_id"])
    if not data:
        return None
    return build_lineup_embed(m, data)


# ── Job principal (chamado a cada 10s pelo cog) ───────────────────────────────

async def run_monitor_tick(bot: discord.Client, channels: list[tuple[int, int]]) -> None:
    global _first_tick
    if not channels:
        return

    try:
        matches = await asyncio.to_thread(get_jogos_rodada)
    except Exception:
        logger.exception("Erro ao buscar jogos da rodada")
        return

    now = time.time()
    from collections import Counter
    status_count = Counter(m["status"] for m in matches)
    logger.info("[tick] rodada com %d jogos | channels=%d | first_tick=%s | %s",
                len(matches), len(channels), _first_tick,
                " ".join(f"{s}={n}" for s, n in status_count.items()))

    for m in matches:
        if not m.get("fifa_id"):
            continue
        try:
            await _tick_match(bot, channels, m, now)
        except Exception:
            logger.exception("[tick] erro ao processar %s x %s — ignorado neste tick",
                             m.get("home_pt"), m.get("away_pt"))

    _first_tick = False


async def _tick_match(bot, channels, m: dict, now: float) -> None:
    key = m["fifa_id"]
    st = _state(key)
    ts = m["date_ts"]
    status = m["status"]
    hf = flag(m["home_en"])
    af = flag(m["away_en"])

    # Na inicialização, suprime escalação para jogos já dentro da janela de 1h
    if _first_tick and not st["lineup_sent"] and status == "notstarted" and 0 < (ts - now) <= LINEUP_WINDOW_SECS:
        st["lineup_sent"] = True

    # ── Suspensão / retomada ──
    _last = st["last_status"]
    if _last == "inprogress" and status == "notstarted" and st["kicked_off"] and not st["final_sent"]:
        st["suspended"] = True
        await _send_all(
            bot, channels,
            embed=discord.Embed(
                description=f"⚠️ **Jogo suspenso temporariamente!**\n⚽ **{hf} {m['home_pt']} x {m['away_pt']} {af}**",
                color=0xFF6600,
            ),
        )
    elif st.get("suspended") and status == "inprogress":
        st["suspended"] = False
        h = m["home_score"] if m["home_score"] is not None else 0
        a = m["away_score"] if m["away_score"] is not None else 0
        await _send_all(
            bot, channels,
            embed=discord.Embed(
                description=f"▶️ **Jogo retomado!**\n⚽ **{hf} {m['home_pt']} {h}-{a} {m['away_pt']} {af}**",
                color=0x2ECC71,
            ),
        )
    st["last_status"] = status

    # ── Aviso 1 hora antes ──
    if not st["announced_60"] and status == "notstarted" and 1800 < (ts - now) <= 3600:
        st["announced_60"] = True
        mins = max(1, int((ts - now) / 60))
        live_url = await asyncio.to_thread(youtube.get_cazetv_live, ts)
        await _send_all(bot, channels, embed=build_pre_game_embed(m, mins, live_url))

    # ── Aviso 30 min antes ──
    if not st["announced_30"] and status == "notstarted" and 0 < (ts - now) <= 1800:
        st["announced_30"] = True
        mins = max(1, int((ts - now) / 60))
        live_url = await asyncio.to_thread(youtube.get_cazetv_live, ts)
        await _send_all(bot, channels, embed=build_pre_game_embed(m, mins, live_url))

    # ── Escalação (1h antes) ──
    if not st["lineup_sent"] and status == "notstarted" and 0 < (ts - now) <= LINEUP_WINDOW_SECS:
        interval = LINEUP_INTERVAL_BRAZIL if is_brazil_match(m) else LINEUP_INTERVAL_OTHER
        if (now - st["last_lineup_check"]) >= interval:
            st["last_lineup_check"] = now
            embed = await check_lineup(m)
            if embed:
                st["lineup_sent"] = True
                for guild_id, channel_id in channels:
                    ch = bot.get_channel(channel_id)
                    if ch is None:
                        continue
                    try:
                        await ch.send(
                            content=(
                                f"📋 **Escalação divulgada!** "
                                f"{hf} {m['home_pt']} x {m['away_pt']} {af}"
                            ),
                            embed=embed,
                        )
                    except Exception:
                        logger.exception("Erro ao enviar embed de escalação")

    # ── Priming ──
    if not st["primed"] and status == "inprogress":
        logger.info("[priming] %s x %s (first_tick=%s)", m["home_pt"], m["away_pt"], _first_tick)
        st["kicked_off"] = True
        data = await asyncio.to_thread(_load_fifa_live, m["fifa_id"])
        if data:
            home = data.get("HomeTeam") or {}
            away = data.get("AwayTeam") or {}
            st["home_team_id"] = home.get("IdTeam")
            st["away_team_id"] = away.get("IdTeam")
            st["pmap"] = _player_map_fifa(home, away)
            for side in (home, away):
                for g in (side.get("Goals") or []):
                    st["seen_goals"].add((g.get("IdPlayer"), g.get("Minute"), g.get("Period"), g.get("Type")))
                for b in (side.get("Bookings") or []):
                    st["seen_cards"].add((b.get("IdPlayer"), b.get("Minute"), b.get("Card")))
                    if b.get("Card") == 2:
                        st["seen_red_players"].add(b.get("IdPlayer"))
            st["goal_count"] = {"home": len(home.get("Goals") or []), "away": len(away.get("Goals") or [])}
            period = data.get("Period")
            st["last_period"] = period
            if period is not None and period > 3:
                st["ht_sent"] = True
            if period is not None and period >= 4:
                st["2ht_sent"] = True
            if period == 11:
                # Reinício durante a disputa: já anunciada e placar reconstruído.
                st["shootout_announced"] = True
                st["shootout_home"] = sum(
                    1 for g in (home.get("Goals") or []) if g.get("Period") == 11)
                st["shootout_away"] = sum(
                    1 for g in (away.get("Goals") or []) if g.get("Period") == 11)
            tl_events = await asyncio.to_thread(_load_fifa_timeline, m)
            primed_tl = 0
            for ev in (tl_events or []):
                eid = ev.get("EventId")
                etype_ev = ev.get("Type")
                if not eid:
                    continue
                if etype_ev == 7:
                    if _first_tick:
                        st["seen_timeline_events"].add(eid)
                        st["kickoff_notified"] = True
                else:
                    st["seen_timeline_events"].add(eid)
                    primed_tl += 1
            logger.info("[priming] concluído: gols=%d cartões=%d tl_eventos=%d",
                        len(st["seen_goals"]), len(st["seen_cards"]), primed_tl)
            st["primed"] = True
        else:
            logger.warning("[priming] live API sem dados para %s x %s", m["home_pt"], m["away_pt"])
        return

    # ── Ao vivo ──
    if status == "inprogress":
        logger.info("[tick] ao vivo: %s x %s | primed=%s kicked_off=%s kickoff_notified=%s",
                    m["home_pt"], m["away_pt"], st["primed"], st["kicked_off"], st["kickoff_notified"])
        await _check_fifa_live(bot, channels, m, st)
        await _check_fifa_timeline(bot, channels, m, st)

    # ── Fim de jogo (via status finished) ──
    if not st["final_sent"] and status == "finished" and st["kicked_off"]:
        st["final_sent"] = True
        h = m["home_score"] if m["home_score"] is not None else 0
        a = m["away_score"] if m["away_score"] is not None else 0
        data = await asyncio.to_thread(_load_fifa_live, m["fifa_id"])
        if data:
            await _send_all(bot, channels, embed=build_final_embed(m, h, a, data))
        else:
            await _send_all(
                bot, channels,
                embed=discord.Embed(
                    title="🏁 Fim de Jogo",
                    description=f"**{hf} {m['home_pt']}  {h} — {a}  {m['away_pt']} {af}**",
                    color=0x2C3E50,
                ),
            )
