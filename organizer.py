import os
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import MessageService, MessageMediaPhoto, MessageMediaDocument

load_dotenv()
SESSION_DIR = os.environ.get("SESSION_DIR", ".")
os.makedirs(SESSION_DIR, exist_ok=True)
client = TelegramClient(os.path.join(SESSION_DIR, "copier_session"), int(os.environ["API_ID"]), os.environ["API_HASH"])
client.flood_sleep_threshold = 600
import richtext
from settings import get_ids

SRC, TARGET = get_ids()
MAX_ALBUM = 10
GAP_SECONDS = int(os.environ.get("GAP_SECONDS", "3600"))


def is_visual(m):
    if m.photo is not None or m.video is not None:
        return True
    if isinstance(m.media, MessageMediaDocument) and m.file and m.file.mime_type:
        return m.file.mime_type.startswith(("image/", "video/"))
    return False


def needs_convert(m):
    return (isinstance(m.media, MessageMediaDocument) and m.video is None
            and is_visual(m))


def is_media(m):
    if m.sticker is not None:
        return False
    if not isinstance(m.media, (MessageMediaPhoto, MessageMediaDocument)):
        return False
    return is_visual(m) or m.document is not None


async def build_blocks():
    items, cur, last_g = [], None, None
    async for m in client.iter_messages(get_ids()[0], reverse=True):
        if isinstance(m, MessageService):
            continue
        text = richtext.msg_markdown(m).strip()
        media = is_media(m)
        g = m.grouped_id
        if g and cur is not None and last_g == g:
            if media:
                cur["media"].append(m)
            if text and not cur["text"]:
                cur["text"] = text
        else:
            cur = {"id": m.id, "text": text, "media": [m] if media else [], "date": m.date}
            items.append(cur)
        last_g = g
    blocks, b = [], None
    for k, it in enumerate(items):
        prev_media = bool(items[k - 1]["media"]) if k > 0 else False
        if it["text"]:
            b = {"key": it["id"], "title": it["text"], "media": list(it["media"]),
                 "after_media": prev_media}
            blocks.append(b)
        elif b is None:
            if it["media"]:
                b = {"key": it["id"], "title": None, "media": list(it["media"]),
                     "after_media": False}
                blocks.append(b)
        elif (it["media"] and k > 0 and items[k - 1]["media"]
              and (it["date"] - items[k - 1]["date"]).total_seconds() > GAP_SECONDS):
            b = {"key": it["id"], "title": None, "media": list(it["media"]),
                 "after_media": False}
            blocks.append(b)
        else:
            b["media"].extend(it["media"])
    return blocks


def album_sizes(n):
    k = -(-n // MAX_ALBUM)
    if k <= 1:
        return [n]
    rest = n - MAX_ALBUM * (k - 2)
    a = -(-rest // 2)
    return [MAX_ALBUM] * (k - 2) + [a, rest - a]


def split_albums(media):
    out, run, cat = [], [], None

    def flush():
        pos = 0
        for size in album_sizes(len(run)):
            out.append(run[pos:pos + size])
            pos += size

    for m in media:
        c = is_visual(m)
        if run and c != cat:
            flush()
            run = []
        run.append(m)
        cat = c
    if run:
        flush()
    return out
