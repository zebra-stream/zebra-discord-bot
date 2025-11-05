import discord
from discord.ext import commands
import asyncio
import logging
from django.conf import settings
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist
from asgiref.sync import sync_to_async
from datetime import datetime, timedelta, timezone
from .models import DiscordServer, DiscordChannel, DiscordUser, DiscordMessage, DiscordReaction
from typing import Optional

logger = logging.getLogger(__name__)


class SummaryCog(commands.Cog):
    """Cog for summary/recap commands"""
    
    def __init__(self, bot):
        self.bot = bot
    
    @commands.command(name='summary', aliases=['recap'])
    async def summary_command(self, ctx, *args):
        """
        Generate a fun, influencer-style summary of recent conversation in this channel 🦓
        
        Usage:
            !summary              - Summarize last 50 messages
            !summary 24           - Summarize messages from last 24 hours
            !summary 24 100       - Summarize last 100 messages from last 24 hours
        """
        async with ctx.typing():
            try:
                # Parse arguments
                hours = None
                limit = None
                
                if len(args) >= 1:
                    try:
                        hours = int(args[0])
                    except ValueError:
                        await ctx.send("🦓 **Oops!** The hours parameter should be a number. Usage: `!summary [hours] [limit]`")
                        return
                
                if len(args) >= 2:
                    try:
                        limit = int(args[1])
                    except ValueError:
                        await ctx.send("🦓 **Oops!** The limit parameter should be a number. Usage: `!summary [hours] [limit]`")
                        return
                
                # Get the channel from database
                channel = await self.bot.get_or_create_channel(ctx.channel)
                
                # Determine time range
                if hours:
                    time_cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
                else:
                    time_cutoff = None
                
                # Fetch messages
                def get_messages():
                    queryset = DiscordMessage.objects.filter(
                        channel=channel
                    ).exclude(
                        author__is_bot=True
                    ).select_related('author').order_by('-timestamp')
                    
                    if time_cutoff:
                        queryset = queryset.filter(timestamp__gte=time_cutoff)
                    
                    if limit:
                        queryset = queryset[:limit]
                    else:
                        queryset = queryset[:50]  # Default to 50 messages
                    
                    return list(queryset)
                
                messages = await sync_to_async(get_messages)()

                if not messages:
                    await ctx.send("🦓 **Hey there!** 👋 No messages found in this channel to summarize. Maybe try a different time range?")
                    return

                # Build conversation text and count unique authors from actual messages
                conversation_text = []
                unique_authors = set()
                for msg in reversed(messages):  # Reverse to get chronological order
                    author_name = msg.author.display_name or msg.author.username
                    unique_authors.add(author_name)  # Count from actual message objects
                    timestamp_str = msg.timestamp.strftime("%H:%M") if msg.timestamp else ""
                    content = msg.content.strip()
                    if content:
                        conversation_text.append(f"[{timestamp_str}] {author_name}: {content}")

                if not conversation_text:
                    await ctx.send("🦓 **Oops!** No text messages found to summarize. Everyone was just sharing images and files! 📸")
                    return

                full_conversation = "\n".join(conversation_text)

                # Generate summary using OpenAI - pass actual author count
                summary = await self.bot.generate_influencer_summary(full_conversation, len(messages), len(unique_authors))
                
                # Send summary
                embed = discord.Embed(
                    title="🦓 **Zebra Stream Recap** 🦓",
                    description=summary,
                    color=0x000000  # Black and white like a zebra!
                )
                embed.set_footer(text=f"Summarized {len(messages)} messages from #{ctx.channel.name}")
                
                await ctx.send(embed=embed)
                
            except Exception as e:
                logger.error(f"Error generating summary: {e}")
                await ctx.send(f"🦓 **Oops!** Something went wrong while creating the summary. Error: {str(e)}")


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
        
        # Load the summary command Cog
        try:
            await self.add_cog(SummaryCog(self))
            logger.info('SummaryCog loaded successfully')
        except Exception as e:
            logger.error(f'Error loading SummaryCog: {e}')
        
        # Log registered commands for debugging (after Cog is loaded)
        logger.info(f'Registered commands: {[cmd.name for cmd in self.commands]}')
    
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
    
    async def generate_influencer_summary(self, conversation: str, message_count: int, author_count: int = None) -> str:
        """
        Generate an influencer-style summary using OpenAI API
        Falls back to basic summary if OpenAI is not configured
        """
        # Debug: Check if API key is loaded
        api_key_present = bool(settings.OPENAI_API_KEY)
        logger.info(f'OpenAI API key present: {api_key_present}')
        
        if not settings.OPENAI_API_KEY:
            logger.warning('OPENAI_API_KEY not set in settings, using basic summary')
            # Fallback: Basic summary without AI
            return self._generate_basic_summary(conversation, message_count, author_count)
        
        try:
            from openai import AsyncOpenAI
            
            # Initialize OpenAI client with explicit base_url
            # Explicitly set to ensure correct endpoint (some SDK versions may have issues)
            client = AsyncOpenAI(
                api_key=settings.OPENAI_API_KEY,
                base_url="https://api.openai.com/v1",
                timeout=30.0
            )
            
            logger.info(f"OpenAI client base_url: {client.base_url}")
            logger.info("Attempting to call OpenAI API...")
            
            prompt = f"""You are a successful influencer Zebra 🦓 who loves to summarize Discord conversations in a fun, engaging, and entertaining way. 

Your personality:
- Energetic and enthusiastic
- Uses emojis naturally (especially 🦓)
- Makes things sound exciting and interesting
- Uses modern influencer language (but keep it PG)
- Highlights the most interesting parts of the conversation
- Makes it feel like you're recapping something epic

Here's a Discord conversation from the last {message_count} messages with {author_count or 'several'} people. Create a fun, engaging summary in the style of a successful influencer Zebra:

{conversation[:8000]}  # Limit to avoid token limits

Create a summary that's:
- 2-4 paragraphs long
- Engaging and fun to read
- Highlights key topics and interesting moments
- Uses your influencer Zebra personality 🦓
- Ends with something encouraging or positive

Start with something catchy and energetic!"""
            
            # Use chat completions endpoint per OpenAI API documentation
            response = await client.chat.completions.create(
                model="gpt-4o-mini",  # Using mini for cost efficiency
                messages=[
                    {"role": "system", "content": "You are a successful influencer Zebra who loves to summarize Discord conversations in a fun, engaging way. You use emojis naturally and make everything sound exciting!"},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=500,
                temperature=0.8
            )
            
            summary = response.choices[0].message.content.strip()
            return summary
            
        except ImportError:
            logger.warning("OpenAI package not installed, using basic summary")
            return self._generate_basic_summary(conversation, message_count, author_count)
        except Exception as e:
            error_msg = str(e)
            error_type = type(e).__name__
            logger.error(f"Error calling OpenAI API: {error_msg}")
            logger.error(f"Error type: {error_type}")
            
            # Try to get more details from the error object
            try:
                if hasattr(e, 'response') and e.response:
                    logger.error(f"Full error response: {e.response}")
                if hasattr(e, 'body') and e.body:
                    logger.error(f"Error body: {e.body}")
                if hasattr(e, 'status_code'):
                    logger.error(f"Status code: {e.status_code}")
            except:
                pass
            
            # Check for quota/billing issues
            if "quota" in error_msg.lower() or "insufficient_quota" in error_msg.lower() or "429" in error_msg:
                logger.error("=" * 60)
                logger.error("OPENAI QUOTA/BILLING ISSUE DETECTED")
                logger.error(f"Error details: {error_msg}")
                logger.error("")
                logger.error("NOTE: Even if your dashboard shows budget available, you may need:")
                logger.error("1. Verify your payment method is active and valid")
                logger.error("2. Check for account-level soft limits (separate from project budget)")
                logger.error("3. Ensure the API key belongs to the same project with the budget")
                logger.error("4. Check account verification status at https://platform.openai.com/account")
                logger.error("5. Try regenerating your API key at https://platform.openai.com/api-keys")
                logger.error("")
                logger.error("Common causes:")
                logger.error("- Payment method needs verification despite having budget")
                logger.error("- Account has soft limits enabled")
                logger.error("- API key is from a different project than the budget")
                logger.error("=" * 60)
            
            # Check if it's a 404 or nginx error (proxy/firewall issue)
            elif "404" in error_msg or "Not Found" in error_msg or "nginx" in error_msg.lower():
                logger.error("=" * 60)
                logger.error("NETWORK/PROXY ISSUE DETECTED")
                logger.error("The nginx 404 error indicates a proxy or firewall is blocking OpenAI API requests.")
                logger.error("This is NOT a code issue - it's a network infrastructure problem.")
                logger.error("Possible solutions:")
                logger.error("1. Check if you're behind a corporate firewall/proxy")
                logger.error("2. Try using a VPN or different network")
                logger.error("3. Contact your network administrator about allowing api.openai.com")
                logger.error("4. Check if there's a proxy configuration blocking the requests")
                logger.error("=" * 60)
            
            # Check if it's a quota issue for user-facing message
            is_quota_issue = "quota" in error_msg.lower() or "insufficient_quota" in error_msg.lower()
            
            # Still return basic summary but indicate API key was set and the specific issue
            return self._generate_basic_summary(
                conversation, 
                message_count, 
                author_count, 
                api_key_was_set=True, 
                quota_issue=is_quota_issue
            )
    
    def _generate_basic_summary(self, conversation: str, message_count: int, author_count: int = None, api_key_was_set: bool = False, quota_issue: bool = False) -> str:
        """Generate a basic summary without AI"""
        # Use provided author_count if available, otherwise try to parse from conversation
        if author_count is None:
            lines = conversation.split('\n')
            unique_authors = set()
            for line in lines:
                if ':' in line:
                    author = line.split(':')[0].split('] ')[-1] if '] ' in line else line.split(':')[0]
                    unique_authors.add(author.strip())
            author_count = len(unique_authors)
        
        base_message = f"""🦓 **Hey everyone!** 👋 Just caught up on the conversation and wow, there's been some action! 

We had **{message_count} messages** with **{author_count} different people** chiming in. The conversation covered a bunch of topics - definitely some interesting discussions happening!"""
        
        if api_key_was_set:
            if quota_issue:
                return f"""{base_message}

⚠️ **AI Summary Unavailable**: Even though your dashboard shows budget available, OpenAI is reporting insufficient quota. This usually means:
• Payment method needs verification (check https://platform.openai.com/account/billing)
• Account-level soft limits are blocking requests (separate from project budget)
• API key might be from a different project than your budget
• Try regenerating your API key at https://platform.openai.com/api-keys

Check the bot logs for detailed error info! 🦓✨"""
            else:
                return f"""{base_message}

⚠️ **AI Summary Unavailable**: Your API key is configured, but OpenAI API requests are failing. Check your network settings or OpenAI API status. 🦓✨"""
        else:
            return f"""{base_message}

Want a more detailed AI-powered summary? Set up your OPENAI_API_KEY in the .env file and I'll give you the full influencer Zebra treatment! 🦓✨"""
    
    async def assign_admin_role(self, user_id, guild_id=None, reason=None):
        """
        Assign admin role to a user
        
        Args:
            user_id: Discord user ID to assign the role to
            guild_id: Optional guild ID (uses DISCORD_GUILD_ID from settings if not provided)
            reason: Optional reason for the role assignment
        
        Returns:
            tuple: (success: bool, message: str)
        """
        try:
            # Get guild ID
            if not guild_id:
                if not settings.DISCORD_GUILD_ID:
                    return False, "DISCORD_GUILD_ID not set in settings"
                guild_id = int(settings.DISCORD_GUILD_ID)
            else:
                guild_id = int(guild_id)
            
            # Get the guild
            guild = self.get_guild(guild_id)
            if not guild:
                return False, f"Guild {guild_id} not found"
            
            # Get the member
            member = guild.get_member(user_id)
            if not member:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.NotFound:
                    return False, f"User {user_id} not found in guild"
            
            # Find admin role
            admin_role = None
            
            # First, try to find role by name (case-insensitive)
            for role in guild.roles:
                if role.name.lower() in ['admin', 'administrator']:
                    admin_role = role
                    break
            
            # If not found by name, find role with administrator permissions
            if not admin_role:
                for role in guild.roles:
                    if role.permissions.administrator:
                        admin_role = role
                        break
            
            if not admin_role:
                return False, "Admin role not found in guild"
            
            # Check if user already has the role
            if admin_role in member.roles:
                return True, f"{member.display_name} already has the {admin_role.name} role"
            
            # Assign the role
            await member.add_roles(admin_role, reason=reason or 'Admin role assignment')
            logger.info(f"Assigned {admin_role.name} role to {member.display_name} (ID: {user_id})")
            return True, f"Successfully assigned {admin_role.name} role to {member.display_name}"
            
        except discord.Forbidden:
            return False, "Bot does not have permission to assign roles"
        except discord.HTTPException as e:
            return False, f"Discord API error: {e}"
        except Exception as e:
            logger.error(f"Error assigning admin role: {e}")
            return False, f"Error: {e}"


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
