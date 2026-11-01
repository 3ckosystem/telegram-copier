import os
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import Chat, Channel

load_dotenv()

api_id = int(os.environ["API_ID"])
api_hash = os.environ["API_HASH"]

client = TelegramClient("copier_session", api_id, api_hash)


async def main():
    print(f"{'ID':<16} {'Tipe':<10} Nama")
    print("-" * 60)
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if isinstance(entity, Channel):
            tipe = "channel" if entity.broadcast else "supergroup"
        elif isinstance(entity, Chat):
            tipe = "group"
        else:
            continue  # lewati user/bot perorangan
        print(f"{dialog.id:<16} {tipe:<10} {dialog.name}")


with client:
    client.loop.run_until_complete(main())
