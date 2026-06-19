"""Cog Copa — comandos + monitoramento ao vivo."""

import asyncio
import logging
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from services import copa as copa_svc
from services import copa_monitor as monitor
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

def _embed_jogos_rodada(jogos: list[dict]) -> discord.Embed:
    embed = discord.Embed(
        title="🏆 Copa 2026 — Jogos da Rodada",
        color=0x3B82F6,
    )
    if not jogos:
        embed.description = "Nenhum jogo nas próximas 48h ou últimas 48h."
        return embed

    linhas = []
    for m in jogos:
        status = m["status"]
        if status == "inprogress":
            icon = "🔴"
        elif status == "finished":
            icon = "✅"
        else:
            icon = "🗓️"
        hf = _flag(m["home_en"])
        af = _flag(m["away_en"])
        linhas.append(
            f"{icon} **{hf} {m['home_pt']}  {_score_str(m)}  {m['away_pt']} {af}**"
            f"  —  {_ts_str(m['date_ts'])} BRT"
        )
    embed.description = "\n".join(linhas)
    embed.set_footer(text="Use /copa-time <seleção> para detalhes de um time")
    return embed


def _embed_team(team_query: str, matches: list[dict]) -> discord.Embed:
    t_en = copa_svc._resolve(team_query)
    pt_name = copa_svc.SS_TO_PT.get(t_en, team_query.title())
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


def _embed_grupo(letter: str, sg: dict | None, gm: list[dict]) -> discord.Embed:
    embed = discord.Embed(
        title=f"🏆 Copa 2026 — Grupo {letter.upper()}",
        color=0x3B82F6,
    )
    if sg:
        rows = sg.get("rows", [])
        lines = ["```", f"{'#':<3} {'Time':<22} {'PJ':<3} {'V':<3} {'E':<3} {'D':<3} {'GP':<3} {'GC':<3} {'SG':<4} Pts", "─" * 52]
        for row in rows:
            pos = row.get("position", "-")
            tname = copa_svc.SS_TO_PT.get(row.get("team", {}).get("name", "?").lower(),
                                           row.get("team", {}).get("name", "?"))
            pj = row.get("matches", 0)
            w, d, l = row.get("wins", 0), row.get("draws", 0), row.get("losses", 0)
            gf, gc = row.get("scoresFor", 0), row.get("scoresAgainst", 0)
            pts = row.get("points", 0)
            lines.append(f"{pos:<3} {tname:<22} {pj:<3} {w:<3} {d:<3} {l:<3} {gf:<3} {gc:<3} {gf-gc:<+4} {pts}")
        lines.append("```")
        embed.add_field(name="Tabela", value="\n".join(lines), inline=False)

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

        linhas = [f"📅 **Jogos de hoje — Copa 2026**\n"]
        for m in sorted(jogos, key=lambda x: x["date_ts"]):
            hora = datetime.fromtimestamp(m["date_ts"], tz=BRT).strftime("%H:%M")
            grupo = m.get("group") or m.get("stage") or ""
            hf = _flag(m["home_en"])
            af = _flag(m["away_en"])
            linhas.append(f"⚽ **{hf} {m['home_pt']} x {m['away_pt']} {af}**  {hora} BRT  *{grupo}*")

        content = "\n".join(linhas)
        for guild_id, channel_id in self._monitor_channels:
            ch = self.bot.get_channel(channel_id)
            if ch:
                try:
                    await ch.send(content)
                except Exception:
                    logger.exception("Erro ao enviar resumo diário para guild %s", guild_id)

    # ── Slash commands ────────────────────────────────────────────────────────

    @app_commands.command(name="copa", description="Mostra os jogos da Copa 2026 na janela de 48h")
    async def cmd_copa(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            jogos = await asyncio.to_thread(copa_svc.get_jogos_rodada)
        except Exception:
            await interaction.followup.send("❌ Erro ao buscar jogos. Tente novamente.", ephemeral=True)
            return
        embed = _embed_jogos_rodada(jogos)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="copa-time", description="Jogos de uma seleção na Copa 2026")
    @app_commands.describe(selecao="Nome da seleção (ex: Brasil, Argentina, França)")
    async def cmd_copa_time(self, interaction: discord.Interaction, selecao: str) -> None:
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
        await interaction.response.defer()
        if len(letra) != 1 or letra.upper() not in "ABCDEFGHIJKL":
            await interaction.followup.send("❌ Informe uma letra de grupo válida (A–L).", ephemeral=True)
            return
        try:
            sg, gm = await asyncio.to_thread(copa_svc.get_group_data, letra)
        except Exception:
            await interaction.followup.send("❌ Erro ao buscar dados. Tente novamente.", ephemeral=True)
            return
        embed = _embed_grupo(letra, sg, gm)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="copa-artilharia", description="Artilheiros da Copa 2026")
    async def cmd_copa_artilharia(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            scorers = await asyncio.to_thread(copa_svc.get_scorers)
        except Exception:
            await interaction.followup.send("❌ Erro ao buscar artilheiros.", ephemeral=True)
            return
        embed = _embed_artilharia(scorers)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="config-copa", description="Define o canal para notificações da Copa (apenas admins)")
    @app_commands.describe(canal="Canal onde as notificações serão enviadas")
    @app_commands.default_permissions(administrator=True)
    async def cmd_config_copa(self, interaction: discord.Interaction, canal: discord.TextChannel) -> None:
        await set_copa_channel(interaction.guild_id, canal.id)
        self._monitor_channels = await get_all_copa_channels()
        await interaction.response.send_message(
            f"✅ Notificações da Copa configuradas para {canal.mention}.", ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CopaCog(bot))
