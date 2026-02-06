"""Video compression functions"""
import subprocess
from pathlib import Path
from config import FFMPEG_CRF

def get_file_size_gb(file_path: Path) -> float:
    """Get file size in GB"""
    return file_path.stat().st_size / (1024 ** 3)

def get_video_duration(input_file: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(input_file)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except:
        return 0

def compress_video(input_file: Path, output_dir: Path, keep_audio: bool = False, progress_callback=None, 
                   cpu_preset: str = "normal", cpu_threads: int = 0) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{input_file.stem}_compressed.mp4"
    
    print(f"\n    Input:  {input_file.name}")
    print(f"    Size:   {get_file_size_gb(input_file):.2f} GB")
    print(f"    Output: {output_file.name}")
    
    duration = get_video_duration(input_file)
    if duration <= 0:
        print("    [!] Could not detect video duration, progress may not show")
    
    # Get CPU thread count
    import multiprocessing
    max_threads = multiprocessing.cpu_count()
    threads = cpu_threads if cpu_threads > 0 else max_threads
    
    # Map friendly preset names to ffmpeg presets
    from config import get_ffmpeg_preset
    ffmpeg_preset = get_ffmpeg_preset(cpu_preset)
    
    # CPU encoding only
    print(f"    Encoder: CPU ({threads}/{max_threads} threads, {cpu_preset} preset)")
    cmd = [
        "ffmpeg", "-y", "-i", str(input_file),
        "-c:v", "libx264",
        "-preset", ffmpeg_preset,
        "-crf", str(FFMPEG_CRF),
        "-threads", str(threads),
    ]
    
    # Audio codec
    if keep_audio:
        cmd.extend(["-c:a", "copy"])
        print(f"    Audio:  Copying (no re-encode)")
    else:
        cmd.extend(["-c:a", "aac", "-b:a", "128k"])
        print(f"    Audio:  Re-encoding to AAC 128k")
    
    # Preserve metadata
    cmd.extend(["-map_metadata", "0"])
    
    # Progress and output
    cmd.extend(["-progress", "pipe:1", "-nostats", str(output_file)])
    
    print()
    
    process = subprocess.Popen(
        cmd, 
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE,
        universal_newlines=True,
        bufsize=1
    )
    
    last_percent = -1
    error_output = []
    
    import threading
    
    def read_stderr():
        for line in process.stderr:
            error_output.append(line)
    
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stderr_thread.start()
    
    for line in process.stdout:
        line = line.strip()
        if line.startswith("out_time_ms="):
            try:
                time_ms = int(line.split("=")[1])
                if duration > 0 and time_ms > 0:
                    percent = min((time_ms / 1000000 / duration) * 100, 100.0)
                    
                    if int(percent) != int(last_percent):
                        last_percent = percent
                        
                        if progress_callback:
                            try:
                                progress_callback(f"Compressing: {percent:.1f}% complete")
                            except:
                                pass
            except:
                pass
    
    process.wait()
    
    if progress_callback:
        try:
            progress_callback(f"Compressing: 100.0% complete")
        except:
            pass
    
    if process.returncode != 0:
        print(f"\n    [!] FFmpeg failed with return code {process.returncode}")
        if error_output:
            print(f"    [!] Error output:")
            for line in error_output[-10:]:
                print(f"        {line.strip()}")
        return None
    
    print(f"    Compressed: {get_file_size_gb(output_file):.2f} GB")
    return output_file
