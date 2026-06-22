from discord import app_commands
import discord
from discord.ext import commands


class ClearMessages(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="limpar", description="Apaga mensagens do canal (máx. 100)")
    @app_commands.describe(mensagens="Número de mensagens para apagar (1–100)")
    @app_commands.default_permissions(manage_messages=True)
    async def clear(self, interaction: discord.Interaction, mensagens: int):
        if mensagens < 1 or mensagens > 100:
            await interaction.response.send_message(
                "❌ Informe um número entre 1 e 100.", ephemeral=True
            )
            return

        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
            channel = self.bot.get_channel(interaction.channel_id)

        if channel is None or not hasattr(channel, "purge"):
            await interaction.response.send_message(
                "❌ Não é possível apagar mensagens neste canal.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            deleted = await channel.purge(limit=mensagens)
            await interaction.followup.send(
                f"✅ {len(deleted)} mensagem{'s' if len(deleted) != 1 else ''} apagada{'s' if len(deleted) != 1 else ''}!",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Sem permissão para apagar mensagens aqui.", ephemeral=True
            )
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"❌ Erro ao apagar mensagens: {e}", ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(ClearMessages(bot))
