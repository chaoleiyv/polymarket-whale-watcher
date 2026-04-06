"""
One-time script to generate a Telegram session string.

Usage:
    python scripts/telegram_auth.py

You'll need:
1. Go to https://my.telegram.org and create an application
2. Get your api_id and api_hash
3. Run this script and enter your phone number
4. Enter the verification code sent to your Telegram
5. Copy the session string into your .env file as TELEGRAM_SESSION_STRING
"""
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession


async def main():
    print("=== Telegram Session Generator ===\n")
    print("Get api_id and api_hash from https://my.telegram.org\n")

    api_id = input("Enter api_id: ").strip()
    api_hash = input("Enter api_hash: ").strip()

    client = TelegramClient(StringSession(), int(api_id), api_hash)

    await client.start()

    session_string = client.session.save()

    print(f"\n{'='*60}")
    print("Your session string (add to .env):\n")
    print(f"TELEGRAM_SESSION_STRING={session_string}")
    print(f"\n{'='*60}")
    print("Keep this string secret! It provides access to your account.")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
