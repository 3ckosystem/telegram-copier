import os
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

api_id = int(os.environ["API_ID"])
api_hash = os.environ["API_HASH"]

SOURCE_ID = -1003841088535

# Ubah angka ini untuk melihat lebih banyak/lebih sedikit pesan.
# Diambil dari pesan TERLAMA ke TERBARU (reverse=True) biar urutannya sesuai riwayat asli.
LIMIT = 50

client = TelegramClient("copier_session", api_id, api_hash)


def describe(msg):
    if msg.photo:
        tipe = "PHOTO"
    elif msg.video:
        tipe = "VIDEO"
    elif msg.document:
        tipe = "DOCUMENT"
    elif msg.text:
        tipe = "TEXT"
    else:
        tipe = "OTHER"
    caption = (msg.text or "").replace("\n", " ⏎ ")[:60]
    return tipe, caption


async def main():
    print(f"Mengambil {LIMIT} pesan tertua dari grup sumber...\n")
    print(f"{'MsgID':<10} {'GroupedID':<20} {'Tipe':<10} Caption/Teks")
    print("-" * 90)
    async for msg in client.iter_messages(SOURCE_ID, limit=LIMIT, reverse=True):
        tipe, caption = describe(msg)
        grouped = msg.grouped_id or ""
        print(f"{msg.id:<10} {str(grouped):<20} {tipe:<10} {caption}")


with client:
    client.loop.run_until_complete(main())
