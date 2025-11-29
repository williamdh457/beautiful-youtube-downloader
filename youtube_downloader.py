#!/usr/bin/env python3
"""
Simple YouTube downloader: video/audio selection, channel browser with paging,
multi-language prompts (en/zh/es). Tested with yt-dlp.
"""
import sys
from pathlib import Path
from typing import Dict, List

try:
    from yt_dlp import YoutubeDL
except ImportError:
    print("Please install yt-dlp first: pip install yt-dlp")
    sys.exit(1)

FFMPEG_BIN = Path(__file__).parent / "ffmpeg" / "ffmpeg-8.0.1-essentials_build" / "bin"

LANG = {
    "en": {
        "choose_lang": "Choose language: 1) English 2) 简体中文 3) Español : ",
        "menu": "\n1) Download by URL\n2) Browse channel (list top 10)\n3) Quit\nSelect: ",
        "enter_url": "Enter a YouTube video URL: ",
        "enter_channel": "Enter a YouTube channel URL (channel/handle/user): ",
        "choose_av": "Choose: 1) Video 2) Audio : ",
        "video_fmt": "Video format: 1) best 2) 1080p 3) 720p : ",
        "audio_fmt": "Audio format: 1) mp3 2) m4a 3) opus : ",
        "list_title": "\nTop {count} videos:",
        "more": "m) More  q) Back  number) Download : ",
        "downloading": "Downloading...",
        "done": "Done.",
        "invalid": "Invalid choice.",
        "error": "Error: {msg}",
    },
    "zh": {
        "choose_lang": "选择语言: 1) English 2) 简体中文 3) Español : ",
        "menu": "\n1) 按URL下载\n2) 浏览频道(列出前10条)\n3) 退出\n请选择: ",
        "enter_url": "输入YouTube视频链接: ",
        "enter_channel": "输入频道链接(可用channel/handle/user): ",
        "choose_av": "选择: 1) 视频 2) 音频 : ",
        "video_fmt": "视频格式: 1) 最佳 2) 1080p 3) 720p : ",
        "audio_fmt": "音频格式: 1) mp3 2) m4a 3) opus : ",
        "list_title": "\n前{count}条视频:",
        "more": "m) 更多  q) 返回  编号) 下载 : ",
        "downloading": "下载中...",
        "done": "完成。",
        "invalid": "无效选择。",
        "error": "错误: {msg}",
    },
    "es": {
        "choose_lang": "Elige idioma: 1) English 2) 简体中文 3) Español : ",
        "menu": "\n1) Descargar por URL\n2) Ver canal (top 10)\n3) Salir\nSelecciona: ",
        "enter_url": "Pega el enlace de YouTube: ",
        "enter_channel": "URL del canal (channel/handle/user): ",
        "choose_av": "Elige: 1) Video 2) Audio : ",
        "video_fmt": "Formato de video: 1) mejor 2) 1080p 3) 720p : ",
        "audio_fmt": "Formato de audio: 1) mp3 2) m4a 3) opus : ",
        "list_title": "\nTop {count} videos:",
        "more": "m) Más  q) Volver  número) Descargar : ",
        "downloading": "Descargando...",
        "done": "Listo.",
        "invalid": "Opción inválida.",
        "error": "Error: {msg}",
    },
}


def pick_lang() -> str:
    choice = input(LANG["en"]["choose_lang"]).strip()
    return {"1": "en", "2": "zh", "3": "es"}.get(choice, "en")


def normalize_channel_url(url: str) -> str:
    """Ensure channel URL points to the videos tab so entries are real videos."""
    url = url.strip()
    if not url:
        return url
    if url.startswith("@"):
        url = f"https://www.youtube.com/{url}"
    if url.startswith("youtube.com/"):
        url = "https://" + url
    lower = url.lower().rstrip("/")
    # If it already points to a list, keep as-is
    if any(tag in lower for tag in ["/videos", "/streams", "/shorts", "/playlist", "/watch", "list="]):
        return url
    if any(key in lower for key in ["/channel/", "/user/", "/c/", "/@"]):
        return url.rstrip("/") + "/videos"
    return url


def build_opts(is_video: bool, fmt_choice: str, text: Dict[str, str]):
    if is_video:
        fmt_map = {
            "1": "bestvideo+bestaudio/best",
            "2": "bestvideo[height<=1080]+bestaudio/best",
            "3": "bestvideo[height<=720]+bestaudio/best",
        }
        opts = {
            "format": fmt_map.get(fmt_choice, fmt_map["1"]),
            "merge_output_format": "mp4",
            "outtmpl": "%(title)s.%(ext)s",
        }
        if FFMPEG_BIN.exists():
            opts["ffmpeg_location"] = str(FFMPEG_BIN)
        return opts
    codec = {"1": "mp3", "2": "m4a", "3": "opus"}.get(fmt_choice, "mp3")
    opts = {
        "format": "bestaudio/best",
        "outtmpl": "%(title)s.%(ext)s",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": codec,
                "preferredquality": "192",
            }
        ],
    }
    if FFMPEG_BIN.exists():
        opts["ffmpeg_location"] = str(FFMPEG_BIN)
    return opts


def download(url: str, is_video: bool, fmt_choice: str, text: Dict[str, str]):
    print(text["downloading"])
    opts = build_opts(is_video, fmt_choice, text)
    try:
        with YoutubeDL(opts) as ydl:
            ydl.download([url])
        print(text["done"])
    except Exception as exc:
        print(text["error"].format(msg=exc))


def fetch_channel_entries(channel_url: str, start: int, count: int) -> List[dict]:
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
        "skip_download": True,
        "playliststart": start + 1,
        "playlistend": start + count,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)
    return info.get("entries", [])


def browse_channel(text: Dict[str, str]):
    url = normalize_channel_url(input(text["enter_channel"]).strip())
    idx = 0
    page = 10
    entries: List[dict] = []
    while True:
        entries = fetch_channel_entries(url, idx, page)
        if not entries:
            # Try forcing /videos once more if user pasted a homepage URL
            if "/videos" not in url:
                url = normalize_channel_url(url + "/videos")
                entries = fetch_channel_entries(url, idx, page)
            if not entries:
                print(text["error"].format(msg="No entries found."))
                return
            return
        print(text["list_title"].format(count=len(entries)))
        for i, e in enumerate(entries, start=1):
            title = e.get("title", "N/A")
            print(f"{i}) {title}")
        choice = input(text["more"]).strip().lower()
        if choice == "q":
            return
        if choice == "m":
            idx += page
            continue
        if choice.isdigit():
            num = int(choice)
            if 1 <= num <= len(entries):
                video_url = entries[num - 1].get("url") or entries[num - 1].get(
                    "webpage_url"
                )
                av = input(text["choose_av"]).strip()
                is_video = av == "1"
                fmt_choice = input(
                    text["video_fmt"] if is_video else text["audio_fmt"]
                ).strip()
                download(video_url, is_video, fmt_choice, text)
            else:
                print(text["invalid"])
        else:
            print(text["invalid"])


def main():
    lang = pick_lang()
    text = LANG[lang]
    while True:
        choice = input(text["menu"]).strip()
        if choice == "1":
            url = input(text["enter_url"]).strip()
            av = input(text["choose_av"]).strip()
            is_video = av == "1"
            fmt_choice = input(
                text["video_fmt"] if is_video else text["audio_fmt"]
            ).strip()
            download(url, is_video, fmt_choice, text)
        elif choice == "2":
            browse_channel(text)
        elif choice == "3":
            break
        else:
            print(text["invalid"])


if __name__ == "__main__":
    main()
