"""Encryption and archiving functions - simplified"""
import subprocess
import re
from pathlib import Path
from config import config


def get_file_size_gb(file_path: Path) -> float:
    """Get file size in GB"""
    return file_path.stat().st_size / (1024 ** 3)


def list_archive_contents(archive_path: Path, password: str) -> list:
    """List contents of encrypted archive without extracting"""
    cmd = ["7z", "l", f"-p{password}", "-slt", str(archive_path)]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return []
        
        files = []
        current_file = {}
        is_folder = False
        
        for line in result.stdout.split('\n'):
            line = line.strip()
            
            if line.startswith('Path = '):
                path = line.split(' = ', 1)[1]
                if not path.endswith('.7z') and not path.endswith('.7z.001'):
                    current_file['name'] = path
            elif line.startswith('Size = '):
                size = line.split(' = ', 1)[1]
                current_file['size'] = size
            elif line.startswith('Folder = '):
                folder = line.split(' = ', 1)[1]
                is_folder = (folder == '+')
            elif line == '' and current_file.get('name'):
                if not is_folder:
                    files.append(current_file.copy())
                current_file = {}
                is_folder = False
        
        return files
    except Exception as e:
        return []


def create_archive(input_files: list, output_dir: Path, archive_name: str, 
                   password: str = None, split_size_mb: int = None, 
                   progress_callback=None):
    """
    Create 7z archive with optional encryption and splitting.
    
    Args:
        input_files: List of file paths to archive
        output_dir: Output directory
        archive_name: Name of archive (without extension)
        password: Optional password for encryption
        split_size_mb: Optional split size in MB (None = no split)
        progress_callback: Optional callback for progress updates
    
    Returns:
        Path to archive file, or list of paths if split
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / f"{archive_name}.7z"
    
    # Build command
    cmd = ["7z", "a"]
    
    # Add split if specified
    if split_size_mb:
        cmd.append(f"-v{split_size_mb}m")
        total_size_gb = sum(f.stat().st_size for f in input_files) / (1024 ** 3)
        print(f"\n    Total size: {total_size_gb:.2f} GB")
        print(f"    Part size: {split_size_mb} MB\n")
    
    # Add encryption if password provided
    if password:
        cmd.extend([f"-p{password}", "-mhe=on"])
    
    cmd.extend(["-bsp1", str(archive_path)])
    cmd.extend([str(f) for f in input_files])
    
    # Run 7z
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    
    last_percent = -1
    sent_completion = False
    
    while True:
        line = process.stdout.readline()
        if not line and process.poll() is not None:
            break
        match = re.search(r'(\d+)%', line)
        if match:
            percent = int(match.group(1))
            # Send progress updates below 100%
            if percent != last_percent and percent < 100:
                last_percent = percent
                if progress_callback:
                    action = "Encrypting" if password else "Archiving"
                    progress_callback(f"{action}: {percent}% complete")
            # Send 100% only once when we see it
            elif percent >= 100 and not sent_completion:
                sent_completion = True
                if progress_callback:
                    action = "Encrypting" if password else "Archiving"
                    progress_callback(f"{action}: 100% complete")
    
    # If process completed but we never sent 100%, send it now
    if process.returncode == 0 and not sent_completion and progress_callback:
        action = "Encrypting" if password else "Archiving"
        progress_callback(f"{action}: 100% complete")
    
    if process.returncode != 0:
        return None if not split_size_mb else []
    
    # Return path or list of parts
    if split_size_mb:
        parts = sorted(output_dir.glob(f"{archive_name}.7z.*"))
        return parts
    else:
        return archive_path


def decrypt_and_extract(archive_path: Path, output_dir: Path, password: str, progress_callback=None) -> bool:
    """Extract encrypted archive"""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    cmd = ["7z", "x", f"-p{password}", "-bsp1", "-y", f"-o{output_dir}", str(archive_path)]
    
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    
    last_percent = -1
    while True:
        line = process.stdout.readline()
        if not line and process.poll() is not None:
            break
        match = re.search(r'(\d+)%', line)
        if match:
            percent = int(match.group(1))
            if percent != last_percent:
                last_percent = percent
                if progress_callback:
                    progress_callback(f"Decrypting: {percent}% complete")
    
    if progress_callback:
        progress_callback(f"Decrypting: 100% complete")
    
    return process.returncode == 0


# Legacy function names for compatibility
def encrypt_multiple_files(input_files, output_dir, password, archive_name="archive", progress_callback=None):
    """Legacy: Encrypt multiple files without splitting"""
    return create_archive(input_files, output_dir, archive_name, password=password, 
                         split_size_mb=None, progress_callback=progress_callback)


def split_and_encrypt_multiple(input_files, output_dir, password, archive_name="archive", progress_callback=None):
    """Legacy: Encrypt and split multiple files"""
    split_size_mb = config.get('split_size_mb', 2000)
    return create_archive(input_files, output_dir, archive_name, password=password,
                         split_size_mb=split_size_mb, progress_callback=progress_callback)


def archive_multiple_files_no_password(input_files, output_dir, archive_name="archive", progress_callback=None):
    """Legacy: Archive without password"""
    return create_archive(input_files, output_dir, archive_name, password=None,
                         split_size_mb=None, progress_callback=progress_callback)


def split_archive_no_password(input_files, output_dir, archive_name, split_size_mb, progress_callback=None):
    """Legacy: Archive and split without password"""
    return create_archive(input_files, output_dir, archive_name, password=None,
                         split_size_mb=split_size_mb, progress_callback=progress_callback)


def encrypt_file(input_file, output_dir, password, progress_callback=None):
    """Legacy: Encrypt single file"""
    return create_archive([input_file], output_dir, input_file.stem, password=password,
                         split_size_mb=None, progress_callback=progress_callback)


def encrypt_and_split_file(input_file, output_dir, password, split_size_mb, progress_callback=None):
    """Legacy: Encrypt and split single file"""
    return create_archive([input_file], output_dir, input_file.stem, password=password,
                         split_size_mb=split_size_mb, progress_callback=progress_callback)


def archive_file_no_password(input_file, output_dir, progress_callback=None):
    """Legacy: Archive single file without password"""
    return create_archive([input_file], output_dir, input_file.stem, password=None,
                         split_size_mb=None, progress_callback=progress_callback)


def archive_and_split_file_no_password(input_file, output_dir, split_size_mb, progress_callback=None):
    """Legacy: Archive and split single file without password"""
    return create_archive([input_file], output_dir, input_file.stem, password=None,
                         split_size_mb=split_size_mb, progress_callback=progress_callback)
