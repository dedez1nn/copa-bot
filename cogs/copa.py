"""Cog Copa — comandos + monitoramento ao vivo."""

import asyncio
import io
import logging
import math
import time
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from services import artilharia
from services import bracket
from services import copa as copa_svc
from services import copa_monitor as monitor
from services import gate
from services import youtube
from services.db import get_all_copa_channels, get_copa_channel, set_copa_channel

logger = logging.getLogger(__name__)

BRT = copa_svc.BRT

BRACKET_EOD_DELAY_SECS = 3600  # chaveamento postado 1h após o fim do último jogo do dia


def _score_str(m: dict) -> str:
    return copa_svc._score(m)


def _ts_str(ts: int) -> str:
    return copa_svc._ts(ts)


def _flag(en: str) -> str:
    return copa_svc.flag(en)


# ── Embeds de consulta ────────────────────────────────────────────────────────

_DAYS_PER_PAGE = 4


def _br(m: dict) -> bool:
    return copa_svc.is_brazil_match(m)


def _jogo_linha(m: dict, show_hora: bool = True) -> str:
    status = m["status"]
    icon = "🔴" if status == "inprogress" else ("✅" if status == "finished" else "🗓️")
    br = _br(m)
    hf = _flag(m["home_en"])
    af = _flag(m["away_en"])
    hora = datetime.fromtimestamp(m["date_ts"], tz=BRT).strftime("%H:%M")
    suffix = f" — {hora} BRT" if show_hora else ""
    linha = f"{icon} **{hf} {m['home_pt']}  {_score_str(m)}  {m['away_pt']} {af}**{suffix}"
    return f"🟢 {linha}" if br else linha


def _jogos_por_dia(jogos: list[dict]) -> list[tuple[str, str]]:
    """Retorna lista de (dia, valor_field) para todos os dias com jogos."""
    por_dia: dict[str, list[str]] = {}
    for m in jogos:
        dia = datetime.fromtimestamp(m["date_ts"], tz=BRT).strftime("%d/%m")
        por_dia.setdefault(dia, []).append(_jogo_linha(m))
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
        grupo = m.get("group") or m.get("stage") or ""
        base = f"{_jogo_linha(m)}  `{_ts_str(m['date_ts'])} BRT`"
        return f"{base}  *{grupo}*" if grupo else base

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


