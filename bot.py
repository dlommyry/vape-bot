
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
from config import BOT_TOKEN
import logging

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# Состояние корзины
user_cart = {}

# Главное меню
main_menu = ReplyKeyboardMarkup(resize_keyboard=True)
main_menu.add(KeyboardButton("🛍 Каталог"), KeyboardButton("🧺 Корзина"))
main_menu.add(KeyboardButton("📞 Поддержка"))

# Каталог
catalog_items = {
    "💨 Elf Bar 600": "Вкус: арбуз, никотин: 2%, цена: 350₽",
    "🔋 HQD Cuvie Air": "Вкус: манго, никотин: 5%, цена: 750₽"
}

@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    await message.answer("Добро пожаловать в Plumbus Shop! Выберите действие:", reply_markup=main_menu)

@dp.message_handler(lambda m: m.text == "📞 Поддержка")
async def support(message: types.Message):
    await message.answer("Напиши нам: @PlumbusSupport")

@dp.message_handler(lambda m: m.text == "🛍 Каталог")
async def catalog(message: types.Message):
    markup = InlineKeyboardMarkup()
    for name in catalog_items:
        markup.add(InlineKeyboardButton(name, callback_data=f"buy:{name}"))
    await message.answer("Выберите товар из каталога:", reply_markup=markup)

@dp.callback_query_handler(lambda c: c.data.startswith("buy:"))
async def add_to_cart(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    item = callback.data.split("buy:")[1]
    user_cart.setdefault(user_id, []).append(item)
    await callback.answer("Добавлено в корзину ✅")
    await callback.message.answer(f"{item} добавлен в корзину.")

@dp.message_handler(lambda m: m.text == "🧺 Корзина")
async def cart(message: types.Message):
    cart = user_cart.get(message.from_user.id, [])
    if not cart:
        await message.answer("Ваша корзина пуста.")
    else:
        text = "\n".join(f"• {item}" for item in cart)
        await message.answer(f"🧺 Ваша корзина:\n{text}\n\nЧтобы оформить заказ, напиши: Оформить")

@dp.message_handler(lambda m: m.text.lower() == "оформить")
async def checkout(message: types.Message):
    cart = user_cart.get(message.from_user.id, [])
    if not cart:
        await message.answer("Сначала добавьте товары в корзину.")
    else:
        order = "\n".join(cart)
        await message.answer(f"Ваш заказ принят:\n{order}\nМенеджер скоро свяжется с вами 🙌")
        user_cart[message.from_user.id] = []

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
