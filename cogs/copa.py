"""Cog Copa — comandos + monitoramento ao vivo."""

import asyncio
import logging
import math
import time
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

import time

from services import copa as copa_svc
from services import copa_monitor as monitor
from services import gate
from services import youtube
from services.db import get_all_copa_channels, get_copa_channel, set_copa_channel

logger = logging.getLogger(__name__)

BRT = copa_svc.BRT


def _score_str(m: dict) -> str:
    return copa_svc._score(m)


def _ts_str(ts: int) -> str:
    return copa_svc._ts(ts)


def _flag(en: str) -> str:
    return copa_svc.flag(en)


# ── Embeds de consulta ────────────────────────────────────────────────────────

_DAYS_PER_PAGE = 4


def _jogos_por_dia(jogos: list[dict]) -> list[tuple[str, str]]:
    """Retorna lista de (dia, valor_field) para todos os dias com jogos."""
    por_dia: dict[str, list[str]] = {}
    for m in jogos:
        status = m["status"]
        icon = "🔴" if status == "inprogress" else ("✅" if status == "finished" else "🗓️")
        hf = _flag(m["home_en"])
        af = _flag(m["away_en"])
        hora = datetime.fromtimestamp(m["date_ts"], tz=BRT).strftime("%H:%M")
        dia  = datetime.fromtimestamp(m["date_ts"], tz=BRT).strftime("%d/%m")
        linha = f"{icon} **{hf} {m['home_pt']}  {_score_str(m)}  {m['away_pt']} {af}** — {hora} BRT"
        por_dia.setdefault(dia, []).append(linha)
    result = []
    for dia, linhas in por_dia.items():
        valor = "\n".join(linhas)
        if len(valor) > 1024:
            valor = valor[:1020] + "\n…"
        result.append((dia, valor))
    return result


def _embed_jogos_page(dias: list[tuple[str, str]], page: int, total_pages: int) -> discord.Embed:
    embed = discord.Embed(title="🏆 Copa 2026 — Jogos da Rodada", color=0x3B82F6)
    start = page * _DAYS_PER_PAGE
    for dia, valor in dias[start:start + _DAYS_PER_PAGE]:
        embed.add_field(name=f"📅 {dia}", value=valor, inline=False)
    footer = "Use /copa-time <seleção> para detalhes de um time"
    if total_pages > 1:
        footer = f"Página {page + 1}/{total_pages} · " + footer
    embed.set_footer(text=footer)
    return embed


def _embed_jogos_rodada(jogos: list[dict]) -> discord.Embed:
    """Compatibilidade: retorna a primeira página (usado em testes)."""
    if not jogos:
        e = discord.Embed(title="🏆 Copa 2026 — Jogos da Rodada", color=0x3B82F6)
        e.description = "Nenhum jogo encontrado para a rodada atual."
        return e
    dias = _jogos_por_dia(jogos)
    return _embed_jogos_page(dias, 0, math.ceil(len(dias) / _DAYS_PER_PAGE))


