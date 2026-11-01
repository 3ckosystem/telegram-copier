import os
import sys
from plan_blocks import client, build_blocks, split_albums

TARGET = int(os.environ["TARGET_CHAT_ID"])


async def main():
    no = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    blocks = await build_blocks()
    b = blocks[no - 1]
    albums = split_albums(b["media"])
    print(f"Blok {no}: judul={'ya' if b['title'] else 'tidak'}, {len(b['media'])} media, album {[len(a) for a in albums]}")
    if input("Ketik YA untuk mengirim ke grup TUJUAN: ").strip() != "YA":
        print("Dibatalkan.")
        return
    if not albums and b["title"]:
        await client.send_message(TARGET, b["title"])
    for i, album in enumerate(albums):
        caps = [b["title"] if (i == 0 and b["title"]) else ""] + [""] * (len(album) - 1)
        await client.send_file(TARGET, [m.media for m in album], caption=caps)
    print("Selesai.")


with client:
    client.loop.run_until_complete(main())
