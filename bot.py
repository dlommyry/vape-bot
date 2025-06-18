from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from config import BOT_TOKEN
import logging

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

main_menu = ReplyKeyboardMarkup(resize_keyboard=True)
main_menu.add(KeyboardButton("🛍 Каталог"), KeyboardButton("🧺 Корзина"))
main_menu.add(KeyboardButton("📞 Поддержка"))

@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    await message.answer("Добро пожаловать в Plumbus Shop! Выберите действие:", reply_markup=main_menu)

@dp.message_handler(lambda message: message.text == "📞 Поддержка")
async def support(message: types.Message):
    await message.answer("Для связи с поддержкой: @PlumbusSupport")

@dp.message_handler(lambda message: message.text == "🛍 Каталог")
async def catalog(message: types.Message):
    text = (
        "Выберите категорию:\n"
        "1️⃣ В наличии\n"
        "2️⃣ Нет в наличии\n"
        "(Категории скоро будут активны)"
    )
    await message.answer(text)

@dp.message_handler(lambda message: message.text == "🧺 Корзина")
async def cart(message: types.Message):
    await message.answer("Ваша корзина пока пуста. Скоро добавим товары :)")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
