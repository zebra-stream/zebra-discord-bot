#!/usr/bin/env python3
"""Check if dashboard query works correctly"""
import os
import sys
import django
from pathlib import Path
from datetime import timedelta
from django.utils import timezone

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'discord_intelligence.settings')
django.setup()

from bot.models import DiscordMessage
from django.conf import settings

print('🔍 Database Configuration:')
print(f'  USE_POSTGRESQL: {settings.USE_POSTGRESQL}')
print(f'  Database Engine: {settings.DATABASES["default"]["ENGINE"]}')
print(f'  Database Name: {settings.DATABASES["default"].get("NAME", "N/A")}')

print('\n📊 Dashboard Query Test (same as dashboard/views.py):')
recent_cutoff = timezone.now() - timedelta(hours=24)
recent_messages = DiscordMessage.objects.filter(
    timestamp__gte=recent_cutoff
).select_related('author', 'channel', 'channel__server').order_by('-timestamp')[:50]

print(f'  Recent messages (last 24h): {recent_messages.count()}')
if recent_messages.count() > 0:
    print('  Messages found:')
    for msg in recent_messages:
        print(f'    - {msg.timestamp}: {msg.content[:40]}...')
else:
    print('  ❌ No messages found!')
    print(f'  Current time: {timezone.now()}')
    print(f'  Cutoff time: {recent_cutoff}')
    
    # Check all messages
    all_messages = DiscordMessage.objects.all()
    print(f'\n  All messages in DB: {all_messages.count()}')
    for msg in all_messages:
        print(f'    - {msg.timestamp} (age: {(timezone.now() - msg.timestamp).total_seconds()/3600:.1f}h)')

