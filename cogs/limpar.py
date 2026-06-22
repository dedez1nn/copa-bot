from discord import app_commands
import discord
from discord.ext import commands

class ClearMessages(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="limpar", description="Apaga o número de mensagens desejado do canal")
    @app_commands.describe(mensagens="Número de mensagens para apagar")
    async def clear(self, interaction: discord.Interaction, mensagens: int):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Você não tem permissão para usar esse comando!", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        deleted = await interaction.channel.purge(limit=mensagens)
        await interaction.followup.send(f"✅ {len(deleted)} mensagens apagadas!", ephemeral=True)

async def setup(bot):
    await bot.add_cog(ClearMessages(bot))