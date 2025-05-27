import html
import json
import logging
import traceback
from io import StringIO
from tempfile import TemporaryFile
from typing import Optional
from urllib.parse import urlsplit
from health_check import start_health_check
import threading
import requests
import telegram.error
from telegram import Update, InputMediaDocument, constants
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

from config import BOT_TOKEN, DEVELOPER_ID

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    import re2 as re
except ImportError:
    import re


class APIException(Exception):
    pass


def extract_tweet_ids(update: Update) -> Optional[list[str]]:
    text = update.effective_message.text
    unshortened_links = ''
    for link in re.findall(r"t.co/[a-zA-Z0-9]+", text):
        try:
            unshortened_link = requests.get('https://' + link).url
            unshortened_links += '\n' + unshortened_link
        except:
            pass
    tweet_ids = re.findall(r"(?:twitter|x).com/.{1,15}/(?:web|status(?:es)?)/([0-9]{1,20})", text + unshortened_links)
    return list(dict.fromkeys(tweet_ids)) or None


def scrape_media(tweet_id: int) -> list[dict]:
    r = requests.get(f'https://api.vxtwitter.com/Twitter/status/{tweet_id}')
    r.raise_for_status()
    try:
        return r.json()['media_extended']
    except requests.exceptions.JSONDecodeError:
        if match := re.search(r'<meta content="(.*?)" property="og:description" />', r.text):
            raise APIException(f'API error: {html.unescape(match.group(1))}')
        raise


def reply_media(update: Update, context: CallbackContext, tweet_media: list) -> bool:
    photos = [m for m in tweet_media if m["type"] == "image"]
    gifs = [m for m in tweet_media if m["type"] == "gif"]
    videos = [m for m in tweet_media if m["type"] == "video"]

    if photos:
        reply_photos(update, context, photos)
    if gifs:
        reply_gifs(update, context, gifs)
    elif videos:
        reply_videos(update, context, videos)

    return bool(photos or gifs or videos)


def reply_photos(update: Update, context: CallbackContext, photos: list[dict]) -> None:
    media_group = []
    for photo in photos:
        url = photo['url']
        try:
            new_url = urlsplit(url)._replace(query='format=jpg&name=orig').geturl()
            requests.head(new_url).raise_for_status()
            media_group.append(InputMediaDocument(media=new_url))
        except requests.HTTPError:
            media_group.append(InputMediaDocument(media=url))
    update.effective_message.reply_media_group(media_group, quote=True)
    context.bot_data['stats']['media_downloaded'] += len(media_group)


def reply_gifs(update: Update, context: CallbackContext, gifs: list[dict]) -> None:
    for gif in gifs:
        update.effective_message.reply_animation(animation=gif['url'], quote=True)
    context.bot_data['stats']['media_downloaded'] += 1


def reply_videos(update: Update, context: CallbackContext, videos: list[dict]) -> None:
    for video in videos:
        url = video['url']
        try:
            r = requests.get(url, stream=True)
            r.raise_for_status()
            size = int(r.headers['Content-Length'])
            if size <= constants.MAX_FILESIZE_DOWNLOAD:
                update.effective_message.reply_video(video=url, quote=True)
            elif size <= constants.MAX_FILESIZE_UPLOAD:
                msg = update.effective_message.reply_text('Uploading large video...')
                with TemporaryFile() as tf:
                    for chunk in r.iter_content(128):
                        tf.write(chunk)
                    tf.seek(0)
                    update.effective_message.reply_video(video=tf, quote=True, supports_streaming=True)
                msg.delete()
            else:
                update.effective_message.reply_text(f'Too large. Direct link:\n{url}', quote=True)
        except Exception:
            update.effective_message.reply_text(f'Error sending video. Direct link:\n{url}', quote=True)
        context.bot_data['stats']['media_downloaded'] += 1


def error_handler(update: object, context: CallbackContext) -> None:
    if isinstance(context.error, telegram.error.Unauthorized):
        return
    if isinstance(context.error, telegram.error.Conflict):
        return
    logger.error("Exception in update:", exc_info=context.error)
    if update is None:
        return
    tb = ''.join(traceback.format_exception(None, context.error, context.error.__traceback__))
    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    message = (
        f'#error_report\n<pre>update = {html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))}</pre>\n'
        f'<pre>chat_data = {html.escape(str(context.chat_data))}</pre>\n'
        f'<pre>user_data = {html.escape(str(context.user_data))}</pre>\n'
        f'<pre>{html.escape(tb)}</pre>'
    )
    string_out = StringIO(message)
    context.bot.send_document(chat_id=DEVELOPER_ID, document=string_out, filename='error_report.txt', caption='Exception in runtime')
    if update:
        error_name = f"{context.error.__class__.__module__}.{context.error.__class__.__qualname__}"
        update.effective_message.reply_text(f'Error\n{error_name}: {str(context.error)}')


def start(update: Update, context: CallbackContext) -> None:
    update.effective_message.reply_markdown_v2(
        fr'Hi {update.effective_user.mention_markdown_v2()}!\nSend tweet link to download media.'
    )


def help_command(update: Update, context: CallbackContext) -> None:
    update.effective_message.reply_text('Send a tweet link to download its media in best quality.')


def stats_command(update: Update, context: CallbackContext) -> None:
    stats = context.bot_data.get('stats', {'messages_handled': 0, 'media_downloaded': 0})
    update.effective_message.reply_markdown_v2(
        f'Stats:\nMessages: {stats["messages_handled"]}\nMedia: {stats["media_downloaded"]}'
    )


def reset_stats_command(update: Update, context: CallbackContext) -> None:
    context.bot_data['stats'] = {'messages_handled': 0, 'media_downloaded': 0}
    update.effective_message.reply_text("Stats reset.")


def handle_message(update: Update, context: CallbackContext) -> None:
    context.bot_data.setdefault('stats', {'messages_handled': 0, 'media_downloaded': 0})
    context.bot_data['stats']['messages_handled'] += 1
    tweet_ids = extract_tweet_ids(update)
    if not tweet_ids:
        update.effective_message.reply_text('No tweet ID found.')
        return
    for tweet_id in tweet_ids:
        try:
            media = scrape_media(int(tweet_id))
            if not reply_media(update, context, media):
                update.effective_message.reply_text('No supported media found.')
        except Exception as e:
            update.effective_message.reply_text(f'Error processing tweet {tweet_id}: {str(e)}')


def main():
    updater = Updater(BOT_TOKEN)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("help", help_command))
    dispatcher.add_handler(CommandHandler("stats", stats_command))
    dispatcher.add_handler(CommandHandler("resetstats", reset_stats_command))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    dispatcher.add_error_handler(error_handler)

    updater.start_polling()
    updater.idle()



# 🔰 Run the Bot
if __name__ == "__main__":
    threading.Thread(target=start_health_check, daemon=True).start()
    main()
