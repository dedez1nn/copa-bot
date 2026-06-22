"""Cog Fenrir — define o canal onde os comandos do bot são permitidos."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from services import gate
from services.db import (
    get_all_command_channels,
    get_command_channel,
    remove_command_channel,
    set_command_channel,
)

logger = logging.getLogger(__name__)


class FenrirCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        channels = await get_all_command_channels()
        gate.load(channels)
        logger.info("Gate de canal carregado: %d guild(s) configurada(s)", len(channels))

    @app_commands.command(
        name="canal-fenrir",
        description="Define o canal onde os comandos do bot podem ser usados (apenas admins)",
    )
    @app_commands.describe(canal="Canal permitido; omita para liberar em todos os canais")
    @app_commands.default_permissions(administrator=True)
    async def cmd_canal_fenrir(
        self, interaction: discord.Interaction, canal: discord.TextChannel | None = None
    ) -> None:
        guild_id = interaction.guild_id

        if canal:
            await set_command_channel(guild_id, canal.id)
            gate.set_channel(guild_id, canal.id)
            embed = discord.Embed(
                title="✅ Canal de comandos configurado",
                description=f"Comandos do bot restritos a {canal.mention}.\nComandos de administração continuam funcionando em qualquer canal.",
                color=0x3B82F6,
            )
        else:
            await remove_command_channel(guild_id)
            gate.remove_channel(guild_id)
            embed = discord.Embed(
                title="✅ Restrição removida",
                description="Comandos do bot liberados em todos os canais.",
                color=0x3B82F6,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FenrirCog(bot))
