import os
import re
from dotenv import load_dotenv
from telethon import TelegramClient, errors
try:
    from telethon.tl.functions.messages import GetForumTopicsRequest
    _TOPIC_PARAM = 'peer'
except ImportError:
    GetForumTopicsRequest = None
    _TOPIC_PARAM = None
from telethon.tl.types import MessageService, MessageMediaPhoto, MessageMediaDocument

load_dotenv()
SESSION_DIR = os.environ.get("SESSION_DIR", ".")
os.makedirs(SESSION_DIR, exist_ok=True)
client = TelegramClient(os.path.join(SESSION_DIR, "copier_session"), int(os.environ["API_ID"]), os.environ["API_HASH"])
client.flood_sleep_threshold = int(os.environ.get("FLOOD_SLEEP_MAX", "3600"))
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


async def _build_blocks_raw(progress=None):
    items, cur, last_g = [], None, None
    total = 0
    if progress:
        try:
            total = (await client.get_messages(get_ids()[0], limit=0)).total
        except Exception:
            total = 0
    scanned = 0
    async for m in client.iter_messages(get_ids()[0], reverse=True):
        scanned += 1
        if progress and scanned % 150 == 0:
            await progress(kind="scan", scanned=scanned, total=total)
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


ARCHIVE_EXT = (".zip", ".rar", ".7z", ".tar", ".tgz", ".tar.gz", ".tbz2", ".tar.bz2", ".txz", ".tar.xz",
               ".gz", ".bz2", ".xz")
ARCHIVE_MIME = {"application/zip", "application/x-zip-compressed", "application/x-rar-compressed",
                "application/vnd.rar", "application/x-7z-compressed", "application/x-tar",
                "application/gzip", "application/x-gzip", "application/x-bzip2", "application/x-xz"}
MULTIPART = re.compile(r"(\.part\d+\.rar|\.7z\.\d{3}|\.zip\.\d{3}|\.z\d{2}|\.r\d{2})$")


def is_archive(m):
    f = m.file
    if not f or not isinstance(m.media, MessageMediaDocument):
        return False
    mime = (f.mime_type or "").lower()
    name = (f.name or "").lower()
    if MULTIPART.search(name):
        return False
    return mime in ARCHIVE_MIME or name.endswith(ARCHIVE_EXT)


def split_albums(media):
    out, run, cat = [], [], None

    def flush():
        pos = 0
        for size in album_sizes(len(run)):
            out.append(run[pos:pos + size])
            pos += size

    for m in media:
        if is_archive(m):
            if run:
                flush()
                run = []
            out.append([m])
            cat = None
            continue
        c = is_visual(m)
        if run and c != cat:
            flush()
            run = []
        run.append(m)
        cat = c
    if run:
        flush()
    return out


async def list_topics(client_, src):
    ent = await client_.get_input_entity(src)
    topics, offset_date, offset_id, offset_topic = {}, 0, 0, 0
    try:
        while True:
            res = await client_(GetForumTopicsRequest(**{_TOPIC_PARAM: ent}, offset_date=offset_date,
                                                    offset_id=offset_id, offset_topic=offset_topic, limit=100))
            batch = [t for t in res.topics if getattr(t, "title", None)]
            if not batch:
                break
            for t in batch:
                topics[t.id] = t
            if len(batch) < 100:
                break
            last = batch[-1]
            offset_date, offset_id, offset_topic = last.date, last.top_message, last.id
    except errors.FloodWaitError:
        raise
    except Exception as e:
        raise ValueError(f"Gagal membaca daftar topik ({type(e).__name__}). Sumber mungkin bukan grup dengan topik.")
    return sorted(topics.values(), key=lambda t: t.id)


def _safe_title(s):
    s = (s or "").replace("`", "'").replace("](", "]\u200b(")
    for pair in ("**", "__", "~~", "||"):
        s = s.replace(pair, pair[0] + "\u200b" + pair[1])
    return s.strip()


def _group_messages(msgs, topic):
    items, cur, last_g = [], None, None
    for m in msgs:
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
        prev = items[k - 1] if k > 0 else None
        if it["text"]:
            b = {"key": it["id"], "title": it["text"], "media": list(it["media"]),
                 "after_media": bool(prev and prev["media"])}
            blocks.append(b)
        elif b is None:
            if it["media"]:
                b = {"key": it["id"], "title": None, "media": list(it["media"]), "after_media": False}
                blocks.append(b)
        elif (it["media"] and prev and prev["media"]
              and (it["date"] - prev["date"]).total_seconds() > GAP_SECONDS):
            b = {"key": it["id"], "title": None, "media": list(it["media"]), "after_media": False}
            blocks.append(b)
        else:
            b["media"].extend(it["media"])
    for blk in blocks:
        if blk["media"]:
            blk["title"] = f"{topic} - {blk['title']}" if blk["title"] else topic
    return blocks


async def build_blocks_topics(progress=None):
    src = get_ids()[0]
    topics = await list_topics(client, src)
    blocks = []
    for i, t in enumerate(topics, 1):
        msgs = [m async for m in client.iter_messages(src, reply_to=t.id, reverse=True)]
        blocks.extend(merge_trailing(_group_messages(msgs, _safe_title(t.title))))
        if progress:
            await progress(kind="scan", scanned=i, total=len(topics))
    return blocks


def merge_trailing(blocks):
    out = []
    for blk in blocks:
        if not blk["media"] and blk["title"] and out and out[-1]["media"]:
            out[-1].setdefault("extra", []).append(blk["title"])
            continue
        out.append(blk)
    return out


async def build_blocks(progress=None):
    return merge_trailing(await _build_blocks_raw(progress))
