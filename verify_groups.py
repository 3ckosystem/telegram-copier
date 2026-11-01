import os
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

api_id = int(os.environ["API_ID"])
api_hash = os.environ["API_HASH"]

SOURCE_ID = -1003841088535
DEST_ID = -5133494846

client = TelegramClient("copier_session", api_id, api_hash)


async def main():
    for label, chat_id in [("SUMBER", SOURCE_ID), ("TUJUAN", DEST_ID)]:
        try:
            entity = await client.get_entity(chat_id)
            name = getattr(entity, "title", getattr(entity, "first_name", "?"))
            print(f"[{label}] OK -> {name} (id={chat_id})")
        except Exception as e:
            print(f"[{label}] GAGAL -> {chat_id}: {e}")


with client:
    client.loop.run_until_complete(main())
