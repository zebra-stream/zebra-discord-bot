#!/usr/bin/env python3
"""
Fixed migration script that reads SQLite directly (bypassing Django ORM issues)
and imports to PostgreSQL.
"""
import os
import sys
import django
import sqlite3
from pathlib import Path
from datetime import datetime

# Add the project directory to Python path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'discord_intelligence.settings')
django.setup()

# Now we can import Django models
from django.db import transaction
from django.conf import settings
from bot.models import DiscordServer, DiscordChannel, DiscordUser, DiscordMessage, DiscordReaction


def migrate_data(sqlite_path=None, dry_run=False):
    """Migrate all data from SQLite to PostgreSQL using raw SQLite queries"""
    
    # Determine SQLite path - check for olddb.sqlite3 first, then db.sqlite3
    if not sqlite_path:
        old_db_path = BASE_DIR / 'olddb.sqlite3'
        if old_db_path.exists():
            sqlite_path = old_db_path
            print(f'📦 Found olddb.sqlite3, using that for migration')
        else:
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
    
    # Connect directly to SQLite using sqlite3 (bypassing Django)
    sqlite_conn = sqlite3.connect(str(sqlite_path))
    sqlite_conn.row_factory = sqlite3.Row  # Return rows as dict-like objects
    sqlite_cursor = sqlite_conn.cursor()
    
    try:
        # Count records in SQLite
        print('\n📊 Counting records in SQLite database...')
        sqlite_cursor.execute('SELECT COUNT(*) FROM discord_servers')
        sqlite_servers = sqlite_cursor.fetchone()[0]
        sqlite_cursor.execute('SELECT COUNT(*) FROM discord_channels')
        sqlite_channels = sqlite_cursor.fetchone()[0]
        sqlite_cursor.execute('SELECT COUNT(*) FROM discord_users')
        sqlite_users = sqlite_cursor.fetchone()[0]
        sqlite_cursor.execute('SELECT COUNT(*) FROM discord_messages')
        sqlite_messages = sqlite_cursor.fetchone()[0]
        sqlite_cursor.execute('SELECT COUNT(*) FROM discord_reactions')
        sqlite_reactions = sqlite_cursor.fetchone()[0]
        
        print(f'  Servers: {sqlite_servers}')
        print(f'  Channels: {sqlite_channels}')
        print(f'  Users: {sqlite_users}')
        print(f'  Messages: {sqlite_messages}')
        print(f'  Reactions: {sqlite_reactions}')
        
        if dry_run:
            print('\n🔍 DRY RUN - No data will be migrated')
            return
        
        # Read all data from SQLite
        print('\n📖 Reading data from SQLite...')
        
        # Read Servers
        sqlite_cursor.execute('SELECT * FROM discord_servers')
        servers_data = [dict(row) for row in sqlite_cursor.fetchall()]
        
        # Read Channels
        sqlite_cursor.execute('SELECT * FROM discord_channels')
        channels_data = [dict(row) for row in sqlite_cursor.fetchall()]
        
        # Read Users
        sqlite_cursor.execute('SELECT * FROM discord_users')
        users_data = [dict(row) for row in sqlite_cursor.fetchall()]
        
        # Read Messages
        sqlite_cursor.execute('SELECT * FROM discord_messages ORDER BY timestamp')
        messages_data = [dict(row) for row in sqlite_cursor.fetchall()]
        
        # Read Reactions
        sqlite_cursor.execute('SELECT * FROM discord_reactions')
        reactions_data = [dict(row) for row in sqlite_cursor.fetchall()]
        
        print(f'  ✓ Read {len(servers_data)} servers')
        print(f'  ✓ Read {len(channels_data)} channels')
        print(f'  ✓ Read {len(users_data)} users')
        print(f'  ✓ Read {len(messages_data)} messages')
        print(f'  ✓ Read {len(reactions_data)} reactions')
        
    finally:
        sqlite_conn.close()
    
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
        
        # Reconnect to SQLite to resolve foreign keys
        sqlite_conn = sqlite3.connect(str(sqlite_path))
        sqlite_cursor = sqlite_conn.cursor()
        
        for channel_data in channels_data:
            # server_id might be Django's internal ID, need to look up the actual Discord server_id
            server_id_fk = channel_data['server_id']
            
            # Check if it's already a Discord server_id (large number) or Django internal ID (small number)
            if server_id_fk < 1000:  # Likely Django internal ID
                # Look up the actual Discord server_id
                sqlite_cursor.execute('SELECT server_id FROM discord_servers WHERE id = ?', (server_id_fk,))
                result = sqlite_cursor.fetchone()
                if result:
                    server_id = result[0]
                else:
                    print(f'  ⚠️  Warning: Could not find server with Django id {server_id_fk} for channel {channel_data.get("name", "unknown")}')
                    continue
            else:
                # It's already a Discord server_id
                server_id = server_id_fk
            
            pg_server = servers_map.get(server_id)
            if not pg_server:
                print(f'  ⚠️  Warning: Could not find server {server_id} for channel {channel_data.get("name", "unknown")}')
                continue
                
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
                    'display_name': user_data.get('display_name', ''),
                    'discriminator': user_data.get('discriminator', ''),
                    'avatar_url': user_data.get('avatar_url', ''),
                    'is_bot': user_data.get('is_bot', False),
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
            # channel_id and author_id might be Django internal IDs, need to resolve them
            channel_id_fk = message_data['channel_id']
            author_id_fk = message_data['author_id']
            
            # Resolve channel_id
            if channel_id_fk < 1000:  # Likely Django internal ID
                sqlite_cursor.execute('SELECT channel_id FROM discord_channels WHERE id = ?', (channel_id_fk,))
                result = sqlite_cursor.fetchone()
                if result:
                    channel_id = result[0]
                else:
                    print(f'  ⚠️  Warning: Could not find channel with Django id {channel_id_fk} for message {message_data["message_id"]}')
                    continue
            else:
                channel_id = channel_id_fk
            
            # Resolve author_id
            if author_id_fk < 1000:  # Likely Django internal ID
                sqlite_cursor.execute('SELECT user_id FROM discord_users WHERE id = ?', (author_id_fk,))
                result = sqlite_cursor.fetchone()
                if result:
                    user_id = result[0]
                else:
                    print(f'  ⚠️  Warning: Could not find user with Django id {author_id_fk} for message {message_data["message_id"]}')
                    continue
            else:
                user_id = author_id_fk
            
            pg_channel = channels_map.get(channel_id)
            pg_author = users_map.get(user_id)
            
            if not pg_channel:
                print(f'  ⚠️  Warning: Could not find channel {channel_id} for message {message_data["message_id"]}')
                continue
            if not pg_author:
                print(f'  ⚠️  Warning: Could not find user {user_id} for message {message_data["message_id"]}')
                continue
            
            pg_message, created = DiscordMessage.objects.get_or_create(
                message_id=message_data['message_id'],
                defaults={
                    'channel': pg_channel,
                    'author': pg_author,
                    'content': message_data['content'] or '',
                    'timestamp': message_data['timestamp'],
                    'edited_timestamp': message_data.get('edited_timestamp'),
                    'is_pinned': message_data.get('is_pinned', False),
                    'has_attachments': message_data.get('has_attachments', False),
                    'attachment_count': message_data.get('attachment_count', 0),
                    'has_embeds': message_data.get('has_embeds', False),
                    'embed_count': message_data.get('embed_count', 0),
                    'created_at': message_data.get('created_at', message_data['timestamp']),
                }
            )
            messages_map[message_data['message_id']] = pg_message
            processed_count += 1
            
            if created:
                stats['messages']['imported'] += 1
            else:
                stats['messages']['skipped'] += 1
            
            if processed_count % 10 == 0:
                print(f'  ⏳ Processed {processed_count}/{total_messages} messages... (imported: {stats["messages"]["imported"]}, skipped: {stats["messages"]["skipped"]})')
        
        sqlite_conn.close()
        
        print(f'  ✓ Processed {processed_count} messages (imported: {stats["messages"]["imported"]}, skipped: {stats["messages"]["skipped"]})')
        
        # 5. Migrate Reactions
        print('\n📦 Migrating Reactions...')
        processed_reactions = 0
        sqlite_conn = sqlite3.connect(str(sqlite_path))
        sqlite_cursor = sqlite_conn.cursor()
        
        for reaction_data in reactions_data:
            # message_id is stored as a bigint (external ID) in the reactions table
            # But we need to check the actual schema first
            # Let's query to get the message_id
            reaction_id = reaction_data.get('id')
            sqlite_cursor.execute('SELECT message_id FROM discord_reactions WHERE id = ?', (reaction_id,))
            result = sqlite_cursor.fetchone()
            if not result:
                continue
            # message_id in reactions table is the Django internal ID, need to look up the actual message_id
            django_message_id = result[0]
            sqlite_cursor.execute('SELECT message_id FROM discord_messages WHERE id = ?', (django_message_id,))
            result = sqlite_cursor.fetchone()
            if not result:
                continue
            actual_message_id = result[0]
            
            pg_message = messages_map.get(actual_message_id)
            if not pg_message:
                continue
            
            reaction, created = DiscordReaction.objects.get_or_create(
                message=pg_message,
                emoji_name=reaction_data['emoji_name'],
                emoji_id=reaction_data.get('emoji_id'),
                defaults={
                    'count': reaction_data.get('count', 1),
                    'created_at': reaction_data.get('created_at'),
                }
            )
            processed_reactions += 1
            if created:
                stats['reactions']['imported'] += 1
            else:
                stats['reactions']['skipped'] += 1
        
        sqlite_conn.close()
        
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

