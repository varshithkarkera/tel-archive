"""Telegram upload with fast parallel transfer"""
import os
import asyncio
import hashlib
import math
from pathlib import Path

async def fast_upload_file(client, file_path, progress_callback=None):
    """Upload file using optimized method"""
    from telethon import utils, helpers
    from telethon.tl.types import InputFile, InputFileBig
    from telethon.tl.functions.upload import SaveFilePartRequest, SaveBigFilePartRequest
    
    file_id = helpers.generate_random_long()
    file_size = os.path.getsize(file_path)
    
    # Determine part size and count
    part_size = utils.get_appropriated_part_size(file_size) * 1024
    part_count = math.ceil(file_size / part_size)
    is_large = file_size > 10 * 1024 * 1024
    
    hash_md5 = hashlib.md5()
    
    with open(file_path, 'rb') as f:
        for part_index in range(part_count):
            part = f.read(part_size)
            
            if not is_large:
                hash_md5.update(part)
            
            # Upload part
            if is_large:
                await client(SaveBigFilePartRequest(file_id, part_index, part_count, part))
            else:
                await client(SaveFilePartRequest(file_id, part_index, part))
            
            if progress_callback:
                progress_callback(f.tell(), file_size)
    
    if is_large:
        return InputFileBig(file_id, part_count, os.path.basename(file_path))
    else:
        return InputFile(file_id, part_count, os.path.basename(file_path), hash_md5.hexdigest())

async def upload_files_to_telegram(parts: list, destination: str, api_id: str, api_hash: str, workspace_dir: Path, archive_password: str = None):
    """Upload files to Telegram"""
    from telethon import TelegramClient, helpers
    from telethon.tl.functions.messages import SendMediaRequest
    from telethon.tl.types import InputMediaUploadedDocument, DocumentAttributeFilename
    from datetime import datetime
    from config import config
    from encryption import list_archive_contents
    
    # Session file stays in root, not workspace
    session_file = workspace_dir.parent / "dailyarchive_session"
    client = TelegramClient(
        str(session_file),
        int(api_id),
        api_hash,
        sequential_updates=True
    )
    
    # Performance tweaks and session persistence
    client.flood_sleep_threshold = 0
    
    await client.connect()
    if not await client.is_user_authorized():
        await client.start()
    
    print("\n" + "-" * 78)
    print("    [+] UPLOADING (Fast Mode)")
    print("-" * 78)
    
    # Convert destination to int if it's a channel ID
    dest = destination
    if dest != "me" and dest.lstrip('-').isdigit():
        dest = int(dest)
    
    for i, part in enumerate(parts, 1):
        print(f"\n    [{i}/{len(parts)}] Uploading {part.name}...")
        
        # Use fast upload
        uploaded_file = await fast_upload_file(
            client,
            str(part),
            progress_callback=lambda current, total: print(
                f"\r    Progress: {current/total*100:.1f}%", end=""
            )
        )
        
        # Generate caption based on settings
        caption_mode = config.get("upload_caption", "detailed")
        
        if caption_mode == "none":
            caption = ""
        elif caption_mode == "minimal":
            caption = f"📦 {part.name}"
        else:  # detailed
            # Get file info
            file_size = part.stat().st_size / (1024 * 1024)  # MB
            created_date = datetime.fromtimestamp(part.stat().st_ctime).strftime("%Y-%m-%d %H:%M")
            upload_date = datetime.now().strftime("%Y-%m-%d %H:%M")
            
            # Try to detect what's inside (from folder name or file name)
            parent_folder = part.parent.name
            original_file = parent_folder
            
            caption = f"📦 {part.name}\n"
            caption += f"📁 Source: {original_file}\n"
            caption += f"📊 Size: {file_size:.1f} MB\n"
            
            # List archive contents if password provided
            if archive_password and part.suffix in ['.7z', '.001']:
                # For split archives, use the first part
                archive_to_check = part if part.suffix == '.7z' else part.parent / f"{part.stem.rsplit('.', 1)[0]}.7z.001"
                
                contents = list_archive_contents(archive_to_check, archive_password)
                
                if contents:
                    caption += f"\n📄 Contents ({len(contents)} file(s)):\n"
                    for file_info in contents[:10]:  # Limit to 10 files
                        # Convert size to readable format
                        try:
                            size_bytes = int(file_info['size'])
                            if size_bytes < 1024:
                                size_str = f"{size_bytes} B"
                            elif size_bytes < 1024 * 1024:
                                size_str = f"{size_bytes / 1024:.1f} KB"
                            else:
                                size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
                        except:
                            size_str = file_info['size']
                        
                        caption += f"  • {file_info['name']} ({size_str})\n"
                    
                    if len(contents) > 10:
                        caption += f"  ... and {len(contents) - 10} more\n"
            
            caption += f"\n📅 Created: {created_date}\n"
            caption += f"⬆️ Uploaded: {upload_date}"
        
        # Send the uploaded file
        media = InputMediaUploadedDocument(
            file=uploaded_file,
            mime_type='application/x-7z-compressed',
            attributes=[DocumentAttributeFilename(part.name)],
        )
        
        await client(SendMediaRequest(
            peer=dest,
            media=media,
            message=caption
        ))
        
        print(f"\n    [+] Uploaded {part.name}")
    
    await client.disconnect()

async def fetch_telegram_channels(api_id: str, api_hash: str, workspace_dir: Path):
    """Fetch user's Telegram channels"""
    from telethon import TelegramClient
    from telethon.tl.types import Channel
    
    # Session file stays in root, not workspace
    session_file = workspace_dir.parent / "dailyarchive_session"
    client = TelegramClient(
        str(session_file),
        int(api_id),
        api_hash,
        sequential_updates=True
    )
    
    await client.connect()
    if not await client.is_user_authorized():
        await client.start()
    
    dialogs = await client.get_dialogs()
    channels = []
    
    print("\n    [>] Your Channels:\n")
    print("        0. Saved Messages (me)")
    
    idx = 1
    for dialog in dialogs:
        if isinstance(dialog.entity, Channel) and dialog.entity.creator:
            channels.append(dialog)
            print(f"        {idx}. {dialog.name} (ID: {dialog.entity.id})")
            idx += 1
    
    await client.disconnect()
    return channels


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
