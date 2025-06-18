
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
from config import BOT_TOKEN, ADMINS, TON_WALLET
import logging

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

user_cart = {}

main_menu = ReplyKeyboardMarkup(resize_keyboard=True)
main_menu.add(KeyboardButton("🛍 Каталог"), KeyboardButton("🧺 Корзина"))
main_menu.add(KeyboardButton("📞 Поддержка"))

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
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("✅ Оплатить TON (-7%)", url=f"https://t.me/tonRocketBot?start={TON_WALLET}"))
        markup.add(InlineKeyboardButton("💳 Оплатить картой", callback_data="pay_card"))
        await message.answer(f"🧺 Ваша корзина:\n{text}", reply_markup=markup)

@dp.callback_query_handler(lambda c: c.data == "pay_card")
async def pay_card(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    cart = user_cart.get(user_id, [])
    if not cart:
        await callback.message.answer("Корзина пуста.")
    else:
        order_text = "\n".join(cart)
        order_id = f"ORD{user_id}{callback.message.message_id}"
        msg = f"🛒 Новый заказ\nID: {order_id}\nПользователь: @{callback.from_user.username or callback.from_user.id}\n\n{order_text}"
        for admin in ADMINS:
            await bot.send_message(admin, msg)
        await callback.message.answer("Ваш заказ принят! Менеджер свяжется с вами.")
        user_cart[user_id] = []

@dp.message_handler(lambda m: m.text.lower() == "оформить")
async def checkout(message: types.Message):
    await message.answer("Пожалуйста, нажмите кнопку оплаты в корзине.")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