def _embed_artilharia(scorers: list[dict]) -> discord.Embed:
    embed = discord.Embed(title="🥇 Artilharia — Copa 2026", color=0x3B82F6)
    if not scorers:
        embed.description = "⏳ Nenhum gol registrado ainda."
        return embed
    lines = []
    for i, s in enumerate(scorers[:20], 1):
        team_en = copa_svc._resolve(s["team"])
        flag_emoji = _flag(team_en)
        br = team_en == "brazil"
        gols = "⚽" * min(s["goals"], 8) + (f" +{s['goals']-8}" if s["goals"] > 8 else "")
        linha = f"`{i:>2}.` {flag_emoji} **{s['name']}** — *{s['team']}* — {gols}"
        lines.append(f"🟢 {linha}" if br else linha)
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
        grupo_str = f"  *{grupo}*" if grupo else ""
        base = f"**{hora} BRT** — {_jogo_linha(m, show_hora=False)}{grupo_str}"
        lines.append(base)
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
        # Chaveamento de fim de dia: arma quando todos os jogos do dia acabam,
        # dispara 1h depois. Guardado por data para enviar só uma vez por dia.
        self._eod_armed_date: str = ""
        self._eod_due_ts: float = 0.0
        self._eod_sent_dates: set[str] = set()

    async def cog_load(self) -> None:
        self._monitor_channels = await get_all_copa_channels()
        self._monitor_loop.start()

    async def cog_unload(self) -> None:
        self._monitor_loop.cancel()

    @tasks.loop(seconds=10)
    async def _monitor_loop(self) -> None:
        try:
            await monitor.run_monitor_tick(self.bot, self._monitor_channels)
            await self._check_daily_summary()
            await self._check_end_of_day_bracket()
        except Exception:
            logger.exception("Erro no loop de monitoramento (tick ignorado)")

    @_monitor_loop.error
    async def _monitor_loop_error(self, error: Exception) -> None:
        logger.exception("Loop de monitoramento parou com erro — reiniciando", exc_info=error)
        self._monitor_loop.restart()

    @_monitor_loop.before_loop
    async def _before_monitor(self) -> None:
        await self.bot.wait_until_ready()
        logger.info("Loop de monitoramento iniciado")

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
        # Anexa ao resumo: chaveamento (com os jogos de hoje destacados) + artilharia.
        bracket_png = await self._render_bracket(highlight_today=True)
        art_png = await self._render_artilharia()
        for guild_id, channel_id in self._monitor_channels:
            ch = self.bot.get_channel(channel_id)
            if not ch:
                continue
            files = []
            if bracket_png is not None:
                files.append(discord.File(io.BytesIO(bracket_png), filename="chaveamento.png"))
            if art_png is not None:
                files.append(discord.File(io.BytesIO(art_png), filename="artilharia.png"))
            try:
                await ch.send(embed=embed, files=files)
            except Exception:
                logger.exception("Erro ao enviar resumo diário para guild %s", guild_id)

    async def _render_bracket(self, highlight_today: bool = False) -> bytes | None:
        """Gera o PNG do chaveamento (ou None em caso de falha)."""
        try:
            return await asyncio.to_thread(
                bracket.render_bracket_png, highlight_today=highlight_today)
        except Exception:
            logger.exception("Erro ao gerar imagem do chaveamento")
            return None

    async def _render_artilharia(self) -> bytes | None:
        """Gera o PNG da artilharia (ou None se não houver gols / em caso de falha)."""
        try:
            scorers = await asyncio.to_thread(copa_svc.get_scorers)
            if not scorers:
                return None
            return await asyncio.to_thread(artilharia.render_artilharia_png, scorers)
        except Exception:
            logger.exception("Erro ao gerar imagem da artilharia")
            return None

    async def _check_end_of_day_bracket(self) -> None:
        """Envia o chaveamento ~1h após o fim do último jogo do dia (1x por dia)."""
        now = time.time()
        # Já armado: aguarda o atraso de 1h e dispara uma vez.
        if self._eod_armed_date:
            if now < self._eod_due_ts:
                return
            date = self._eod_armed_date
            self._eod_armed_date = ""
            if date in self._eod_sent_dates:
                return
            self._eod_sent_dates.add(date)
            bracket_png = await self._render_bracket(highlight_today=True)
            art_png = await self._render_artilharia()
            if bracket_png is None and art_png is None:
                return
            embed = None
            if bracket_png is not None:
                embed = discord.Embed(
                    title="🗺️ Chaveamento — Fim dos jogos do dia",
                    description="Situação atual do mata-mata e artilharia.",
                    color=0xFFCD46,
                )
                embed.set_image(url="attachment://chaveamento.png")
                embed.set_footer(text="Copa do Mundo FIFA™ 2026")
                embed.timestamp = discord.utils.utcnow()
            for guild_id, channel_id in self._monitor_channels:
                ch = self.bot.get_channel(channel_id)
                if not ch:
                    continue
                files = []
                if bracket_png is not None:
                    files.append(discord.File(io.BytesIO(bracket_png), filename="chaveamento.png"))
                if art_png is not None:
                    files.append(discord.File(io.BytesIO(art_png), filename="artilharia.png"))
                try:
                    await ch.send(embed=embed, files=files)
                except Exception:
                    logger.exception("Erro ao enviar fim de dia para guild %s", guild_id)
            return

        # Não armado: arma quando todos os jogos de hoje estiverem encerrados.
        today = datetime.now(BRT).strftime("%Y-%m-%d")
        if today in self._eod_sent_dates:
            return
        try:
            jogos = await asyncio.to_thread(copa_svc.get_jogos_do_dia)
        except Exception:
            logger.exception("Erro ao buscar jogos do dia (fim de dia)")
            return
        if not jogos:
            return
        if all(j["status"] == "finished" for j in jogos):
            self._eod_armed_date = today
            self._eod_due_ts = now + BRACKET_EOD_DELAY_SECS
            logger.info("[bracket] todos os %d jogos de %s encerrados — chaveamento em +1h",
                        len(jogos), today)

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
        if scorers:
            try:
                png = await asyncio.to_thread(artilharia.render_artilharia_png, scorers)
                file = discord.File(io.BytesIO(png), filename="artilharia.png")
                await interaction.followup.send(file=file)
                return
            except Exception:
                logger.exception("Falha ao renderizar artilharia em imagem; usando embed")
        await interaction.followup.send(embed=_embed_artilharia(scorers))

    @app_commands.command(name="chaveamento", description="Imagem do chaveamento atual do mata-mata (R32→Final)")
    async def cmd_chaveamento(self, interaction: discord.Interaction) -> None:
        if not await gate.allowed(interaction):
            return
        await interaction.response.defer()
        png = await self._render_bracket()
        if png is None:
            await interaction.followup.send("❌ Não foi possível gerar o chaveamento agora.", ephemeral=True)
            return
        embed = discord.Embed(title="🗺️ Chaveamento — Copa 2026", color=0xFFCD46)
        embed.set_image(url="attachment://chaveamento.png")
        embed.set_footer(text="Copa do Mundo FIFA™ 2026")
        await interaction.followup.send(
            embed=embed,
            file=discord.File(io.BytesIO(png), filename="chaveamento.png"),
        )

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
                "`/copa-artilharia` — Top artilheiros da Copa\n"
                "`/chaveamento` — Imagem do chaveamento do mata-mata"
            ),
            inline=False,
        )
        embed.set_footer(text="Use /config-copa #canal para definir onde as notificações chegam")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CopaCog(bot))
