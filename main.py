import threading
from health_check import start_health_check
import os
import requests
from yt_dlp import YoutubeDL
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

# Replace with your bot token
TOKEN = "7769792227:AAFbg7gi7sUpCNT8hQHeZSiqmVs41Xlxbd8"

# Ensure downloads folder exists
if not os.path.exists("downloads"):
    os.makedirs("downloads")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Send me a Twitter URL, and I'll fetch the video for you!")


async def download_and_send_video(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    msg = await update.message.reply_text("🔄 Downloading large video, please wait...")

    ydl_opts = {
        'outtmpl': 'downloads/%(title)s.%(ext)s',
        'format': 'bestvideo+bestaudio/best',
        'max_filesize': 2 * 1024 * 1024 * 1024,  # 2 GB max limit
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)
    except Exception as e:
        await msg.edit_text(f"❌ Download error: {e}")
        return

    await msg.edit_text("📤 Uploading the video...")

    try:
        # Open file in binary mode
        with open(file_path, "rb") as video_file:
            await update.message.reply_video(video=video_file, caption="✅ Here's your video!")
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"❌ Upload failed: {e}")
    finally:
        try:
            os.remove(file_path)
        except Exception:
            pass


async def handle_twitter_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    # Validate URL roughly
    if "twitter.com" not in url:
        await update.message.reply_text("❌ Please send a valid Twitter URL.")
        return

    try:
        # Extract the tweet path after twitter.com/
        tweet_path = url.split("twitter.com/")[-1].split("?")[0]

        # Call vxtwitter API
        api_url = f"https://api.vxtwitter.com/{tweet_path}"
        response = requests.get(api_url, timeout=10)
        data = response.json()

        if 'media' not in data or not data['media']:
            await update.message.reply_text("⚠️ No media found in this tweet.")
            return

        # Look for video media
        video_url = None
        for media in data['media']:
            if media.get('type') == 'video':
                video_url = media.get('url')
                break

        if not video_url:
            await update.message.reply_text("⚠️ Couldn't find a video URL in this tweet.")
            return

        # Check video size via HEAD request (some servers may block HEAD, handle errors)
        try:
            head = requests.head(video_url, allow_redirects=True, timeout=10)
            content_length = int(head.headers.get('Content-Length', 0))
        except Exception:
            content_length = 0  # Unknown size fallback

        # Telegram max file size for uploading is ~50MB
        telegram_limit = 50 * 1024 * 1024

        if content_length > telegram_limit:
            # Use yt-dlp to download and send
            await download_and_send_video(update, context, video_url)
        else:
            # Small enough, send directly by URL
            await update.message.reply_video(video=video_url, caption="✅ Here's your video!")

    except Exception as e:
        await update.message.reply_text(f"❌ An error occurred: {e}")


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❓ Unknown command. Send a Twitter video link to download.")


if __name__ == "__main__":
    application = ApplicationBuilder().token(TOKEN).build()

    # Command handlers
    application.add_handler(CommandHandler("start", start))

    # Twitter link handler (all text messages)
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_twitter_link))

    # Unknown commands handler
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    print("Bot started...")
    application.run_polling()


# 🔰 Run the Bot
if __name__ == "__main__":
    threading.Thread(target=start_health_check, daemon=True).start()
    asyncio.run(main())
