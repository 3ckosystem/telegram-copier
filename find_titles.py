import os
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import MessageService

load_dotenv()

api_id = int(os.environ["API_ID"])
api_hash = os.environ["API_HASH"]

SOURCE_ID = -1003841088535

# Naikkan ini kalau judul pertama belum ketemu.
SCAN_LIMIT = 500

client = TelegramClient("copier_session", api_id, api_hash)


async def main():
    print(f"Memindai {SCAN_LIMIT} pesan pertama untuk mencari pesan BERTEKS (calon judul)...\n")
    found = 0
    async for msg in client.iter_messages(SOURCE_ID, limit=SCAN_LIMIT, reverse=True):
        if isinstance(msg, MessageService):
            continue
        text = (msg.text or "").strip()
        if text:
            found += 1
            tipe_media = "ADA_MEDIA" if msg.media else "TEKS_MURNI"
            cuplikan = text.replace("\n", " ⏎ ")[:80]
            print(f"id={msg.id:<8} grouped={str(msg.grouped_id or '-'):<20} [{tipe_media}] {cuplikan}")
            if found >= 30:
                print("\n(berhenti setelah 30 ditemukan, masih ada kemungkinan lebih banyak)")
                break
    if found == 0:
        print("Tidak ada satupun pesan berteks ditemukan dalam rentang ini.")


with client:
    client.loop.run_until_complete(main())