class JogosView(discord.ui.View):
    def __init__(self, dias: list[tuple[str, str]]):
        super().__init__(timeout=300)
        self.dias = dias
        self.page = 0
        self.total = math.ceil(len(dias) / _DAYS_PER_PAGE)
        self._sync()

    def _sync(self):
        self.btn_prev.disabled = self.page == 0
        self.btn_next.disabled = self.page >= self.total - 1

    def _embed(self) -> discord.Embed:
        return _embed_jogos_page(self.dias, self.page, self.total)

    @discord.ui.button(label="◀ Anterior", style=discord.ButtonStyle.secondary)
    async def btn_prev(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        self.page -= 1
        self._sync()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="Próximo ▶", style=discord.ButtonStyle.secondary)
    async def btn_next(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        self.page += 1
        self._sync()
        await interaction.response.edit_message(embed=self._embed(), view=self)


def _embed_team(team_query: str, matches: list[dict]) -> discord.Embed:
    t_en = copa_svc._resolve(team_query)
    pt_name = copa_svc.EN_TO_PT.get(t_en, team_query.title())
    team_flag = _flag(t_en)

    embed = discord.Embed(
        title=f"🏆 {team_flag} {pt_name} — Copa 2026",
        color=0x3B82F6,
    )
    if not matches:
        embed.description = "Nenhum jogo encontrado."
        return embed

    ao_vivo = [m for m in matches if m["status"] == "inprogress"]
    passados = [m for m in matches if m["status"] == "finished"]
    proximos = sorted([m for m in matches if m["status"] == "notstarted"], key=lambda x: x["date_ts"])

    def _fmt(m):
        hf = _flag(m["home_en"])
        af = _flag(m["away_en"])
        grupo = m.get("group") or m.get("stage") or ""
        return f"{hf} **{m['home_pt']}** {_score_str(m)} **{m['away_pt']}** {af}  `{_ts_str(m['date_ts'])} BRT`  *{grupo}*"

    if ao_vivo:
        embed.add_field(name="🔴 Ao vivo", value="\n".join(_fmt(m) for m in ao_vivo), inline=False)
    if passados:
        embed.add_field(
            name="✅ Resultados",
            value="\n".join(_fmt(m) for m in sorted(passados, key=lambda x: x["date_ts"])),
            inline=False,
        )
    if proximos:
        embed.add_field(name="🗓️ Próximos", value="\n".join(_fmt(m) for m in proximos), inline=False)

    return embed


def _embed_grupo(letter: str, gm: list[dict]) -> discord.Embed:
    embed = discord.Embed(
        title=f"🏆 Copa 2026 — Grupo {letter.upper()}",
        color=0x3B82F6,
    )
    if gm:
        jogos_str = []
        for m in gm:
            hf = _flag(m["home_en"])
            af = _flag(m["away_en"])
            jogos_str.append(
                f"{hf} **{m['home_pt']}** {_score_str(m)} **{m['away_pt']}** {af}  `{_ts_str(m['date_ts'])} BRT`"
            )
        embed.add_field(name="Jogos", value="\n".join(jogos_str), inline=False)

    return embed


def _embed_artilharia(scorers: list[dict]) -> discord.Embed:
    embed = discord.Embed(title="🥇 Artilharia — Copa 2026", color=0x3B82F6)
    if not scorers:
        embed.description = "⏳ Nenhum gol registrado ainda."
        return embed
    lines = ["```", f"{'#':<4} {'Jogador':<22} {'Time':<20} Gols", "─" * 52]
    for i, s in enumerate(scorers[:20], 1):
        lines.append(f"{i:<4} {s['name']:<22} {s['team']:<20} {s['goals']}")
    lines.append("```")
    embed.description = "\n".join(lines)
    return embed


def _embed_resumo_diario(jogos: list[dict], now_brt: datetime) -> discord.Embed:
    hoje_str = now_brt.strftime("%d/%m/%Y")
    embed = discord.Embed(
        title="📅 Jogos de Hoje — Copa do Mundo 2026",
        color=0x3B82F6,
    )
    lines = []
    for m in sorted(jogos, key=lambda x: x["date_ts"]):
        hora = datetime.fromtimestamp(m["date_ts"], tz=BRT).strftime("%H:%M")
        grupo = m.get("group") or m.get("stage") or ""
        hf = _flag(m["home_en"])
        af = _flag(m["away_en"])
        grupo_str = f"  *{grupo}*" if grupo else ""
        lines.append(f"⚽ **{hora} BRT** — {hf} **{m['home_pt']}** × **{m['away_pt']}** {af}{grupo_str}")
    embed.description = "\n".join(lines)
    embed.set_footer(text=hoje_str + " · Copa do Mundo FIFA™ 2026")
    embed.timestamp = discord.utils.utcnow()
    return embed


# ── Cog ───────────────────────────────────────────────────────────────────────

class CopaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._monitor_channels: list[tuple[int, int]] = []
        self._daily_sent_date: str = ""

    async def cog_load(self) -> None:
        self._monitor_channels = await get_all_copa_channels()
        self._monitor_loop.start()

    async def cog_unload(self) -> None:
        self._monitor_loop.cancel()

    @tasks.loop(seconds=10)
    async def _monitor_loop(self) -> None:
        await monitor.run_monitor_tick(self.bot, self._monitor_channels)
        await self._check_daily_summary()

    @_monitor_loop.before_loop
    async def _before_monitor(self) -> None:
        await self.bot.wait_until_ready()

    async def _check_daily_summary(self) -> None:
        now_brt = datetime.now(BRT)
        today_str = now_brt.strftime("%Y-%m-%d")
        if now_brt.hour != 9 or self._daily_sent_date == today_str:
            return
        self._daily_sent_date = today_str

        try:
            jogos = await asyncio.to_thread(copa_svc.get_jogos_hoje)
        except Exception:
            logger.exception("Erro ao buscar jogos para resumo diário")
            return

        if not jogos:
            return

        embed = _embed_resumo_diario(jogos, datetime.now(BRT))
        for guild_id, channel_id in self._monitor_channels:
            ch = self.bot.get_channel(channel_id)
            if ch:
                try:
                    await ch.send(embed=embed)
                except Exception:
                    logger.exception("Erro ao enviar resumo diário para guild %s", guild_id)

    # ── Slash commands ────────────────────────────────────────────────────────

    @app_commands.command(name="copa", description="Mostra todos os jogos da rodada atual da Copa 2026")
    async def cmd_copa(self, interaction: discord.Interaction) -> None:
        if not await gate.allowed(interaction):
            return
        await interaction.response.defer()
        try:
            jogos = await asyncio.to_thread(copa_svc.get_jogos_rodada)
        except Exception:
            await interaction.followup.send("❌ Erro ao buscar jogos. Tente novamente.", ephemeral=True)
            return
        if not jogos:
            await interaction.followup.send("Nenhum jogo encontrado para a rodada atual.", ephemeral=True)
            return
        dias = _jogos_por_dia(jogos)
        if len(dias) <= _DAYS_PER_PAGE:
            await interaction.followup.send(embed=_embed_jogos_page(dias, 0, 1))
        else:
            view = JogosView(dias)
            await interaction.followup.send(embed=view._embed(), view=view)

    @app_commands.command(name="copa-time", description="Jogos de uma seleção na Copa 2026")
    @app_commands.describe(selecao="Nome da seleção (ex: Brasil, Argentina, França)")
    async def cmd_copa_time(self, interaction: discord.Interaction, selecao: str) -> None:
        if not await gate.allowed(interaction):
            return
        await interaction.response.defer()
        try:
            matches = await asyncio.to_thread(copa_svc.get_team_matches, selecao)
        except Exception:
            await interaction.followup.send("❌ Erro ao buscar dados. Tente novamente.", ephemeral=True)
            return
        embed = _embed_team(selecao, matches)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="copa-grupo", description="Tabela e jogos de um grupo da Copa 2026")
    @app_commands.describe(letra="Letra do grupo (A–L)")
    async def cmd_copa_grupo(self, interaction: discord.Interaction, letra: str) -> None:
        if not await gate.allowed(interaction):
            return
        await interaction.response.defer()
        if len(letra) != 1 or letra.upper() not in "ABCDEFGHIJKL":
            await interaction.followup.send("❌ Informe uma letra de grupo válida (A–L).", ephemeral=True)
            return
        try:
            gm = await asyncio.to_thread(copa_svc.get_group_matches, letra)
        except Exception:
            await interaction.followup.send("❌ Erro ao buscar dados. Tente novamente.", ephemeral=True)
            return
        embed = _embed_grupo(letra, gm)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="copa-artilharia", description="Artilheiros da Copa 2026")
    async def cmd_copa_artilharia(self, interaction: discord.Interaction) -> None:
        if not await gate.allowed(interaction):
            return
        await interaction.response.defer()
        try:
            scorers = await asyncio.to_thread(copa_svc.get_scorers)
        except Exception:
            await interaction.followup.send("❌ Erro ao buscar artilheiros.", ephemeral=True)
            return
        embed = _embed_artilharia(scorers)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="copa-quando", description="Mostra em quantos minutos começa a próxima partida")
    async def cmd_copa_quando(self, interaction: discord.Interaction) -> None:
        if not await gate.allowed(interaction):
            return
        await interaction.response.defer()
        try:
            matches = await asyncio.to_thread(copa_svc.get_jogos_rodada)
        except Exception:
            await interaction.followup.send("❌ Erro ao buscar jogos.", ephemeral=True)
            return

        now = time.time()
        upcoming = sorted(
            [m for m in matches if m["status"] == "notstarted" and m["date_ts"] > now],
            key=lambda m: m["date_ts"],
        )
        if not upcoming:
            await interaction.followup.send("Nenhuma partida agendada encontrada.", ephemeral=True)
            return

        m = upcoming[0]
        mins = max(1, int((m["date_ts"] - now) / 60))
        live_url = await asyncio.to_thread(youtube.get_cazetv_live, m["date_ts"])
        embed = monitor.build_pre_game_embed(m, mins, live_url)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="config-copa", description="Guia e configuração das notificações da Copa (apenas admins)")
    @app_commands.describe(canal="(Opcional) Canal onde as notificações automáticas serão enviadas")
    @app_commands.default_permissions(administrator=True)
    async def cmd_config_copa(
        self, interaction: discord.Interaction, canal: discord.TextChannel | None = None
    ) -> None:
        guild_id = interaction.guild_id

        if canal:
            await set_copa_channel(guild_id, canal.id)
            self._monitor_channels = await get_all_copa_channels()

        channel_id = await get_copa_channel(guild_id)
        ch = interaction.guild.get_channel(channel_id) if channel_id else None
        canal_str = ch.mention if ch else "❌ Não configurado"

        embed = discord.Embed(
            title="🏆 Copa 2026 — Painel de Configuração",
            color=0x3B82F6,
        )
        embed.add_field(
            name="📡 Notificações automáticas",
            value=(
                f"**Canal:** {canal_str}\n"
                "**Resumo diário:** 09:00 BRT (automático)\n"
                "**Alertas ao vivo:** gols, cartões, escalações, VAR"
            ),
            inline=False,
        )
        embed.add_field(
            name="📋 Comandos disponíveis",
            value=(
                "`/copa` — Todos os jogos da rodada atual\n"
                "`/copa-quando` — Em quantos minutos começa a próxima partida\n"
                "`/copa-time <seleção>` — Todos os jogos de uma seleção\n"
                "`/copa-grupo <letra>` — Jogos de um grupo (A–L)\n"
                "`/copa-artilharia` — Top artilheiros da Copa"
            ),
            inline=False,
        )
        embed.set_footer(text="Use /config-copa #canal para definir onde as notificações chegam")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CopaCog(bot))
