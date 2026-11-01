import os
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import MessageService

load_dotenv()
client = TelegramClient("copier_session", int(os.environ["API_ID"]), os.environ["API_HASH"])
SRC = int(os.environ["SOURCE_CHAT_ID"])


async def main():
    ent = await client.get_entity(SRC)
    print("Proteksi konten (noforwards):", getattr(ent, "noforwards", False))
    items, cur, last_g = [], None, None
    async for m in client.iter_messages(SRC, reverse=True):
        if isinstance(m, MessageService):
            continue
        text = (m.text or "").strip()
        media = m.photo is not None or m.video is not None or m.document is not None
        g = m.grouped_id
        if g and cur is not None and last_g == g:
            cur["n"] += 1 if media else 0
            if text and not cur["text"]:
                cur["text"] = text
        else:
            cur = {"text": text, "n": 1 if media else 0, "media": media}
            items.append(cur)
        last_g = g
    blocks, b, pre = [], None, 0
    for it in items:
        if it["text"]:
            b = {"title": it["text"], "n": it["n"]}
            blocks.append(b)
        elif b is None:
            pre += it["n"]
        else:
            b["n"] += it["n"]
    ns = [x["n"] for x in blocks] or [0]
    print("Total item:", len(items), "| media sebelum judul pertama:", pre)
    print("Jumlah blok:", len(blocks),
          "| teks murni:", sum(1 for i in items if i["text"] and not i["media"]),
          "| caption di media:", sum(1 for i in items if i["text"] and i["media"]))
    print("Media per blok (min/maks/rata2):", min(ns), max(ns), round(sum(ns) / len(ns), 1))
    print("Judul >100 huruf:", sum(1 for x in blocks if len(x["title"]) > 100))
    print("3 judul pertama:")
    for x in blocks[:3]:
        print(" -", x["title"].replace("\n", " ⏎ ")[:40], f"({x['n']} media)")


with client:
    client.loop.run_until_complete(main())
