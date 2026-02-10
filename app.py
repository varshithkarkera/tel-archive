#!/usr/bin/env python3
"""Flask web app for Tel Archive"""
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, session, Response
from werkzeug.utils import secure_filename
import os
import asyncio
from pathlib import Path
from datetime import datetime
import json
import time
import threading

# Global lock for Telegram session file access
telegram_session_lock = threading.Lock()

from config import load_config, save_config, config, WORKSPACE_DIR
from video import compress_video, get_file_size_gb
from encryption import encrypt_multiple_files, split_and_encrypt_multiple, decrypt_and_extract, list_archive_contents
from telegram_archives import fetch_telegram_archives, download_telegram_archive, delete_telegram_archive

# Suppress Telethon flood wait spam
import logging
logging.getLogger('telethon').setLevel(logging.ERROR)

app = Flask(__name__)
app.secret_key = os.urandom(24).hex()
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024 * 1024  # 50GB max

# Log file for persistent logging
LOG_FILE = WORKSPACE_DIR.parent / "dailyarchive.log"

# Setup logging to file
import logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%I:%M:%S %p',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()  # Also print to console
    ]
)
logger = logging.getLogger(__name__)

def log_message(msg, msg_type='info'):
    """Log message to both file and console"""
    if msg_type == 'error':
        logger.error(msg)
    else:
        logger.info(msg)

def add_progress_log(job_id, msg, msg_type='info'):
    """Add log to both progress_logs and log file"""
    progress_logs[job_id].append({'msg': msg, 'type': msg_type})
    log_message(msg, msg_type)

# Progress storage file
PROGRESS_FILE = WORKSPACE_DIR.parent / "progress_data.json"

# Helper function to load progress from disk
def load_progress():
    """Load progress data from disk"""
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

# Helper function to save progress to disk
def save_progress():
    """Save progress data to disk"""
    try:
        data = {
            'progress_data': progress_data,
            'progress_logs': progress_logs
        }
        with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Failed to save progress: {e}")

# Load existing progress on startup
saved_progress = load_progress()
progress_data = saved_progress.get('progress_data', {})
progress_logs = saved_progress.get('progress_logs', {})

# Clean up completed jobs from loaded progress
for job_id in list(progress_data.keys()):
    if 'COMPLETE' in str(progress_data.get(job_id, '')) or 'ERROR' in str(progress_data.get(job_id, '')):
        # Keep completed jobs for display but don't restart them
        pass

# Helper function to create Telegram client with proper session handling
def create_telegram_client(api_id, api_hash):
    """Create TelegramClient with persistent session"""
    from telethon import TelegramClient
    
    session_file = WORKSPACE_DIR.parent / "dailyarchive_session"
    client = TelegramClient(
        str(session_file),
        int(api_id),
        api_hash,
        sequential_updates=True
    )
    return client

async def start_telegram_client(client):
    """Start Telegram client, reusing existing session if available"""
    await client.connect()
    if not await client.is_user_authorized():
        await client.start()
    return client


load_config()

# Progress storage by job ID
progress_data = {}
progress_logs = {}  # Store all log messages per job
active_jobs = set()  # Track active jobs to prevent duplicates
current_job_id = None  # Track the currently active job

@app.route('/progress/<job_id>')
def progress(job_id):
    """Get progress for a job"""
    msg = progress_data.get(job_id, '')
    complete = 'COMPLETE' in msg or 'ERROR' in msg
    results = progress_data.get(f"{job_id}_results", []) if complete else []
    result = progress_data.get(f"{job_id}_result", {}) if complete else {}
    logs = progress_logs.get(job_id, [])
    
    # Save progress to disk on every request
    save_progress()
    
    return jsonify({'message': msg, 'complete': complete, 'results': results, 'result': result, 'logs': logs})

@app.route('/active-job')
def get_active_job():
    """Get the currently active job ID"""
    return jsonify({'job_id': current_job_id})

@app.route('/')
def index():
    """Home page"""
    return render_template('index.html', config=config)

