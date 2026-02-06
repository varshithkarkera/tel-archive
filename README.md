# Tel Archive

```
╔══════════════════════════════════════════════════════════════════════════════════════════════════╗
║                                                                                                  ║
 ║        ████████╗███████╗██╗          █████╗ ██████╗  ██████╗██╗  ██╗██╗██╗   ██╗███████╗       ║
 ║        ╚══██╔══╝██╔════╝██║         ██╔══██╗██╔══██╗██╔════╝██║  ██║██║██║   ██║██╔════╝       ║
 ║           ██║   █████╗  ██║         ███████║██████╔╝██║     ███████║██║██║   ██║█████╗         ║
 ║           ██║   ██╔══╝  ██║         ██╔══██║██╔══██╗██║     ██╔══██║██║╚██╗ ██╔╝██╔══╝         ║
 ║           ██║   ███████╗███████╗    ██║  ██║██║  ██║╚██████╗██║  ██║██║ ╚████╔╝ ███████╗       ║
 ║           ╚═╝   ╚══════╝╚══════╝    ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═══╝  ╚══════╝       ║
║                                                                                                  ║
╚══════════════════════════════════════════════════════════════════════════════════════════════════╝
```

**Compress. Archive. Encrypt. Upload to Telegram. Unlimited Storage.**

Archive everything to Telegram. Compress, encrypt, and store unlimited files using Telegram as your personal cloud storage.

---

## Features

- Multi-threaded CPU video compression with configurable speed/quality presets
- AES-256 encryption via 7-Zip
- Auto-split large files for Telegram limits (2GB free / 4GB premium)
- Parallel upload/download with up to 20 simultaneous connections
- Multi-channel support - upload to any channel you own
- Archive browser - view and manage uploaded files
- Download and decrypt files from Telegram
- BIP39 mnemonic password generation

---

## Requirements

- Python 3.8+
- FFmpeg (for video compression)
- 7-Zip (for encryption)
- Telegram API credentials (get from https://my.telegram.org/auth)

---

## Installation

### 1. Clone the repository
```bash
git clone https://github.com/varshithkarkera/tel-archive.git
cd tel-archive
```

### 2. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 3. Install system dependencies

**Windows:**
- Download and install [FFmpeg](https://ffmpeg.org/download.html)
- Download and install [7-Zip](https://www.7-zip.org/)
- Add both to your PATH

### 4. Get Telegram API credentials

1. Go to https://my.telegram.org/auth
2. Log in with your phone number
3. Click "API development tools"
4. Create a new application
5. Copy your `api_id` and `api_hash`

---

## Usage

### First Run

1. **Start the server**
```bash
python app.py
```

2. **Open your browser** to `http://localhost:5001`

3. **Complete the onboarding wizard**:
   - Enter Telegram API credentials
   - Log in to Telegram (phone + verification code)
   - Set encryption password (or generate a secure 12-word passphrase)
   - Choose default upload destination

### Process Files

1. Place files in the `archive/` folder
2. Go to **Process Files** tab
3. Select files to process
4. Choose options:
   - **Compress videos**: CPU video compression with configurable presets
   - **Bundle files**: Combine into single archive
   - **Encrypt**: AES-256 encryption (recommended)
   - **Upload to Telegram**: Send to your channel
5. Select upload destination (or use default)
6. Click **Process Files**

### View Archives

1. Go to **Archives** tab
2. Click **Fetch from Telegram** to load uploaded files
3. Click **Show Parts** to see individual files in an archive
4. Click **Download All** to download and optionally decrypt
5. Options:
   - **Decrypt by default**: Auto-decrypt after download
   - **Delete .7z after decrypt**: Clean up encrypted files

### Downloaded Files

1. Go to **Downloaded** tab
2. View all files downloaded from Telegram
3. Files are stored in `archive/Downloaded/{archive_name}/`

---

## Settings

### Video Compression

**CPU Encoding:**
- Uses all available CPU cores (configurable)
- Multi-threaded for maximum performance

**CPU Presets:**
- Fastest: 77% compression (ultrafast preset)
- Fast: 87% compression (superfast preset)
- Normal: 91% compression (veryfast preset, default)

All presets use CRF 28 quality for consistent output.

**Audio:**
- Fast (copy): Copies audio without re-encoding (faster, larger file)
- Full (re-encode): Re-encodes audio to AAC 128k (slower, smaller file)

**CPU Threads:**
- 0 = Use all available threads (default)
- Set lower to limit CPU usage and leave resources for other tasks

### Upload/Download

**Split Size:**
- Free: 2000 MB (2GB)
- Premium: 4000 MB (4GB)

**Parallel Connections:**
- Default: 20 connections
- Higher = faster but more resource intensive
- May be limited by Telegram

**Caption Mode:**
- Detailed: Full metadata
- Minimal: Filename only
- None: No caption

### Encryption

**Password:**
- AES-256 encryption
- Generate 12-word BIP39 mnemonic
- Old passwords saved to archive/old_passwords.txt

**Auto-split Archives:**
- Automatically split files larger than split size
- WARNING: Disabling will cause uploads to fail for oversized files

---

## Dependencies

- Flask - Web framework
- Telethon - Telegram client
- cryptg - Fast encryption for Telethon
- mnemonic - BIP39 passphrase generation
- FFmpeg - Video processing
- 7-Zip - Archive encryption

---

## Credits

Made by [Varshith Karkera](https://varshithkarkera.in)

Parallel upload implementation inspired by [Kotatogram](https://github.com/kotatogram/kotatogram-desktop)

---

## Links

- GitHub: https://github.com/varshithkarkera/telarchive
- Website: https://varshithkarkera.in
- Issues: https://github.com/varshithkarkera/telarchive/issues
- X: https://x.com/varshithkarkera

---

## Star History

<p align="center">
  <img src="https://api.star-history.com/svg?repos=varshithkarkera/telarchive&type=Date&theme=dark&v=1" />
</p>

---

## License

MIT License - See LICENSE file for details

---

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

---

## Disclaimer

This tool is for personal use only. Respect Telegram's Terms of Service and local laws regarding data storage and encryption. The developers are not responsible for any misuse of this software.

---

**Enjoy unlimited cloud storage with Tel Archive!**
