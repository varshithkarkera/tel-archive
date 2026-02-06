"""Telegram archive management - fetch, download, delete"""
import os
import asyncio
from pathlib import Path

async def fetch_telegram_archives(api_id: str, api_hash: str, destination: str, workspace_dir: Path):
    """Fetch and group archives from Telegram"""
    from telethon import TelegramClient
    from datetime import datetime
    import re
    
    session_file = workspace_dir.parent / "dailyarchive_session"
    client = TelegramClient(str(session_file), int(api_id), api_hash, sequential_updates=True)
    
    await client.connect()
    if not await client.is_user_authorized():
        await client.start()
    
    # Convert destination
    dest = destination
    if dest != "me" and dest.lstrip('-').isdigit():
        dest = int(dest)
    
    # Fetch messages with documents
    messages = []
    async for message in client.iter_messages(dest, limit=1000):
        if message.document and message.file.name:
            messages.append(message)
    
    await client.disconnect()
    
    # Group by archive name (detect .7z and .7z.001, .7z.002 pattern)
    archives = {}
    
    for msg in messages:
        filename = msg.file.name
        
        # Detect archive pattern
        match = re.match(r'(.+?)\.7z(?:\.(\d+))?$', filename)
        if match:
            archive_name = match.group(1)
            part_num = int(match.group(2)) if match.group(2) else 0
            
            if archive_name not in archives:
                archives[archive_name] = {
                    'id': archive_name,
                    'name': archive_name,
                    'files': [],
                    'parts': 0,
                    'total_size': 0,
                    'date': None,
                    'expanded': False
                }
            
            archives[archive_name]['files'].append({
                'name': filename,
                'size': f"{msg.file.size / (1024*1024):.1f} MB",
                'message_id': msg.id,
                'part_num': part_num
            })
            archives[archive_name]['parts'] += 1
            archives[archive_name]['total_size'] += msg.file.size
            
            if not archives[archive_name]['date'] or msg.date > archives[archive_name]['date']:
                archives[archive_name]['date'] = msg.date
    
    # Format output
    result = []
    for archive in archives.values():
        # Sort files by part number
        archive['files'].sort(key=lambda x: x['part_num'])
        
        result.append({
            'id': archive['id'],
            'name': archive['name'],
            'parts': archive['parts'],
            'total_size': f"{archive['total_size'] / (1024*1024):.1f} MB",
            'date': archive['date'].strftime('%Y-%m-%d %H:%M') if archive['date'] else 'Unknown',
            'files': archive['files'],
            'expanded': False
        })
    
    # Sort by date (newest first)
    result.sort(key=lambda x: x['date'], reverse=True)
    
    return result

async def download_telegram_archive(archive_id: str, api_id: str, api_hash: str, destination: str, workspace_dir: Path):
    """Download archive from Telegram"""
    from telethon import TelegramClient
    import re
    
    session_file = workspace_dir.parent / "dailyarchive_session"
    client = TelegramClient(str(session_file), int(api_id), api_hash, sequential_updates=True)
    
    await client.connect()
    if not await client.is_user_authorized():
        await client.start()
    
    # Convert destination
    dest = destination
    if dest != "me" and dest.lstrip('-').isdigit():
        dest = int(dest)
    
    # Create download folder
    download_dir = workspace_dir / f"downloaded_{archive_id}"
    download_dir.mkdir(exist_ok=True)
    
    # Fetch and download matching files
    async for message in client.iter_messages(dest, limit=1000):
        if message.document and message.file.name:
            filename = message.file.name
            match = re.match(r'(.+?)\.7z(?:\.(\d+))?$', filename)
            
            if match and match.group(1) == archive_id:
                print(f"Downloading {filename}...")
                await message.download_media(file=str(download_dir / filename))
    
    await client.disconnect()
    
    return download_dir

async def delete_telegram_archive(archive_id: str, api_id: str, api_hash: str, destination: str, workspace_dir: Path):
    """Delete archive from Telegram"""
    from telethon import TelegramClient
    import re
    
    session_file = workspace_dir.parent / "dailyarchive_session"
    client = TelegramClient(str(session_file), int(api_id), api_hash, sequential_updates=True)
    
    await client.connect()
    if not await client.is_user_authorized():
        await client.start()
    
    # Convert destination
    dest = destination
    if dest != "me" and dest.lstrip('-').isdigit():
        dest = int(dest)
    
    # Find and delete matching messages
    deleted = 0
    message_ids = []
    
    async for message in client.iter_messages(dest, limit=1000):
        if message.document and message.file.name:
            filename = message.file.name
            match = re.match(r'(.+?)\.7z(?:\.(\d+))?$', filename)
            
            if match and match.group(1) == archive_id:
                message_ids.append(message.id)
    
    if message_ids:
        await client.delete_messages(dest, message_ids)
        deleted = len(message_ids)
    
    await client.disconnect()
    
    return deleted
