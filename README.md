# YouTube Batch Downloader (yt-dlp + Flask)

A local-first batch downloader with channel paging, mixed queue, format selection, and user-selectable parallel downloads. Bilingual UI (EN/中文), runs on `127.0.0.1:5000`.

## Highlights
- Channel browse: enter handle/channel/user/videos URL, fetch paged results, keep selections across pages.
- Batch queue: merge channel picks with pasted URLs; deduped queue.
- Formats: Video (Best/1080p/720p) or Audio (mp3/m4a/opus).
- Parallel downloads: choose worker count in UI; thread pool handles concurrent downloads (default 4, max 8).
- Local only: no uploads; files save to current folder; auto-uses bundled `ffmpeg/ffmpeg-8.0.1-essentials_build/bin`.

## Requirements
- Python 3.9+
- Dependencies: `pip install flask yt-dlp`
- FFmpeg already bundled at `ffmpeg/ffmpeg-8.0.1-essentials_build/bin` (no PATH edits needed)

## Quick start
```powershell
cd C:\Users\Admin\Desktop\youdown
python app.py
```
Open http://127.0.0.1:5000 (hard refresh with Ctrl+Shift+R if styles look stale).

## Usage
1) Toggle language at top (EN/中文).  
2) Channel mode: paste channel/handle/user/videos URL → Fetch → check items → page via Prev/Next (checks persist) → Add selected to queue.  
3) Manual add: paste one URL per line in “Paste URLs” → Add selected.  
4) Choose Mode (video/audio) and Format.  
5) Choose `Parallel jobs` (1–8).  
6) Click Start download; status pane shows per-item done/error.  
7) You can keep adding more videos while a job runs; when it finishes, click Start download again to process the new queue.

## Troubleshooting
- Styling not updated: hard refresh (Ctrl+Shift+R).
- Extraction errors: verify the link is public; `/videos` is auto-added for channel handles to list normal uploads.
- FFmpeg not found: ensure `ffmpeg/ffmpeg-8.0.1-essentials_build/bin` contains `ffmpeg.exe`.

## Layout
- `app.py`: Flask server + inline HTML/CSS/JS.
- `youtube_downloader.py`: CLI version.
- `ffmpeg/`: bundled ffmpeg binaries.

## Recent changes
- User-selectable parallel downloads (default 4, max 8).
- Cross-page selection retention; queue deduping; can keep adding items during/after runs.
- Updated dark glassy UI with bilingual copy.
