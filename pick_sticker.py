from organizer import client


async def main():
    async for m in client.iter_messages("me", limit=50):
        if m.sticker:
            with open(".env") as f:
                lines = [l for l in f.read().splitlines() if not l.startswith("STICKER_MSG_ID=")]
            lines.append(f"STICKER_MSG_ID={m.id}")
            with open(".env", "w") as f:
                f.write("\n".join(lines) + "\n")
            print(f"Stiker ditemukan (pesan #{m.id}) dan disimpan ke .env")
            return
    print("Tidak ada stiker di Pesan Tersimpan (50 pesan terakhir).")


with client:
    client.loop.run_until_complete(main())
