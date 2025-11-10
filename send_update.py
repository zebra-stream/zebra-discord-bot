#!/usr/bin/env python3
import asyncio
import discord
import os
from dotenv import load_dotenv

load_dotenv()

async def send():
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        channel = client.get_channel(1429419794230411347)  # #general

        msg = """🦓 **YO! Context Docs Just Got REAL** 🦓

not gonna lie, our docs were lowkey sus 💀

**What Happened:**
Claude Code did a deep dive into Discord + Git history and was like "wait... docs say Phase 1 done ✅ but y'all talking about janky layouts??"

**The Tea:**
- Docs: "Everything works! ✅"
- Reality: Multi-panel janky, guests untested, X OAuth broken, streams not saving
- Friday test stream and we got WORK TO DO fam

**What's Fixed:**
✅ STATUS.md updated with 8 critical issues
✅ Added "Open Questions for Friday Test Stream"
✅ DISCORD_INTEGRATION_GUIDE.md for Rex
✅ **HUGE:** Discord bot + Zebrastream now share PostgreSQL database!

**Database Glow-Up:**
Both repos now hit same PostgreSQL (zebrastream_dev on leviathan-db). Rex's agent can query our Discord messages without setup. Single source of truth fr fr.

**Next:** Rex check the new guide so your agent can stay synced too!

stay zebra 🦓✨"""

        await channel.send(msg)
        print('✅ Message sent!')
        await client.close()

    await client.start(os.getenv('DISCORD_BOT_TOKEN'))

asyncio.run(send())
