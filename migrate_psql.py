#!/usr/bin/env python3
"""
Standalone script to migrate data from SQLite to PostgreSQL.

Usage:
    python migrate_psql.py
"""
import os
import sys
import django
from pathlib import Path

# Add the project directory to Python path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'discord_intelligence.settings')
django.setup()

# Now we can import Django models
from django.db import connections, transaction
from django.conf import settings
from bot.models import DiscordServer, DiscordChannel, DiscordUser, DiscordMessage, DiscordReaction


def migrate_data(sqlite_path=None, dry_run=False):
    """Migrate all data from SQLite to PostgreSQL"""
    
    # Determine SQLite path
    if not sqlite_path:
        sqlite_path = BASE_DIR / 'db.sqlite3'
    else:
        sqlite_path = Path(sqlite_path)
    
    if not sqlite_path.exists():
        print(f'❌ SQLite database not found at: {sqlite_path}')
        return
    
    print(f'✅ Found SQLite database at: {sqlite_path}')
    
    # Check if PostgreSQL is configured
    if not settings.USE_POSTGRESQL:
        print('❌ PostgreSQL is not enabled. Set USE_POSTGRESQL=True in your .env file.')
        return
    
    # Store original database config
    original_db_config = settings.DATABASES['default'].copy()
    
    # Temporarily switch to SQLite to read data
    settings.DATABASES['default'] = {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': str(sqlite_path),
    }
    
    # Close existing connections and reconnect
    connections.close_all()
    
    try:
        # Count records in SQLite
        print('\n📊 Counting records in SQLite database...')
        sqlite_servers = DiscordServer.objects.count()
        sqlite_channels = DiscordChannel.objects.count()
        sqlite_users = DiscordUser.objects.count()
        sqlite_messages = DiscordMessage.objects.count()
        sqlite_reactions = DiscordReaction.objects.count()
        
        print(f'  Servers: {sqlite_servers}')
        print(f'  Channels: {sqlite_channels}')
        print(f'  Users: {sqlite_users}')
        print(f'  Messages: {sqlite_messages}')
        print(f'  Reactions: {sqlite_reactions}')
        
        if dry_run:
            print('\n🔍 DRY RUN - No data will be migrated')
            return
        
        # Read all data from SQLite into memory
        print('\n📖 Reading data from SQLite...')
        
        # Read Servers
        servers_data = []
        for server in DiscordServer.objects.all():
            servers_data.append({
                'server_id': server.server_id,
                'name': server.name,
                'created_at': server.created_at,
                'updated_at': server.updated_at,
            })
        
        # Read Channels (with server_id reference)
        channels_data = []
        for channel in DiscordChannel.objects.select_related('server').all():
            channels_data.append({
                'channel_id': channel.channel_id,
                'server_id': channel.server.server_id,  # Store server_id for lookup
                'name': channel.name,
                'channel_type': channel.channel_type,
                'created_at': channel.created_at,
                'updated_at': channel.updated_at,
            })
        
        # Read Users
        users_data = []
        for user in DiscordUser.objects.all():
            users_data.append({
                'user_id': user.user_id,
                'username': user.username,
                'display_name': user.display_name,
                'discriminator': user.discriminator,
                'avatar_url': user.avatar_url,
                'is_bot': user.is_bot,
                'created_at': user.created_at,
                'updated_at': user.updated_at,
            })
        
        # Read Messages (with channel_id and user_id references)
        messages_data = []
        for message in DiscordMessage.objects.select_related('channel', 'author').all():
            messages_data.append({
                'message_id': message.message_id,
                'channel_id': message.channel.channel_id,  # Store channel_id for lookup
                'user_id': message.author.user_id,  # Store user_id for lookup
                'content': message.content,
                'timestamp': message.timestamp,
                'edited_timestamp': message.edited_timestamp,
                'is_pinned': message.is_pinned,
                'has_attachments': message.has_attachments,
                'attachment_count': message.attachment_count,
                'has_embeds': message.has_embeds,
                'embed_count': message.embed_count,
                'created_at': message.created_at,
            })
        
        # Read Reactions (with message_id reference)
        reactions_data = []
        for reaction in DiscordReaction.objects.select_related('message').all():
            reactions_data.append({
                'message_id': reaction.message.message_id,  # Store message_id for lookup
                'emoji_name': reaction.emoji_name,
                'emoji_id': reaction.emoji_id,
                'count': reaction.count,
                'created_at': reaction.created_at,
            })
        
        print(f'  ✓ Read {len(servers_data)} servers')
        print(f'  ✓ Read {len(channels_data)} channels')
        print(f'  ✓ Read {len(users_data)} users')
        print(f'  ✓ Read {len(messages_data)} messages')
        print(f'  ✓ Read {len(reactions_data)} reactions')
        
    finally:
        # Switch back to PostgreSQL
        settings.DATABASES['default'] = original_db_config
        connections.close_all()
    
    # Check PostgreSQL counts
    print('\n📊 Checking PostgreSQL database...')
    pg_servers = DiscordServer.objects.count()
    pg_channels = DiscordChannel.objects.count()
    pg_users = DiscordUser.objects.count()
    pg_messages = DiscordMessage.objects.count()
    pg_reactions = DiscordReaction.objects.count()
    
    print(f'  Servers: {pg_servers}')
    print(f'  Channels: {pg_channels}')
    print(f'  Users: {pg_users}')
    print(f'  Messages: {pg_messages}')
    print(f'  Reactions: {pg_reactions}')
    
    if pg_messages > 0:
        print('\n⚠️  PostgreSQL already has data. Will import only missing records (safe to run).')
    
    # Now write to PostgreSQL
    print('\n🚀 Starting migration to PostgreSQL...\n')
    
    # Track statistics
    stats = {
        'servers': {'imported': 0, 'skipped': 0},
        'channels': {'imported': 0, 'skipped': 0},
        'users': {'imported': 0, 'skipped': 0},
        'messages': {'imported': 0, 'skipped': 0},
        'reactions': {'imported': 0, 'skipped': 0},
    }
    
    with transaction.atomic():
        # 1. Migrate Servers
        print('📦 Migrating Servers...')
        servers_map = {}  # Maps server_id to Django model instance
        for server_data in servers_data:
            pg_server, created = DiscordServer.objects.get_or_create(
                server_id=server_data['server_id'],
                defaults={
                    'name': server_data['name'],
                    'created_at': server_data['created_at'],
                    'updated_at': server_data['updated_at'],
                }
            )
            servers_map[server_data['server_id']] = pg_server
            if created:
                stats['servers']['imported'] += 1
                print(f'  ✓ Imported server: {server_data["name"]}')
            else:
                stats['servers']['skipped'] += 1
        
        # 2. Migrate Channels
        print('\n📦 Migrating Channels...')
        channels_map = {}  # Maps channel_id to Django model instance
        for channel_data in channels_data:
            pg_server = servers_map[channel_data['server_id']]
            pg_channel, created = DiscordChannel.objects.get_or_create(
                channel_id=channel_data['channel_id'],
                defaults={
                    'server': pg_server,
                    'name': channel_data['name'],
                    'channel_type': channel_data['channel_type'],
                    'created_at': channel_data['created_at'],
                    'updated_at': channel_data['updated_at'],
                }
            )
            channels_map[channel_data['channel_id']] = pg_channel
            if created:
                stats['channels']['imported'] += 1
                print(f'  ✓ Imported channel: {channel_data["name"]}')
            else:
                stats['channels']['skipped'] += 1
        
        # 3. Migrate Users
        print('\n📦 Migrating Users...')
        users_map = {}  # Maps user_id to Django model instance
        for user_data in users_data:
            pg_user, created = DiscordUser.objects.get_or_create(
                user_id=user_data['user_id'],
                defaults={
                    'username': user_data['username'],
                    'display_name': user_data['display_name'],
                    'discriminator': user_data['discriminator'],
                    'avatar_url': user_data['avatar_url'],
                    'is_bot': user_data['is_bot'],
                    'created_at': user_data['created_at'],
                    'updated_at': user_data['updated_at'],
                }
            )
            users_map[user_data['user_id']] = pg_user
            if created:
                stats['users']['imported'] += 1
                print(f'  ✓ Imported user: {user_data["username"]}')
            else:
                stats['users']['skipped'] += 1
        
        # 4. Migrate Messages
        print('\n📦 Migrating Messages...')
        messages_map = {}  # Maps message_id to Django model instance
        total_messages = len(messages_data)
        processed_count = 0
        
        for message_data in messages_data:
            pg_channel = channels_map[message_data['channel_id']]
            pg_author = users_map[message_data['user_id']]
            
            pg_message, created = DiscordMessage.objects.get_or_create(
                message_id=message_data['message_id'],
                defaults={
                    'channel': pg_channel,
                    'author': pg_author,
                    'content': message_data['content'],
                    'timestamp': message_data['timestamp'],
                    'edited_timestamp': message_data['edited_timestamp'],
                    'is_pinned': message_data['is_pinned'],
                    'has_attachments': message_data['has_attachments'],
                    'attachment_count': message_data['attachment_count'],
                    'has_embeds': message_data['has_embeds'],
                    'embed_count': message_data['embed_count'],
                    'created_at': message_data['created_at'],
                }
            )
            messages_map[message_data['message_id']] = pg_message
            processed_count += 1
            
            if created:
                stats['messages']['imported'] += 1
            else:
                stats['messages']['skipped'] += 1
            
            if processed_count % 100 == 0:
                print(f'  ⏳ Processed {processed_count}/{total_messages} messages... (imported: {stats["messages"]["imported"]}, skipped: {stats["messages"]["skipped"]})')
        
        print(f'  ✓ Processed {processed_count} messages (imported: {stats["messages"]["imported"]}, skipped: {stats["messages"]["skipped"]})')
        
        # 5. Migrate Reactions
        print('\n📦 Migrating Reactions...')
        processed_reactions = 0
        for reaction_data in reactions_data:
            pg_message = messages_map[reaction_data['message_id']]
            
            reaction, created = DiscordReaction.objects.get_or_create(
                message=pg_message,
                emoji_name=reaction_data['emoji_name'],
                emoji_id=reaction_data['emoji_id'],
                defaults={
                    'count': reaction_data['count'],
                    'created_at': reaction_data['created_at'],
                }
            )
            processed_reactions += 1
            if created:
                stats['reactions']['imported'] += 1
            else:
                stats['reactions']['skipped'] += 1
        
        print(f'  ✓ Processed {processed_reactions} reactions (imported: {stats["reactions"]["imported"]}, skipped: {stats["reactions"]["skipped"]})')
    
    # Final summary
    print('\n' + '='*50)
    print('✅ Migration completed successfully!')
    print('='*50)
    
    print('\n📊 Import Summary:')
    print(f'  Servers:   {stats["servers"]["imported"]} imported, {stats["servers"]["skipped"]} skipped')
    print(f'  Channels:  {stats["channels"]["imported"]} imported, {stats["channels"]["skipped"]} skipped')
    print(f'  Users:     {stats["users"]["imported"]} imported, {stats["users"]["skipped"]} skipped')
    print(f'  Messages:  {stats["messages"]["imported"]} imported, {stats["messages"]["skipped"]} skipped')
    print(f'  Reactions: {stats["reactions"]["imported"]} imported, {stats["reactions"]["skipped"]} skipped')
    
    print('\n📊 Final counts in PostgreSQL:')
    print(f'  Servers: {DiscordServer.objects.count()}')
    print(f'  Channels: {DiscordChannel.objects.count()}')
    print(f'  Users: {DiscordUser.objects.count()}')
    print(f'  Messages: {DiscordMessage.objects.count()}')
    print(f'  Reactions: {DiscordReaction.objects.count()}')
    print('')


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Migrate data from SQLite to PostgreSQL')
    parser.add_argument('--sqlite-path', type=str, default=None,
                        help='Path to SQLite database file (default: db.sqlite3 in project root)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be migrated without actually migrating')
    
    args = parser.parse_args()
    
    migrate_data(sqlite_path=args.sqlite_path, dry_run=args.dry_run)

