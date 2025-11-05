import discord
from discord.ext import commands
import asyncio
import logging
from django.conf import settings
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist
from asgiref.sync import sync_to_async
from datetime import datetime
from .models import DiscordServer, DiscordChannel, DiscordUser, DiscordMessage, DiscordReaction

logger = logging.getLogger(__name__)


class DiscordIntelligenceBot(commands.Bot):
    """Discord bot for monitoring and storing server activity"""
    
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        intents.reactions = True
        
        super().__init__(command_prefix='!', intents=intents)
    
    async def on_ready(self):
        """Called when the bot is ready"""
        logger.info(f'{self.user} has connected to Discord!')
        
        # Sync guilds and channels to database
        await self.sync_guild_data()
    
    async def on_message(self, message):
        """Handle incoming messages"""
        if message.author.bot:
            return
        
        await self.store_message(message)
        await self.process_commands(message)
    
    async def on_message_edit(self, before, after):
        """Handle message edits"""
        if after.author.bot:
            return
        
        try:
            discord_message = await sync_to_async(DiscordMessage.objects.get)(message_id=after.id)
            discord_message.content = after.content
            discord_message.edited_timestamp = after.edited_at
            await sync_to_async(discord_message.save)()
            logger.info(f"Updated message {after.id}")
        except ObjectDoesNotExist:
            logger.warning(f"Message {after.id} not found for edit")
    
    async def on_message_delete(self, message):
        """Handle message deletions"""
        if message.author.bot:
            return
        
        try:
            await sync_to_async(DiscordMessage.objects.filter(message_id=message.id).delete)()
            logger.info(f"Deleted message {message.id}")
        except Exception as e:
            logger.error(f"Error deleting message {message.id}: {e}")
    
    async def on_reaction_add(self, reaction, user):
        """Handle reaction additions"""
        if user.bot:
            return
        
        await self.store_reaction(reaction, user)
    
    async def on_reaction_remove(self, reaction, user):
        """Handle reaction removals"""
        if user.bot:
            return
        
        await self.update_reaction_count(reaction)
    
    async def sync_guild_data(self):
        """Sync guild, channel, and user data to database"""
        for guild in self.guilds:
            await self.store_guild(guild)
            
            for channel in guild.channels:
                await self.store_channel(channel, guild)
            
            for member in guild.members:
                await self.store_user(member)
    
    async def store_guild(self, guild):
        """Store guild information"""
        server, created = await sync_to_async(DiscordServer.objects.get_or_create)(
            server_id=guild.id,
            defaults={'name': guild.name}
        )
        if not created and server.name != guild.name:
            server.name = guild.name
            await sync_to_async(server.save)()
        
        logger.info(f"{'Created' if created else 'Updated'} guild: {guild.name}")
    
    async def store_channel(self, channel, guild):
        """Store channel information"""
        if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel)):
            return
        
        server = await sync_to_async(DiscordServer.objects.get)(server_id=guild.id)
        channel_type = channel.type.name if hasattr(channel.type, 'name') else str(channel.type)
        
        discord_channel, created = await sync_to_async(DiscordChannel.objects.get_or_create)(
            channel_id=channel.id,
            defaults={
                'server': server,
                'name': channel.name,
                'channel_type': channel_type
            }
        )
        
        if not created and (discord_channel.name != channel.name or discord_channel.channel_type != channel_type):
            discord_channel.name = channel.name
            discord_channel.channel_type = channel_type
            await sync_to_async(discord_channel.save)()
        
        logger.info(f"{'Created' if created else 'Updated'} channel: #{channel.name}")
    
    async def store_user(self, member):
        """Store user information"""
        discriminator = member.discriminator if member.discriminator != '0' else ''
        
        user, created = await sync_to_async(DiscordUser.objects.get_or_create)(
            user_id=member.id,
            defaults={
                'username': member.name,
                'display_name': member.display_name,
                'discriminator': discriminator,
                'avatar_url': str(member.avatar.url) if member.avatar else '',
                'is_bot': member.bot
            }
        )
        
        if not created:
            updated = False
            if user.username != member.name:
                user.username = member.name
                updated = True
            if user.display_name != member.display_name:
                user.display_name = member.display_name
                updated = True
            if user.avatar_url != (str(member.avatar.url) if member.avatar else ''):
                user.avatar_url = str(member.avatar.url) if member.avatar else ''
                updated = True
            
            if updated:
                await sync_to_async(user.save)()
    
    async def store_message(self, message):
        """Store message information"""
        try:
            # Get or create channel
            channel = await self.get_or_create_channel(message.channel)
            
            # Get or create user
            user = await self.get_or_create_user(message.author)
            
            # Create message
            discord_message, created = await sync_to_async(DiscordMessage.objects.get_or_create)(
                message_id=message.id,
                defaults={
                    'channel': channel,
                    'author': user,
                    'content': message.content,
                    'timestamp': message.created_at,
                    'edited_timestamp': message.edited_at,
                    'is_pinned': message.pinned,
                    'has_attachments': len(message.attachments) > 0,
                    'attachment_count': len(message.attachments),
                    'has_embeds': len(message.embeds) > 0,
                    'embed_count': len(message.embeds)
                }
            )
            
            if created:
                logger.info(f"Stored message from {user.username} in #{channel.name}")
            
            # Store reactions
            for reaction in message.reactions:
                await self.store_reaction(reaction, None)
        
        except Exception as e:
            logger.error(f"Error storing message {message.id}: {e}")
    
    async def get_or_create_channel(self, channel):
        """Get or create channel in database"""
        try:
            return await sync_to_async(DiscordChannel.objects.get)(channel_id=channel.id)
        except ObjectDoesNotExist:
            await self.store_channel(channel, channel.guild)
            return await sync_to_async(DiscordChannel.objects.get)(channel_id=channel.id)
    
    async def get_or_create_user(self, member):
        """Get or create user in database"""
        try:
            return await sync_to_async(DiscordUser.objects.get)(user_id=member.id)
        except ObjectDoesNotExist:
            await self.store_user(member)
            return await sync_to_async(DiscordUser.objects.get)(user_id=member.id)
    
    async def store_reaction(self, reaction, user):
        """Store reaction information"""
        try:
            message = await sync_to_async(DiscordMessage.objects.get)(message_id=reaction.message.id)
            
            reaction_obj, created = await sync_to_async(DiscordReaction.objects.get_or_create)(
                message=message,
                emoji_name=reaction.emoji.name if reaction.emoji.name else str(reaction.emoji),
                emoji_id=reaction.emoji.id if hasattr(reaction.emoji, 'id') else None,
                defaults={'count': reaction.count}
            )
            
            if not created:
                reaction_obj.count = reaction.count
                await sync_to_async(reaction_obj.save)()
        
        except ObjectDoesNotExist:
            logger.warning(f"Message {reaction.message.id} not found for reaction")
        except Exception as e:
            logger.error(f"Error storing reaction: {e}")
    
    async def update_reaction_count(self, reaction):
        """Update reaction count when removed"""
        try:
            message = await sync_to_async(DiscordMessage.objects.get)(message_id=reaction.message.id)
            
            reaction_obj = await sync_to_async(DiscordReaction.objects.get)(
                message=message,
                emoji_name=reaction.emoji.name if reaction.emoji.name else str(reaction.emoji),
                emoji_id=reaction.emoji.id if hasattr(reaction.emoji, 'id') else None
            )
            
            reaction_obj.count = reaction.count
            await sync_to_async(reaction_obj.save)()
        
        except ObjectDoesNotExist:
            pass
        except Exception as e:
            logger.error(f"Error updating reaction count: {e}")


# Global bot instance
bot = None


async def start_bot():
    """Start the Discord bot"""
    global bot
    
    if not settings.DISCORD_BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN not set in environment variables")
        return
    
    bot = DiscordIntelligenceBot()
    
    try:
        await bot.start(settings.DISCORD_BOT_TOKEN)
    except Exception as e:
        logger.error(f"Error starting bot: {e}")


def run_bot():
    """Run the bot in the event loop"""
    asyncio.run(start_bot())
