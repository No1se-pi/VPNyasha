import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

API_TOKEN = ""

logging.basicConfig(level=logging.INFO)

dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    text = (
        "👋 Привет! Я мини‑бот для ID.\n\n"
        f"🧑 Твой user_id: <code>{user_id}</code>\n"
        f"💬 chat_id этого чата: <code>{chat_id}</code>\n\n"
        "Отправь мне стикер — пришлю его <b>file_id</b>."
    )
    await message.answer(text)

@dp.message(F.sticker)
async def sticker_id(message: Message):
    sticker = message.sticker
    file_id = sticker.file_id
    await message.answer(
        "👉 <b>file_id этого стикера:</b>\n"
        f"<code>{file_id}</code>"
    )

async def main():
    bot = Bot(
        token=API_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