@app.route('/files')
def list_files():
    """List files in archive folder"""
    files = []
    for item in WORKSPACE_DIR.rglob('*'):
        if item.is_file() and not item.name.startswith('.'):
            rel_path = item.relative_to(WORKSPACE_DIR)
            files.append({
                'name': str(rel_path),
                'size': item.stat().st_size,
                'size_gb': round(item.stat().st_size / (1024**3), 2),
                'modified': datetime.fromtimestamp(item.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
            })
    return jsonify(files)

@app.route('/upload', methods=['POST'])
def upload_files():
    """Upload files to archive folder"""
    if 'files[]' not in request.files:
        return jsonify({'error': 'No files provided'}), 400
    
    files = request.files.getlist('files[]')
    uploaded = []
    
    for file in files:
        if file.filename:
            filename = secure_filename(file.filename)
            filepath = WORKSPACE_DIR / filename
            file.save(filepath)
            uploaded.append(filename)
    
    return jsonify({'success': True, 'files': uploaded})

@app.route('/compress', methods=['POST'])
def compress():
    """Compress video files"""
    global current_job_id
    
    data = request.json
    files = data.get('files', [])
    keep_audio = data.get('keep_audio', False)
    initial_logs = data.get('initial_logs', [])
    
    # Create a unique job identifier based on files
    file_signature = '|'.join(sorted(files))
    
    # Check if these files are already being processed
    if file_signature in active_jobs:
        # Return the existing job_id instead of error
        if current_job_id:
            return jsonify({'success': True, 'job_id': current_job_id, 'reused': True})
        else:
            return jsonify({'error': 'These files are already being processed'}), 409
    
    job_id = str(int(time.time() * 1000))
    active_jobs.add(file_signature)
    
    current_job_id = job_id
    
    # Initialize progress
    progress_data[job_id] = "Starting compression..."
    progress_logs[job_id] = initial_logs  # Store initial logs from frontend
    
    # Run compression in background thread
    def compress_task():
        results = []  # Initialize results list
        try:
            for filename in files:
                filepath = WORKSPACE_DIR / filename
                if not filepath.exists():
                    continue
                
                # Create output folder
                date_str = datetime.now().strftime('%Y%m%d')
                counter = 1
                while True:
                    folder_name = f"{date_str}_{filepath.stem}" if counter == 1 else f"{date_str}_{counter}_{filepath.stem}"
                    output_dir = WORKSPACE_DIR / folder_name
                    if not output_dir.exists():
                        break
                    counter += 1
                
                output_dir.mkdir(parents=True, exist_ok=True)
                
                add_progress_log(job_id, f'[{files.index(filename) + 1}/{len(files)}] Compressing {filepath.name}...', 'info')
                
                def progress_callback(msg):
                    progress_data[job_id] = msg
                
                compressed = compress_video(filepath, output_dir, keep_audio, progress_callback, 
                                          cpu_preset=config.get('cpu_preset', 'normal'),
                                          cpu_threads=config.get('cpu_threads', 0))
                
                if compressed:
                    add_progress_log(job_id, f'[OK] Compressed {filepath.name} → {compressed.name} ({round(get_file_size_gb(compressed), 2)} GB)', 'success')
                    results.append({
                        'original': filename,
                        'compressed': str(compressed.relative_to(WORKSPACE_DIR)),
                        'size': round(get_file_size_gb(compressed), 2)
                    })
            
            progress_data[job_id] = "COMPLETE"
            progress_data[f"{job_id}_results"] = results
        except Exception as e:
            progress_data[job_id] = f"ERROR: {str(e)}"
            progress_logs[job_id].append({'time': datetime.now().isoformat(), 'msg': f"ERROR: {str(e)}", 'type': 'error'})
        finally:
            # Remove from active jobs when done
            if file_signature in active_jobs:
                active_jobs.remove(file_signature)
            # Clear current job if this was it
            global current_job_id
            if current_job_id == job_id:
                current_job_id = None
    
    thread = threading.Thread(target=compress_task)
    thread.start()
    
    return jsonify({'success': True, 'job_id': job_id})

@app.route('/encrypt', methods=['POST'])
def encrypt():
    """Encrypt or archive files"""
    data = request.json
    files = data.get('files', [])
    bundle = data.get('bundle', True)
    encrypt_files = data.get('encrypt', True)
    auto_upload = data.get('auto_upload', False)
    upload_destination = data.get('upload_destination', 'me')
    
    # Create a unique job identifier to prevent duplicates
    file_signature = f"encrypt|{bundle}|{encrypt_files}|{'|'.join(sorted(files))}"
    if file_signature in active_jobs:
        return jsonify({'error': 'These files are already being processed'}), 409
    
    job_id = str(int(time.time() * 1000))
    active_jobs.add(file_signature)
    
    global current_job_id
    current_job_id = job_id
    

    
    if encrypt_files and not config.get('password'):
        return jsonify({'error': 'No password set'}), 400
    
    file_paths = [WORKSPACE_DIR / f for f in files if (WORKSPACE_DIR / f).exists()]
    if not file_paths:
        return jsonify({'error': 'No valid files'}), 400
    
    # Create output folder
    date_str = datetime.now().strftime('%Y%m%d')
    counter = 1
    while True:
        archive_name = date_str if counter == 1 else f"{date_str}_{counter}"
        output_dir = WORKSPACE_DIR / archive_name
        if not output_dir.exists():
            break
        counter += 1
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Initialize progress
    progress_data[job_id] = "Starting..."
    progress_logs[job_id] = []
    
    password = config.get('password') if encrypt_files else None
    
    def process_task():
        try:
            def progress_callback(msg):
                progress_data[job_id] = msg
            
            if bundle:
                # Bundle all files into one archive
                total_size_bytes = sum(f.stat().st_size for f in file_paths)
                total_size_mb = total_size_bytes / (1024 * 1024)
                split_size_mb = config.get('split_size_mb', 2000)
                should_split = total_size_mb > split_size_mb
                
                add_progress_log(job_id, f'[ARCHIVE] Bundling {len(file_paths)} file(s) into one archive', 'info')
                log_message(f'[ARCHIVE] Bundling {len(file_paths)} file(s) into one archive')
                
                if should_split:
                    add_progress_log(job_id, f'[SPLIT] Archive size {total_size_mb:.0f}MB exceeds {split_size_mb}MB limit, splitting...', 'info')
                    log_message(f'[SPLIT] Archive size {total_size_mb:.0f}MB exceeds {split_size_mb}MB limit, splitting...')
                
                if encrypt_files:
                    # Encrypted bundle - always check for split
                    if should_split:
                        parts = split_and_encrypt_multiple(file_paths, output_dir, password, archive_name, progress_callback)
                        if parts:
                            add_progress_log(job_id, f'[OK] Encrypted and split into {len(parts)} part(s): {archive_name}', 'success')
                            progress_data[job_id] = "COMPLETE"
                            progress_data[f"{job_id}_result"] = {
                                'success': True,
                                'folder': archive_name,
                                'parts': len(parts),
                                'split': True,
                                'encrypted': True
                            }
                        else:
                            raise Exception("Encryption failed")
                    else:
                        encrypted = encrypt_multiple_files(file_paths, output_dir, password, archive_name, progress_callback)
                        if encrypted:
                            progress_data[job_id] = "COMPLETE"
                            progress_data[f"{job_id}_result"] = {
                                'success': True,
                                'folder': archive_name,
                                'file': encrypted.name,
                                'split': False,
                                'encrypted': True
                            }
                        else:
                            raise Exception("Encryption failed")
                else:
                    # Non-encrypted bundle - also check for split
                    from encryption import archive_multiple_files_no_password, split_archive_no_password
                    if should_split:
                        parts = split_archive_no_password(file_paths, output_dir, archive_name, split_size_mb, progress_callback)
                        if parts:
                            progress_data[job_id] = "COMPLETE"
                            progress_data[f"{job_id}_result"] = {
                                'success': True,
                                'folder': archive_name,
                                'parts': len(parts),
                                'split': True,
                                'encrypted': False
                            }
                        else:
                            raise Exception("Archiving failed")
                    else:
                        archived = archive_multiple_files_no_password(file_paths, output_dir, archive_name, progress_callback)
                        if archived:
                            progress_data[job_id] = "COMPLETE"
                            progress_data[f"{job_id}_result"] = {
                                'success': True,
                                'folder': archive_name,
                                'file': archived.name,
                                'split': False,
                                'encrypted': False
                            }
                        else:
                            raise Exception("Archiving failed")
            else:
                # Process each file separately (with optional parallel upload)
                add_progress_log(job_id, f'[ARCHIVE] Processing {len(file_paths)} file(s) separately', 'info')
                
                split_size_mb = config.get('split_size_mb', 2000)
                results = []
                uploaded_count = 0
                total_files = len(file_paths)
                
                # Collect all files to upload in batch with source mapping
                files_to_upload = []
                file_source_map = {}  # Map output file to original source file
                
                for i, file_path in enumerate(file_paths, 1):
                    file_output_dir = output_dir / file_path.stem
                    file_output_dir.mkdir(parents=True, exist_ok=True)
                    
                    # Store original file path relative to workspace
                    original_relative_path = str(file_path.relative_to(WORKSPACE_DIR))
                    
                    # Check if file needs splitting
                    file_size_mb = file_path.stat().st_size / (1024 * 1024)
                    needs_split = file_size_mb > split_size_mb
                    
                    # Create progress callback that shows overall progress
                    def file_progress_callback(msg):
                        # Extract percentage from message like "Encrypting: 45% complete"
                        import re
                        match = re.search(r'(\d+)%', msg)
                        if match:
                            file_percent = int(match.group(1))
                            # Calculate overall progress: (completed files + current file progress) / total files
                            overall_percent = ((i - 1) * 100 + file_percent) / total_files
                            action = "Encrypting" if encrypt_files else "Archiving"
                            progress_callback(f"{action}: {overall_percent:.0f}% complete")
                        else:
                            progress_callback(msg)
                    
                    if encrypt_files:
                        # Encrypt each file separately (with split if needed)
                        from encryption import encrypt_file, encrypt_and_split_file
                        
                        if needs_split:
                            add_progress_log(job_id, f'[{i}/{len(file_paths)}] Encrypting and splitting {file_path.name} ({file_size_mb:.0f}MB)...', 'info')
                            parts = encrypt_and_split_file(file_path, file_output_dir, password, split_size_mb, file_progress_callback)
                            if parts:
                                for part in parts:
                                    results.append(str(part.relative_to(output_dir)))
                                    if auto_upload:
                                        files_to_upload.append(part)
                                        file_source_map[str(part)] = original_relative_path
                                add_progress_log(job_id, f'[{i}/{len(file_paths)}] Encrypted {file_path.name} into {len(parts)} part(s)', 'success')
                        else:
                            encrypted = encrypt_file(file_path, file_output_dir, password, file_progress_callback)
                            if encrypted:
                                results.append(str(encrypted.relative_to(output_dir)))
                                add_progress_log(job_id, f'[{i}/{len(file_paths)}] Encrypted {file_path.name}', 'success')
                                if auto_upload:
                                    files_to_upload.append(encrypted)
                                    file_source_map[str(encrypted)] = original_relative_path
                    else:
                        # Archive each file separately (no password, with split if needed)
                        from encryption import archive_file_no_password, archive_and_split_file_no_password
                        
                        if needs_split:
                            add_progress_log(job_id, f'[{i}/{len(file_paths)}] Archiving and splitting {file_path.name} ({file_size_mb:.0f}MB)...', 'info')
                            parts = archive_and_split_file_no_password(file_path, file_output_dir, split_size_mb, file_progress_callback)
                            if parts:
                                for part in parts:
                                    results.append(str(part.relative_to(output_dir)))
                                    if auto_upload:
                                        files_to_upload.append(part)
                                        file_source_map[str(part)] = original_relative_path
                                add_progress_log(job_id, f'[{i}/{len(file_paths)}] Archived {file_path.name} into {len(parts)} part(s)', 'success')
                        else:
                            archived = archive_file_no_password(file_path, file_output_dir, file_progress_callback)
                            if archived:
                                results.append(str(archived.relative_to(output_dir)))
                                add_progress_log(job_id, f'[{i}/{len(file_paths)}] Archived {file_path.name}', 'success')
                                if auto_upload:
                                    files_to_upload.append(archived)
                                    file_source_map[str(archived)] = original_relative_path
                
                # Upload all files in batch with single client connection
                if auto_upload and files_to_upload:
                    add_progress_log(job_id, f'[UPLOAD] Starting batch upload of {len(files_to_upload)} file(s)...', 'info')
                    try:
                        asyncio.run(upload_multiple_files_batch(files_to_upload, upload_destination, job_id, file_source_map))
                        uploaded_count = len(files_to_upload)
                        add_progress_log(job_id, f'[OK] Uploaded {uploaded_count} file(s) successfully', 'success')
                    except Exception as e:
                        add_progress_log(job_id, f'[ERROR] Batch upload failed: {str(e)}', 'error')
                
                progress_data[job_id] = "COMPLETE"
                progress_data[f"{job_id}_result"] = {
                    'success': True,
                    'folder': archive_name,
                    'files': results,
                    'split': False,
                    'encrypted': encrypt_files,
                    'separate': True,
                    'uploaded': uploaded_count if auto_upload else 0
                }
                
        except Exception as e:
            progress_data[job_id] = f"ERROR: {str(e)}"
            add_progress_log(job_id, f'[ERROR] Processing failed: {str(e)}', 'error')
        finally:
            # Remove from active jobs when done
            if file_signature in active_jobs:
                active_jobs.remove(file_signature)
            # Clear current job if this was it
            global current_job_id
            if current_job_id == job_id:
                current_job_id = None
    
    thread = threading.Thread(target=process_task)
    thread.start()
    
    return jsonify({'success': True, 'job_id': job_id})

async def upload_multiple_files_batch(file_paths, destination, job_id, file_source_map=None):
    """Upload multiple files to Telegram using a single client connection"""
    from telethon.tl.functions.messages import SendMediaRequest
    from telethon.tl.types import InputMediaUploadedDocument, DocumentAttributeFilename
    from parallel_upload import parallel_upload_file
    from datetime import datetime
    from encryption import list_archive_contents
    
    if file_source_map is None:
        file_source_map = {}
    
    client = None
    try:
        # Create single client connection for all uploads
        client = create_telegram_client(config['telegram_api_id'], config['telegram_api_hash'])
        await start_telegram_client(client)
        
        # Convert destination - default to 'me' if empty or None
        dest = destination if destination and destination.strip() else config.get('upload_destination', 'me')
        if not dest or dest.strip() == '':
            dest = 'me'
        
        add_progress_log(job_id, f'[UPLOAD] Destination: {dest}', 'info')
        
        if dest != "me" and dest.lstrip('-').isdigit():
            dest = int(dest)
        
        total_files = len(file_paths)
        
        for i, file_path in enumerate(file_paths, 1):
            try:
                add_progress_log(job_id, f'[{i}/{total_files}] Uploading {file_path.name}...', 'info')
                
                # Get original source file path
                original_source = file_source_map.get(str(file_path), file_path.name)
                
                # Upload file with progress tracking
                import time
                start_time = time.time()
                last_update = [start_time]
                last_bytes = [0]
                
                def upload_progress(current, total):
                    now = time.time()
                    elapsed = now - last_update[0]
                    
                    if elapsed >= 0.5:
                        bytes_diff = current - last_bytes[0]
                        speed_mbps = (bytes_diff / elapsed) / (1024 * 1024)
                        last_update[0] = now
                        last_bytes[0] = current
                        
                        percent = (current / total) * 100
                        progress_data[job_id] = f"Uploading [{i}/{total_files}]: {percent:.1f}% ({speed_mbps:.2f} MB/s) - {file_path.name}"
                    elif current == total:
                        total_time = now - start_time
                        avg_speed = (total / total_time) / (1024 * 1024) if total_time > 0 else 0
                        progress_data[job_id] = f"Uploading [{i}/{total_files}]: 100.0% ({avg_speed:.2f} MB/s) - {file_path.name}"
                
                uploaded_file, file_size = await parallel_upload_file(
                    client, str(file_path), upload_progress, 
                    max_connections=config.get('parallel_connections', 20)
                )
                
                # Generate caption
                caption_mode = config.get("upload_caption", "detailed")
                if caption_mode == "none":
                    caption = ""
                elif caption_mode == "minimal":
                    caption = f"📦 {original_source}"
                else:  # detailed
                    file_size_mb = file_path.stat().st_size / (1024 * 1024)
                    created_date = datetime.fromtimestamp(file_path.stat().st_ctime).strftime("%Y-%m-%d %H:%M")
                    upload_date = datetime.now().strftime("%Y-%m-%d %H:%M")
                    
                    # Detect if this is a split part and if it's encrypted
                    import re
                    part_match = re.search(r'\.7z\.(\d+)$', file_path.name)
                    is_encrypted = file_path.suffix == '.7z' or '.7z.' in file_path.name or file_path.name.endswith('.7z')
                    
                    if part_match:
                        part_num = int(part_match.group(1))
                        base_name = re.sub(r'\.7z\.\d+$', '', file_path.name)
                        total_parts = len(list(file_path.parent.glob(f"{base_name}.7z.*")))
                        caption = f"📦 Part {part_num} of {total_parts}\n"
                        caption += f"Archive: {file_path.name}\n"
                        caption += f"Source: {original_source}\n"
                    else:
                        caption = f"📦 Archive: {file_path.name}\n"
                        caption += f"Source: {original_source}\n"
                    
                    caption += f"📊 Size: {file_size_mb:.2f} MB\n"
                    
                    if is_encrypted:
                        caption += f"🔒 Encrypted: Yes\n"
                    else:
                        caption += f"⚠️ Encrypted: No (Unprotected)\n"
                    
                    caption += f"\n📅 Created: {created_date}\n"
                    caption += f"⬆️ Uploaded: {upload_date}"
                
                # Send to Telegram
                media = InputMediaUploadedDocument(
                    file=uploaded_file,
                    mime_type='application/x-7z-compressed',
                    attributes=[DocumentAttributeFilename(file_name=file_path.name)]
                )
                
                await client(SendMediaRequest(
                    peer=dest,
                    media=media,
                    message=caption
                ))
                
                add_progress_log(job_id, f'[OK] Uploaded {file_path.name}', 'success')
                
            except Exception as file_error:
                add_progress_log(job_id, f'[ERROR] Failed to upload {file_path.name}: {str(file_error)}', 'error')
                # Continue with next file instead of stopping
                continue
        
        # Disconnect after all uploads complete
        if client:
            await client.disconnect()
        
    except Exception as e:
        add_progress_log(job_id, f'[ERROR] Batch upload exception: {str(e)}', 'error')
        import traceback
        add_progress_log(job_id, f'[ERROR] Traceback: {traceback.format_exc()}', 'error')
        if client:
            try:
                await client.disconnect()
            except:
                pass
        raise

async def upload_single_file(file_path, destination, job_id):
    """Upload a single file to Telegram immediately"""
    from telethon.tl.functions.messages import SendMediaRequest
    from telethon.tl.types import InputMediaUploadedDocument, DocumentAttributeFilename
    from parallel_upload import parallel_upload_file
    from datetime import datetime
    from encryption import list_archive_contents
    
    try:
        client = create_telegram_client(config['telegram_api_id'], config['telegram_api_hash'])
        await start_telegram_client(client)
        
        # Convert destination
        dest = destination
        if dest != "me" and dest.lstrip('-').isdigit():
            dest = int(dest)
        
        # Upload file with progress tracking and speed calculation
        import time
        start_time = time.time()
        last_update = [start_time]
        last_bytes = [0]
        
        def upload_progress(current, total):
            now = time.time()
            elapsed = now - last_update[0]
            
            if elapsed >= 0.5:  # Update every 0.5 seconds
                bytes_diff = current - last_bytes[0]
                speed_mbps = (bytes_diff / elapsed) / (1024 * 1024)
                last_update[0] = now
                last_bytes[0] = current
                
                percent = (current / total) * 100
                progress_data[job_id] = f"Uploading: {percent:.1f}% ({speed_mbps:.2f} MB/s) - {file_path.name}"
            elif current == total:  # Final update
                total_time = now - start_time
                avg_speed = (total / total_time) / (1024 * 1024) if total_time > 0 else 0
                progress_data[job_id] = f"Uploading: 100.0% ({avg_speed:.2f} MB/s) - {file_path.name}"
        
        uploaded_file, file_size = await parallel_upload_file(
            client, str(file_path), upload_progress,
            max_connections=config.get('parallel_connections', 20)
        )
        
        # Generate caption
        caption_mode = config.get("upload_caption", "detailed")
        if caption_mode == "none":
            caption = ""
        elif caption_mode == "minimal":
            caption = f"📦 {file_path.name}"
        else:  # detailed
            file_size_mb = file_path.stat().st_size / (1024 * 1024)
            created_date = datetime.fromtimestamp(file_path.stat().st_ctime).strftime("%Y-%m-%d %H:%M")
            upload_date = datetime.now().strftime("%Y-%m-%d %H:%M")
            
            # Detect if this is a split part and if it's encrypted
            import re
            part_match = re.search(r'\.7z\.(\d+)$', file_path.name)
            is_encrypted = file_path.suffix == '.7z' or '.7z.' in file_path.name or file_path.name.endswith('.7z')
            
            if part_match:
                part_num = int(part_match.group(1))
                # Count total parts in the same directory
                base_name = re.sub(r'\.7z\.\d+$', '', file_path.name)
                total_parts = len(list(file_path.parent.glob(f"{base_name}.7z.*")))
                caption = f"📦 **Part {part_num} of {total_parts}**\n"
                caption += f"**File:** {file_path.name}\n"
            else:
                caption = f"📦 **File:** {file_path.name}\n"
            
            caption += f"📊 **Size:** {file_size_mb:.2f} MB\n"
            
            if is_encrypted:
                caption += f"🔒 **Encrypted:** Yes\n"
                try:
                    contents = list_archive_contents(str(file_path), config.get('password'))
                    if contents:
                        caption += f"\n📁 **Archive Contents:**\n"
                        for item in contents[:10]:
                            # Get original file metadata if it's a video
                            file_name = item['name']
                            file_size_str = item['size']
                            
                            # Try to get video metadata
                            if file_name.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.flv', '.wmv', '.webm')):
                                # Try to find the original file to get metadata
                                original_path = WORKSPACE_DIR / file_name
                                if original_path.exists():
                                    try:
                                        import subprocess
                                        result = subprocess.run(
                                            ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', str(original_path)],
                                            capture_output=True, text=True, timeout=5
                                        )
                                        if result.returncode == 0:
                                            import json
                                            metadata = json.loads(result.stdout)
                                            
                                            # Extract video info
                                            video_stream = next((s for s in metadata.get('streams', []) if s.get('codec_type') == 'video'), None)
                                            format_info = metadata.get('format', {})
                                            
                                            if video_stream:
                                                width = video_stream.get('width', 'N/A')
                                                height = video_stream.get('height', 'N/A')
                                                codec = video_stream.get('codec_name', 'N/A')
                                                duration = float(format_info.get('duration', 0))
                                                duration_str = f"{int(duration//60)}:{int(duration%60):02d}" if duration > 0 else 'N/A'
                                                
                                                caption += f"  🎬 **{file_name}** ({file_size_str})\n"
                                                caption += f"     • Resolution: {width}x{height}\n"
                                                caption += f"     • Codec: {codec}\n"
                                                caption += f"     • Duration: {duration_str}\n"
                                            else:
                                                caption += f"  • {file_name} ({file_size_str})\n"
                                        else:
                                            caption += f"  • {file_name} ({file_size_str})\n"
                                    except:
                                        caption += f"  • {file_name} ({file_size_str})\n"
                                else:
                                    caption += f"  • {file_name} ({file_size_str})\n"
                            else:
                                caption += f"  • {file_name} ({file_size_str})\n"
                        
                        if len(contents) > 10:
                            caption += f"  ... and {len(contents) - 10} more\n"
                except:
                    pass
            else:
                caption += f"⚠️ **Encrypted:** No (Unprotected)\n"
            
            caption += f"\n📅 **Created:** {created_date}\n"
            caption += f"⬆️ **Uploaded:** {upload_date}"
        
        # Send to Telegram
        media = InputMediaUploadedDocument(
            file=uploaded_file,
            mime_type='application/x-7z-compressed',
            attributes=[DocumentAttributeFilename(file_name=file_path.name)]
        )
        
        await client(SendMediaRequest(
            peer=dest,
            media=media,
            message=caption
        ))
        
        await client.disconnect()
    except Exception as e:
        add_progress_log(job_id, f'[ERROR] Upload exception: {str(e)}', 'error')
        import traceback
        add_progress_log(job_id, f'[ERROR] Traceback: {traceback.format_exc()}', 'error')
        raise


@app.route('/telegram-upload-raw', methods=['POST'])
def telegram_upload_raw():
    """Upload raw files directly to Telegram without archiving"""
    data = request.json
    files = data.get('files', [])
    custom_dest = data.get('destination')
    job_id = str(int(time.time() * 1000))
    
    if not config.get('telegram_api_id') or not config.get('telegram_api_hash'):
        return jsonify({'error': 'Telegram credentials not set'}), 400
    
    file_paths = [WORKSPACE_DIR / f for f in files if (WORKSPACE_DIR / f).exists()]
    if not file_paths:
        return jsonify({'error': 'No valid files'}), 400
    
    # Check file sizes - raw files cannot be split
    split_size_mb = config.get('split_size_mb', 2000)
    split_size_bytes = split_size_mb * 1024 * 1024
    oversized_files = []
    
    for file_path in file_paths:
        file_size_bytes = file_path.stat().st_size
        if file_size_bytes > split_size_bytes:
            file_size_mb = file_size_bytes / (1024 * 1024)
            oversized_files.append(f"{file_path.name} ({file_size_mb:.0f}MB)")
    
    if oversized_files:
        error_msg = f"Cannot upload raw files larger than {split_size_mb}MB. Files exceeding limit: {', '.join(oversized_files)}. Enable 'Bundle' or 'Encrypt' to split large files."
        return jsonify({'error': error_msg}), 400
    
    progress_data[job_id] = f"Uploading {len(file_paths)} raw file(s) to Telegram..."
    progress_logs[job_id] = [
        {'msg': f'[UPLOAD] Starting upload of {len(file_paths)} raw file(s)...', 'type': 'info'}
    ]
    
    def upload_task():
        try:
            async def upload_with_progress():
                from telethon import TelegramClient
                from telethon.tl.functions.messages import SendMediaRequest
                from telethon.tl.types import InputMediaUploadedDocument, DocumentAttributeFilename
                from parallel_upload import parallel_upload_file
                
                session_file = WORKSPACE_DIR.parent / "dailyarchive_session"
                client = TelegramClient(
                    str(session_file),
                    int(config['telegram_api_id']),
                    config['telegram_api_hash']
                )
                
                await client.start()
                
                dest = custom_dest if custom_dest else config.get('upload_destination', 'me')
                if dest != "me" and dest.lstrip('-').isdigit():
                    dest = int(dest)
                
                for i, file_path in enumerate(file_paths, 1):
                    add_progress_log(job_id, f'[{i}/{len(file_paths)}] Uploading {file_path.name}...', 'info')
                    
                    import time
                    start_time = time.time()
                    last_update = [start_time]
                    last_bytes = [0]
                    
                    def file_progress(current, total):
                        now = time.time()
                        elapsed = now - last_update[0]
                        
                        if elapsed >= 0.5:
                            bytes_diff = current - last_bytes[0]
                            speed_mbps = (bytes_diff / elapsed) / (1024 * 1024)
                            last_update[0] = now
                            last_bytes[0] = current
                            
                            percent = (current / total) * 100
                            progress_data[job_id] = f"Uploading [{i}/{len(file_paths)}]: {percent:.1f}% ({speed_mbps:.2f} MB/s) - {file_path.name}"
                    
                    uploaded_file, _ = await parallel_upload_file(
                        client, str(file_path), file_progress,
                        max_connections=config.get('parallel_connections', 20)
                    )
                    
                    # Generate caption
                    caption_mode = config.get("upload_caption", "detailed")
                    
                    if caption_mode == "none":
                        caption = ""
                    elif caption_mode == "minimal":
                        caption = f"📄 {file_path.name}"
                    else:  # detailed
                        from datetime import datetime
                        file_size = file_path.stat().st_size / (1024 * 1024)
                        created_date = datetime.fromtimestamp(file_path.stat().st_ctime).strftime("%Y-%m-%d %H:%M")
                        upload_date = datetime.now().strftime("%Y-%m-%d %H:%M")
                        
                        caption = f"📄 {file_path.name}\n"
                        caption += f"📊 Size: {file_size:.1f} MB\n"
                        caption += f"⚠️ Encrypted: No (Unprotected)\n"
                        caption += f"📅 Created: {created_date}\n"
                        caption += f"⬆️ Uploaded: {upload_date}"
                    
                    media = InputMediaUploadedDocument(
                        file=uploaded_file,
                        mime_type='application/octet-stream',
                        attributes=[DocumentAttributeFilename(file_path.name)],
                    )
                    
                    await client(SendMediaRequest(
                        peer=dest,
                        media=media,
                        message=caption
                    ))
                    
                    add_progress_log(job_id, f'[OK] Uploaded {file_path.name}', 'success')
                
                await client.disconnect()
            
            asyncio.run(upload_with_progress())
            progress_data[job_id] = "COMPLETE"
            add_progress_log(job_id, f'[OK] Uploaded {len(file_paths)} file(s) successfully', 'success')
        except Exception as e:
            progress_data[job_id] = f"ERROR: {str(e)}"
            add_progress_log(job_id, f'[ERROR] Upload failed: {str(e)}', 'error')
    
    thread = threading.Thread(target=upload_task)
    thread.start()
    
    return jsonify({'success': True, 'job_id': job_id, 'files': len(file_paths)})

@app.route('/telegram-upload', methods=['POST'])
def telegram_upload():
    """Upload to Telegram"""
    data = request.json
    folder = data.get('folder')
    custom_dest = data.get('destination')  # Custom destination for this upload
    job_id = str(int(time.time() * 1000))
    
    if not config.get('telegram_api_id') or not config.get('telegram_api_hash'):
        return jsonify({'error': 'Telegram credentials not set'}), 400
    
    folder_path = WORKSPACE_DIR / folder
    if not folder_path.exists():
        return jsonify({'error': 'Folder not found'}), 404
    
    # Get archive files (check root and subfolders for separate files)
    parts = sorted(folder_path.glob('*.7z.*'))
    if not parts:
        parts = sorted(folder_path.glob('*.7z'))
    
    # If no files in root, check subfolders (for separately processed files)
    if not parts:
        parts = sorted(folder_path.glob('**/*.7z'))
        if not parts:
            parts = sorted(folder_path.glob('**/*.7z.*'))
    
    if not parts:
        return jsonify({'error': 'No archive files found'}), 404
    
    return _telegram_upload_files_internal(parts, custom_dest, job_id)

@app.route('/telegram-upload-files', methods=['POST'])
def telegram_upload_files():
    """Upload specific files to Telegram"""
    data = request.json
    files = data.get('files', [])
    custom_dest = data.get('destination')
    job_id = str(int(time.time() * 1000))
    
    if not config.get('telegram_api_id') or not config.get('telegram_api_hash'):
        return jsonify({'error': 'Telegram credentials not set'}), 400
    
    if not files:
        return jsonify({'error': 'No files specified'}), 400
    
    # Convert file paths to Path objects
    parts = [WORKSPACE_DIR / f for f in files if (WORKSPACE_DIR / f).exists()]
    
    if not parts:
        return jsonify({'error': 'No valid files found'}), 404
    
    return _telegram_upload_files_internal(parts, custom_dest, job_id)

def _telegram_upload_files_internal(parts, custom_dest, job_id):
    """Internal function to upload files to Telegram"""
    # Initialize progress
    progress_data[job_id] = f"Uploading {len(parts)} file(s) to Telegram..."
    progress_logs[job_id] = [
        {'msg': f'[UPLOAD] Starting upload of {len(parts)} file(s) to Telegram...', 'type': 'info'}
    ]
    
    def upload_task():
        try:
            # Track progress
            current_file = [0]  # Use list to allow modification in nested function
            
            async def upload_with_progress():
                from telethon import TelegramClient
                from telethon.tl.functions.messages import SendMediaRequest
                from telethon.tl.types import InputMediaUploadedDocument, DocumentAttributeFilename
                from parallel_upload import parallel_upload_file
                
                # Session file stays in root, not workspace
                session_file = WORKSPACE_DIR.parent / "dailyarchive_session"
                client = TelegramClient(
                    str(session_file),
                    int(config['telegram_api_id']),
                    config['telegram_api_hash']
                )
                
                await client.start()
                
                # Convert destination to int if it's a channel ID
                # Use custom destination if provided, otherwise use default from config
                dest = custom_dest if custom_dest else config.get('upload_destination', 'me')
                if dest != "me" and dest.lstrip('-').isdigit():
                    dest = int(dest)
                
                for i, part in enumerate(parts, 1):
                    current_file[0] = i
                    add_progress_log(job_id, f'[{i}/{len(parts)}] Uploading {part.name}...', 'info')
                    
                    import time
                    start_time = time.time()
                    last_update = [start_time]
                    last_bytes = [0]
                    
                    def file_progress(current, total):
                        now = time.time()
                        elapsed = now - last_update[0]
                        
                        if elapsed >= 0.5:
                            bytes_diff = current - last_bytes[0]
                            speed_mbps = (bytes_diff / elapsed) / (1024 * 1024)
                            last_bytes[0] = current
                            
                            percent = (current / total) * 100
                            progress_data[job_id] = f"Uploading [{i}/{len(parts)}]: {percent:.1f}% ({speed_mbps:.2f} MB/s) - {part.name}"
                    
                    uploaded_file, _ = await parallel_upload_file(
                        client, str(part), file_progress,
                        max_connections=config.get('parallel_connections', 20)
                    )
                    
                    # Generate caption based on settings
                    caption_mode = config.get("upload_caption", "detailed")
                    
                    if caption_mode == "none":
                        caption = ""
                    elif caption_mode == "minimal":
                        caption = f"📦 {part.name}"
                    else:  # detailed
                        from datetime import datetime
                        file_size = part.stat().st_size / (1024 * 1024)
                        created_date = datetime.fromtimestamp(part.stat().st_ctime).strftime("%Y-%m-%d %H:%M")
                        upload_date = datetime.now().strftime("%Y-%m-%d %H:%M")
                        
                        parent_folder = part.parent.name
                        
                        # Detect if encrypted based on file extension
                        is_encrypted = part.suffix == '.7z' or '.7z.' in part.name
                        
                        # Detect if this is a split part
                        import re
                        part_match = re.search(r'\.7z\.(\d+)$', part.name)
                        if part_match:
                            part_num = int(part_match.group(1))
                            base_name = re.sub(r'\.7z\.\d+$', '', part.name)
                            total_parts = len(list(part.parent.glob(f"{base_name}.7z.*")))
                            caption = f"📦 Part {part_num} of {total_parts}\n"
                        else:
                            caption = f"📦 {part.name}\n"
                        
                        caption += f"📁 Archive: {part.name}\n"
                        
                        # Try to get source from parent folder structure
                        try:
                            relative_path = part.relative_to(WORKSPACE_DIR)
                            if len(relative_path.parts) > 1:
                                source_hint = str(relative_path.parent)
                                caption += f"📂 Source: {source_hint}\n"
                        except:
                            pass
                        
                        caption += f"📊 Size: {file_size:.1f} MB\n"
                        
                        # Check if encrypted
                        if is_encrypted:
                            caption += f"🔒 Encrypted: Yes\n"
                        else:
                            caption += f"Encrypted: No (Unprotected)\n"
                        
                        # List archive contents if encrypted
                        if is_encrypted:
                            try:
                                from encryption import list_archive_contents
                                password = config.get('password')
                                if password:
                                    contents = list_archive_contents(str(part), password)
                                    if contents:
                                        caption += f"\nContents ({len(contents)} file(s)):\n"
                                        for file_info in contents[:10]:
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
                                            
                                            caption += f"• {file_info['name']} ({size_str})\n"
                                        
                                        if len(contents) > 10:
                                            caption += f"... and {len(contents) - 10} more\n"
                            except:
                                pass
                        
                        caption += f"\n� Createed: {created_date}\n"
                        caption += f"⬆️ Uploaded: {upload_date}"
                    
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
                    
                    add_progress_log(job_id, f'[OK] Uploaded {part.name}', 'success')
                
                await client.disconnect()
            
            asyncio.run(upload_with_progress())
            progress_data[job_id] = "COMPLETE"
            add_progress_log(job_id, f'[OK] Uploaded {len(parts)} file(s) successfully', 'success')
        except Exception as e:
            progress_data[job_id] = f"ERROR: {str(e)}"
            add_progress_log(job_id, f'[ERROR] Upload failed: {str(e)}', 'error')
    
    thread = threading.Thread(target=upload_task)
    thread.start()
    
    return jsonify({'success': True, 'job_id': job_id, 'parts': len(parts)})

@app.route('/decrypt', methods=['POST'])
def decrypt():
    """Decrypt archive"""
    data = request.json
    folder = data.get('folder')
    password = data.get('password') or config.get('password')
    
    if not password:
        return jsonify({'error': 'No password provided'}), 400
    
    folder_path = WORKSPACE_DIR / folder
    if not folder_path.exists():
        return jsonify({'error': 'Folder not found'}), 404
    
    # Find archive
    archives = list(folder_path.glob('*.7z.001')) or list(folder_path.glob('*.7z'))
    if not archives:
        return jsonify({'error': 'No archive found'}), 404
    
    success = decrypt_and_extract(archives[0], folder_path, password)
    
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Decryption failed'}), 500

@app.route('/generate-passphrase')
def generate_passphrase():
    """Generate a BIP39-style 12-word mnemonic"""
    from mnemonic import Mnemonic
    mnemo = Mnemonic("english")
    words = mnemo.generate(strength=128)  # 128 bits = 12 words
    # Replace spaces with hyphens
    passphrase = words.replace(' ', '-')
    return jsonify({'passphrase': passphrase})

@app.route('/save-old-password', methods=['POST'])
def save_old_password():
    """Save old password to a text file"""
    data = request.json
    old_password = data.get('old_password')
    
    if not old_password:
        return jsonify({'error': 'No password provided'}), 400
    
    try:
        # Save to archive/old_passwords.txt
        password_file = WORKSPACE_DIR / 'old_passwords.txt'
        
        # Append with timestamp
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        with open(password_file, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] {old_password}\n")
        
        return jsonify({'success': True, 'file': 'archive/old_passwords.txt'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/verify-upload-config', methods=['POST'])
def verify_upload_config():
    """Verify that upload configuration is valid before starting upload"""
    data = request.json
    destination = data.get('destination', '')
    
    # Check API credentials
    if not config.get('telegram_api_id') or not config.get('telegram_api_hash'):
        return jsonify({
            'valid': False,
            'error': 'Telegram API credentials not set',
            'missing': ['api_id', 'api_hash']
        }), 400
    
    # Check if destination is valid (not empty after fallback)
    final_dest = destination if destination and destination.strip() else config.get('upload_destination', 'me')
    if not final_dest or final_dest.strip() == '':
        final_dest = 'me'
    
    # Check if session exists (user is logged in)
    session_file = WORKSPACE_DIR.parent / "dailyarchive_session.session"
    if not session_file.exists():
        return jsonify({
            'valid': False,
            'error': 'Not logged in to Telegram. Please go to Settings and save your credentials to login.',
            'missing': ['telegram_session']
        }), 401
    
    return jsonify({
        'valid': True,
        'destination': final_dest,
        'message': f'Upload will go to: {final_dest}'
    })


@app.route('/settings', methods=['GET', 'POST'])
def settings():
    """Settings page"""
    if request.method == 'POST':
        data = request.json
        
        if 'password' in data:
            config['password'] = data['password']
        if 'telegram_api_id' in data:
            config['telegram_api_id'] = data['telegram_api_id']
        if 'telegram_api_hash' in data:
            config['telegram_api_hash'] = data['telegram_api_hash']
        if 'upload_destination' in data:
            config['upload_destination'] = data['upload_destination']
        if 'upload_caption' in data:
            config['upload_caption'] = data['upload_caption']
        if 'split_size_mb' in data:
            config['split_size_mb'] = data['split_size_mb']
        if 'video_keep_audio' in data:
            config['video_keep_audio'] = data['video_keep_audio']
        if 'cpu_preset' in data:
            config['cpu_preset'] = data['cpu_preset']
        if 'cpu_threads' in data:
            config['cpu_threads'] = data['cpu_threads']
        if 'parallel_connections' in data:
            config['parallel_connections'] = data['parallel_connections']
        
        save_config()
        return jsonify({'success': True})
    
    return jsonify(config)

@app.route('/logout', methods=['POST'])
def logout():
    """Logout from Telegram and delete session"""
    try:
        session_file = WORKSPACE_DIR.parent / "dailyarchive_session.session"
        if session_file.exists():
            session_file.unlink()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/reset', methods=['POST'])
def reset():
    """Reset all settings to defaults and save old password"""
    try:
        old_password = config.get('password', '')
        
        if old_password:
            old_passwords_file = WORKSPACE_DIR / "old_passwords.txt"
            with open(old_passwords_file, 'a', encoding='utf-8') as f:
                from datetime import datetime
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                f.write(f"[{timestamp}] {old_password}\n")
        
        session_file = WORKSPACE_DIR.parent / "dailyarchive_session.session"
        if session_file.exists():
            session_file.unlink()
        
        from config import DEFAULT_CONFIG
        config.clear()
        config.update(DEFAULT_CONFIG)
        save_config()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/folders')
def list_folders():
    """List processed folders"""
    folders = []
    for item in WORKSPACE_DIR.iterdir():
        if item.is_dir() and item.name[0].isdigit():
            parts = list(item.glob('*.7z.*')) or list(item.glob('*.7z'))
            folders.append({
                'name': item.name,
                'files': len(parts),
                'created': datetime.fromtimestamp(item.stat().st_ctime).strftime('%Y-%m-%d %H:%M')
            })
    return jsonify(sorted(folders, key=lambda x: x['name'], reverse=True))

@app.route('/telegram-archives')
def telegram_archives():
    """Fetch archives from Telegram"""
    if not config.get('telegram_api_id') or not config.get('telegram_api_hash'):
        return jsonify({'error': 'Telegram credentials not set'}), 400
    
    try:
        archives = asyncio.run(fetch_telegram_archives(
            config['telegram_api_id'],
            config['telegram_api_hash'],
            config.get('upload_destination', 'me'),
            WORKSPACE_DIR
        ))
        return jsonify({'success': True, 'archives': archives})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/telegram-channels')
def telegram_channels():
    """List available Telegram channels/chats"""
    if not config.get('telegram_api_id') or not config.get('telegram_api_hash'):
        return jsonify({'error': 'Telegram credentials not set'}), 400
    
    try:
        async def get_channels():
            from telethon.tl.types import Channel
            
            client = create_telegram_client(config['telegram_api_id'], config['telegram_api_hash'])
            
            # Connect without starting (no interactive prompts)
            await client.connect()
            
            # Check if already authorized
            if not await client.is_user_authorized():
                await client.disconnect()
                return {'error': 'Not logged in. Please login first.', 'needs_login': True}
            
            channels = []
            
            # Add "Saved Messages" as default
            channels.append({
                'id': 'me',
                'name': 'Saved Messages',
                'type': 'self'
            })
            
            # Get all dialogs (chats, channels, groups)
            async for dialog in client.iter_dialogs():
                entity = dialog.entity
                
                # Only include channels where user is creator/admin
                if isinstance(entity, Channel):
                    # Check if user has admin rights (creator or admin)
                    if entity.creator or (hasattr(entity, 'admin_rights') and entity.admin_rights):
                        channel_info = {
                            'id': str(entity.id) if entity.id > 0 else str(-100 + entity.id),  # Convert to proper format
                            'name': dialog.name or 'Unknown',
                            'type': 'channel'
                        }
                        channels.append(channel_info)
            
            await client.disconnect()
            return {'success': True, 'channels': channels}
        
        result = asyncio.run(get_channels())
        if 'needs_login' in result:
            return jsonify(result), 401
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/telegram-login-send-code', methods=['POST'])
def telegram_login_send_code():
    """Send OTP code to phone number"""
    data = request.json
    phone = data.get('phone')
    
    if not phone:
        return jsonify({'error': 'Phone number required'}), 400
    
    if not config.get('telegram_api_id') or not config.get('telegram_api_hash'):
        return jsonify({'error': 'Telegram credentials not set'}), 400
    
    try:
        async def send_code():
            client = create_telegram_client(config['telegram_api_id'], config['telegram_api_hash'])
            
            await client.connect()
            
            # Send code request
            result = await client.send_code_request(phone)
            phone_code_hash = result.phone_code_hash
            
            await client.disconnect()
            
            return {'success': True, 'phone_code_hash': phone_code_hash}
        
        result = asyncio.run(send_code())
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/telegram-login-verify', methods=['POST'])
def telegram_login_verify():
    """Verify OTP code and complete login"""
    data = request.json
    phone = data.get('phone')
    code = data.get('code')
    phone_code_hash = data.get('phone_code_hash')
    
    if not phone or not code or not phone_code_hash:
        return jsonify({'error': 'Phone, code, and phone_code_hash required'}), 400
    
    if not config.get('telegram_api_id') or not config.get('telegram_api_hash'):
        return jsonify({'error': 'Telegram credentials not set'}), 400
    
    try:
        async def verify_code():
            client = create_telegram_client(config['telegram_api_id'], config['telegram_api_hash'])
            
            await client.connect()
            
            # Sign in with code
            await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            
            # Get user info
            me = await client.get_me()
            
            # Ensure session is saved before disconnecting
            await client.disconnect()
            
            return {'success': True, 'user': {'id': me.id, 'name': f"{me.first_name} {me.last_name or ''}".strip()}}
        
        result = asyncio.run(verify_code())
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/telegram-login-status')
def telegram_login_status():
    """Check if user is logged in to Telegram"""
    if not config.get('telegram_api_id') or not config.get('telegram_api_hash'):
        return jsonify({'logged_in': False, 'error': 'Telegram credentials not set'})
    
    try:
        async def check_status():
            client = create_telegram_client(config['telegram_api_id'], config['telegram_api_hash'])
            
            await client.connect()
            is_authorized = await client.is_user_authorized()
            
            user_info = None
            if is_authorized:
                me = await client.get_me()
                user_info = {'id': me.id, 'name': f"{me.first_name} {me.last_name or ''}".strip(), 'phone': me.phone}
            
            await client.disconnect()
            
            return {'logged_in': is_authorized, 'user': user_info}
        
        result = asyncio.run(check_status())
        return jsonify(result)
    except Exception as e:
        return jsonify({'logged_in': False, 'error': str(e)})

@app.route('/telegram-download', methods=['POST'])
def telegram_download():
    """Download archive from Telegram"""
    data = request.json
    archive_id = data.get('archive_id')
    decrypt = data.get('decrypt', False)
    delete_after_decrypt = data.get('delete_after_decrypt', False)
    job_id = str(int(time.time() * 1000))
    
    if not config.get('telegram_api_id') or not config.get('telegram_api_hash'):
        return jsonify({'error': 'Telegram credentials not set'}), 400
    
    # Initialize progress
    progress_data[job_id] = f"Starting download of {archive_id}..."
    progress_logs[job_id] = [
        {'msg': f'[DOWNLOAD] Starting download of archive: {archive_id}', 'type': 'info'},
        {'msg': f'[DEBUG] Decrypt option: {decrypt}', 'type': 'info'}
    ]
    
    def download_task():
        try:
            async def download_with_progress():
                from telethon import TelegramClient
                import re
                
                session_file = WORKSPACE_DIR.parent / "dailyarchive_session"
                client = TelegramClient(str(session_file), int(config['telegram_api_id']), config['telegram_api_hash'])
                
                await client.start()
                
                # Convert destination
                dest = config.get('upload_destination', 'me')
                if dest != "me" and dest.lstrip('-').isdigit():
                    dest = int(dest)
                
                # Create download folder inside archive/Downloaded/
                downloads_root = WORKSPACE_DIR / "Downloaded"
                downloads_root.mkdir(exist_ok=True)
                download_dir = downloads_root / archive_id
                download_dir.mkdir(exist_ok=True)
                
                # Fetch and download matching files
                files_to_download = []
                async for message in client.iter_messages(dest, limit=1000):
                    if message.document and message.file.name:
                        filename = message.file.name
                        match = re.match(r'(.+?)\.7z(?:\.(\d+))?$', filename)
                        
                        if match and match.group(1) == archive_id:
                            files_to_download.append((message, filename))
                
                if not files_to_download:
                    raise Exception(f"No files found for archive: {archive_id}")
                
                add_progress_log(job_id, f'[DOWNLOAD] Found {len(files_to_download)} file(s) to download', 'info')
                
                # Download each file with progress
                for i, (message, filename) in enumerate(files_to_download, 1):
                    file_size = message.file.size
                    
                    def file_progress(current, total):
                        import time
                        percent = (current / total) * 100
                        speed_mbps = 0  # Calculate if needed
                        progress_data[job_id] = f"Downloading [{i}/{len(files_to_download)}]: {percent:.1f}% - {filename}"
                    
                    add_progress_log(job_id, f'[{i}/{len(files_to_download)}] Downloading {filename}...', 'info')
                    
                    # Use parallel download for speed
                    from parallel_upload import parallel_download_file
                    import time
                    start_time = time.time()
                    last_update = [start_time]
                    last_bytes = [0]
                    
                    def file_progress(current, total):
                        now = time.time()
                        elapsed = now - last_update[0]
                        
                        # Update speed every 0.5 seconds
                        if elapsed >= 0.5:
                            bytes_diff = current - last_bytes[0]
                            speed_mbps = (bytes_diff / elapsed) / (1024 * 1024)  # MB/s
                            last_update[0] = now
                            last_bytes[0] = current
                            
                            percent = (current / total) * 100
                            progress_data[job_id] = f"Downloading [{i}/{len(files_to_download)}]: {percent:.1f}% ({speed_mbps:.2f} MB/s) - {filename}"
                    
                    await parallel_download_file(client, message, str(download_dir / filename), file_progress)
                    add_progress_log(job_id, f'[OK] Downloaded {filename}', 'success')
                
                await client.disconnect()
                
                # Decrypt if requested
                add_progress_log(job_id, f'[DEBUG] Decrypt flag is: {decrypt}', 'info')
                if decrypt:
                    progress_data[job_id] = "Decrypting archive..."
                    add_progress_log(job_id, '[DECRYPT] Starting decryption...', 'info')
                    
                    # Find the archive file - prioritize .7z.001 (split archives), then .7z files
                    archives = list(download_dir.glob('*.7z.001'))
                    if not archives:
                        archives = list(download_dir.glob('*.7z'))
                    
                    add_progress_log(job_id, f'[DEBUG] Found {len(archives)} archive(s) to decrypt', 'info')
                    if archives:
                        add_progress_log(job_id, f'[DEBUG] Archives: {[a.name for a in archives]}', 'info')
                        from encryption import decrypt_and_extract
                        password = config.get('password')
                        if not password:
                            raise Exception("No password set for decryption")
                        
                        # Decrypt each archive found
                        for archive in archives:
                            add_progress_log(job_id, f'[DECRYPT] Decrypting {archive.name}...', 'info')
                            
                            def decrypt_progress(msg):
                                progress_data[job_id] = msg
                            
                            success = decrypt_and_extract(archive, download_dir, password, decrypt_progress)
                            if success:
                                add_progress_log(job_id, f'[OK] Decrypted {archive.name}', 'success')
                            else:
                                raise Exception(f"Decryption failed for {archive.name}")
                        
                        # Delete .7z files if requested
                        if delete_after_decrypt:
                            add_progress_log(job_id, '[CLEANUP] Deleting .7z files...', 'info')
                            for archive_file in download_dir.glob('*.7z*'):
                                archive_file.unlink()
                            add_progress_log(job_id, '[OK] Deleted .7z files', 'success')
                    else:
                        add_progress_log(job_id, '[WARNING] No .7z archives found to decrypt', 'warning')
                
                return download_dir
            
            path = asyncio.run(download_with_progress())
            progress_data[job_id] = "COMPLETE"
            progress_data[f"{job_id}_result"] = {'path': str(path)}
            add_progress_log(job_id, f'[OK] Download complete: {path.name}', 'success')
        except Exception as e:
            progress_data[job_id] = f"ERROR: {str(e)}"
            add_progress_log(job_id, f'[ERROR] Download failed: {str(e)}', 'error')
    
    thread = threading.Thread(target=download_task)
    thread.start()
    
    return jsonify({'success': True, 'job_id': job_id})

@app.route('/telegram-delete', methods=['POST'])
def telegram_delete():
    """Delete archive from Telegram"""
    data = request.json
    archive_id = data.get('archive_id')
    
    if not config.get('telegram_api_id') or not config.get('telegram_api_hash'):
        return jsonify({'error': 'Telegram credentials not set'}), 400
    
    try:
        deleted = asyncio.run(delete_telegram_archive(
            archive_id,
            config['telegram_api_id'],
            config['telegram_api_hash'],
            config.get('upload_destination', 'me'),
            WORKSPACE_DIR
        ))
        return jsonify({'success': True, 'deleted': deleted})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/delete', methods=['POST'])
def delete_file():
    """Delete file or folder"""
    data = request.json
    path = data.get('path')
    
    if not path:
        return jsonify({'error': 'No path provided'}), 400
    
    full_path = WORKSPACE_DIR / path
    if not full_path.exists():
        return jsonify({'error': 'Path not found'}), 404
    
    try:
        if full_path.is_file():
            full_path.unlink()
        else:
            import shutil
            shutil.rmtree(full_path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/telegram-download-single', methods=['POST'])
def telegram_download_single():
    """Download a single file from Telegram"""
    data = request.json
    archive_id = data.get('archive_id')
    filename = data.get('filename')
    message_id = data.get('message_id')
    decrypt = data.get('decrypt', False)
    delete_after_decrypt = data.get('delete_after_decrypt', False)
    job_id = str(int(time.time() * 1000))
    
    if not config.get('telegram_api_id') or not config.get('telegram_api_hash'):
        return jsonify({'error': 'Telegram credentials not set'}), 400
    
    # Initialize progress
    progress_data[job_id] = f"Starting download of {filename}..."
    progress_logs[job_id] = [
        {'msg': f'[DOWNLOAD] Starting download: {filename}', 'type': 'info'},
        {'msg': f'[DEBUG] Decrypt option: {decrypt}', 'type': 'info'}
    ]
    
    def download_task():
        try:
            async def download_with_progress():
                from telethon import TelegramClient
                
                session_file = WORKSPACE_DIR.parent / "dailyarchive_session"
                client = TelegramClient(str(session_file), int(config['telegram_api_id']), config['telegram_api_hash'])
                
                await client.start()
                
                # Convert destination
                dest = config.get('upload_destination', 'me')
                if dest != "me" and dest.lstrip('-').isdigit():
                    dest = int(dest)
                
                # Create download folder
                downloads_root = WORKSPACE_DIR / "Downloaded"
                downloads_root.mkdir(exist_ok=True)
                download_dir = downloads_root / archive_id
                download_dir.mkdir(exist_ok=True)
                
                # Get the specific message
                message = await client.get_messages(dest, ids=message_id)
                
                if not message or not message.document:
                    raise Exception(f"File not found: {filename}")
                
                # Download with progress
                from parallel_upload import parallel_download_file
                import time
                start_time = time.time()
                last_update = [start_time]
                last_bytes = [0]
                
                def file_progress(current, total):
                    now = time.time()
                    elapsed = now - last_update[0]
                    
                    if elapsed >= 0.5:
                        bytes_diff = current - last_bytes[0]
                        speed_mbps = (bytes_diff / elapsed) / (1024 * 1024)
                        last_update[0] = now
                        last_bytes[0] = current
                        
                        percent = (current / total) * 100
                        progress_data[job_id] = f"Downloading: {percent:.1f}% ({speed_mbps:.2f} MB/s) - {filename}"
                
                await parallel_download_file(client, message, str(download_dir / filename), file_progress)
                add_progress_log(job_id, f'[OK] Downloaded {filename}', 'success')
                
                await client.disconnect()
                
                # Decrypt if requested and file is a .7z archive
                if decrypt and (filename.endswith('.7z') or '.7z.' in filename):
                    progress_data[job_id] = "Decrypting archive..."
                    add_progress_log(job_id, '[DECRYPT] Starting decryption...', 'info')
                    
                    from encryption import decrypt_and_extract
                    password = config.get('password')
                    if not password:
                        raise Exception("No password set for decryption")
                    
                    archive_path = download_dir / filename
                    add_progress_log(job_id, f'[DECRYPT] Decrypting {filename}...', 'info')
                    
                    def decrypt_progress(msg):
                        progress_data[job_id] = msg
                    
                    success = decrypt_and_extract(archive_path, download_dir, password, decrypt_progress)
                    if success:
                        add_progress_log(job_id, f'[OK] Decrypted {filename}', 'success')
                        
                        # Delete .7z file if requested
                        if delete_after_decrypt:
                            add_progress_log(job_id, '[CLEANUP] Deleting .7z file...', 'info')
                            archive_path.unlink()
                            add_progress_log(job_id, '[OK] Deleted .7z file', 'success')
                    else:
                        raise Exception(f"Decryption failed for {filename}")
                
                return download_dir
            
            path = asyncio.run(download_with_progress())
            progress_data[job_id] = "COMPLETE"
            progress_data[f"{job_id}_result"] = {'path': str(path / filename)}
            add_progress_log(job_id, f'[OK] Download complete: {archive_id}', 'success')
        except Exception as e:
            progress_data[job_id] = f"ERROR: {str(e)}"
            add_progress_log(job_id, f'[ERROR] Download failed: {str(e)}', 'error')
    
    thread = threading.Thread(target=download_task)
    thread.start()
    
    return jsonify({'success': True, 'job_id': job_id})

@app.route('/downloaded')
def list_downloaded():
    """List downloaded folders"""
    downloads_root = WORKSPACE_DIR / "Downloaded"
    folders = []
    
    if downloads_root.exists():
        for item in downloads_root.iterdir():
            if item.is_dir():
                # Get folder info
                files = list(item.rglob('*'))
                file_count = sum(1 for f in files if f.is_file())
                total_size = sum(f.stat().st_size for f in files if f.is_file())
                
                folders.append({
                    'name': f"Downloaded/{item.name}",
                    'display_name': item.name,
                    'files': file_count,
                    'size': round(total_size / (1024**3), 2),
                    'created': datetime.fromtimestamp(item.stat().st_ctime).strftime('%Y-%m-%d %H:%M')
                })
    
    return jsonify(sorted(folders, key=lambda x: x['created'], reverse=True))

if __name__ == '__main__':
    import logging
    import webbrowser
    import threading
    import os
    
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    # Only open browser and show message in the main process (not reloader)
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        print("\n" + "="*60)
        print("  TEL ARCHIVE - Starting server...")
        print("  Opening browser at http://localhost:5001")
        print("="*60 + "\n")
        
        def open_browser():
            import time
            time.sleep(1.5)
            webbrowser.open('http://localhost:5001')
        
        threading.Thread(target=open_browser, daemon=True).start()
    
    app.run(debug=True, host='0.0.0.0', port=5001)

