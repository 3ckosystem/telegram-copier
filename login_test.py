import os
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

api_id = int(os.environ["API_ID"])
api_hash = os.environ["API_HASH"]

os.makedirs(os.environ.get("SESSION_DIR", "."), exist_ok=True)
client = TelegramClient(os.path.join(os.environ.get("SESSION_DIR", "."), "copier_session"), api_id, api_hash)


async def main():
    me = await client.get_me()
    print(f"Login berhasil sebagai: {me.first_name} (@{me.username}) id={me.id}")


with client:
    client.loop.run_until_complete(main())
