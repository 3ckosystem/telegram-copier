import os
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

api_id = int(os.environ["API_ID"])
api_hash = os.environ["API_HASH"]

SOURCE_ID = -1003841088535
LIMIT = 10

client = TelegramClient("copier_session", api_id, api_hash)


async def main():
    async for msg in client.iter_messages(SOURCE_ID, limit=LIMIT, reverse=True):
        print("=" * 80)
        print(f"id={msg.id} class={type(msg).__name__}")
        print(f"  raw .message = {msg.message!r}")
        print(f"  .text        = {msg.text!r}")
        print(f"  .action      = {getattr(msg, 'action', None)!r}")
        print(f"  has_media    = {msg.media is not None}")


with client:
    client.loop.run_until_complete(main())
