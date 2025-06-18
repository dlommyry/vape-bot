
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message
from aiogram.utils import executor
from config import BOT_TOKEN
import logging

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

@dp.message_handler(commands=["start"])
async def start(message: Message):
    await message.answer("Привет! Это тестовый магазин. Каталог и корзина скоро будут тут!")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
