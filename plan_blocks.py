import os
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import MessageService

load_dotenv()
client = TelegramClient("copier_session", int(os.environ["API_ID"]), os.environ["API_HASH"])
SRC = int(os.environ["SOURCE_CHAT_ID"])
MAX_ALBUM = 10


def is_visual(m):
    return m.photo is not None or m.video is not None


def is_media(m):
    return is_visual(m) or m.document is not None


async def build_blocks():
    items, cur, last_g = [], None, None
    async for m in client.iter_messages(SRC, reverse=True):
        if isinstance(m, MessageService):
            continue
        text = (m.text or "").strip()
        media = is_media(m)
        g = m.grouped_id
        if g and cur is not None and last_g == g:
            if media:
                cur["media"].append(m)
            if text and not cur["text"]:
                cur["text"] = text
        else:
            cur = {"text": text, "media": [m] if media else []}
            items.append(cur)
        last_g = g
    blocks, b = [], None
    for it in items:
        if it["text"]:
            b = {"title": it["text"], "media": list(it["media"])}
            blocks.append(b)
        elif b is None:
            if it["media"]:
                b = {"title": None, "media": list(it["media"])}
                blocks.append(b)
        else:
            b["media"].extend(it["media"])
    return blocks


def split_albums(media):
    visual = [m for m in media if is_visual(m)]
    docs = [m for m in media if not is_visual(m)]
    out = []
    for grp in (visual, docs):
        for i in range(0, len(grp), MAX_ALBUM):
            out.append(grp[i:i + MAX_ALBUM])
    return out


async def main():
    blocks = await build_blocks()
    plans = [split_albums(b["media"]) for b in blocks]
    text_only = sum(1 for b in blocks if not b["media"])
    with_docs = sum(1 for b in blocks if any(not is_visual(m) for m in b["media"]))
    print("Total blok:", len(blocks), "| blok teks saja:", text_only, "| blok berisi dokumen:", with_docs)
    print("Total album setelah dipecah:", sum(len(p) for p in plans))
    print("Total media:", sum(len(b["media"]) for b in blocks))
    print("Contoh 4 blok pertama:")
    for i, (b, p) in enumerate(zip(blocks[:4], plans[:4]), 1):
        judul = "[tanpa judul]" if b["title"] is None else "ada judul"
        print(f"  Blok {i}: {judul}, {len(b['media'])} media -> album {[len(a) for a in p]}")
    big = max(range(len(blocks)), key=lambda i: len(blocks[i]["media"]))
    print(f"Blok terbesar: nomor {big + 1}, {len(blocks[big]['media'])} media -> album {[len(a) for a in plans[big]]}")


if __name__ == "__main__":
    with client:
        client.loop.run_until_complete(main())
if __name__ == "__main__":
    with client:
        client.loop.run_until_complete(main())
