
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.utils import executor
from config import BOT_TOKEN, ADMINS
import sqlite3
import logging

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# --- Инициализация БД ---
conn = sqlite3.connect("vape_shop.db")
cursor = conn.cursor()
cursor.execute("""CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    description TEXT,
    quantity INTEGER,
    category TEXT
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS waitlist (
    user_id INTEGER,
    product_name TEXT
)""")
conn.commit()

# --- Интерфейсы ---
main_menu = ReplyKeyboardMarkup(resize_keyboard=True)
main_menu.add(KeyboardButton("🛍 Каталог"), KeyboardButton("🧺 Корзина"))
main_menu.add(KeyboardButton("📞 Поддержка"))

admin_menu = ReplyKeyboardMarkup(resize_keyboard=True)
admin_menu.add(KeyboardButton("➕ Добавить товар"), KeyboardButton("❌ Удалить товар"))
admin_menu.add(KeyboardButton("✏️ Изменить остаток"), KeyboardButton("📦 Остатки"))
admin_menu.add(KeyboardButton("⬅️ Назад"))

user_cart = {}

@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    if str(message.from_user.id) in ADMINS:
        await message.answer("Админ-панель открыта:", reply_markup=admin_menu)
    else:
        await message.answer("Добро пожаловать в Plumbus Shop! Выберите действие:", reply_markup=main_menu)

@dp.message_handler(lambda m: m.text == "📞 Поддержка")
async def support(message: types.Message):
    await message.answer("Для связи с поддержкой: @PlumbusSupport")

@dp.message_handler(lambda m: m.text == "🛍 Каталог")
async def catalog(message: types.Message):
    markup = InlineKeyboardMarkup()
    cursor.execute("SELECT name, quantity FROM products")
    for name, qty in cursor.fetchall():
        label = f"{name} ({'в наличии' if qty > 0 else 'нет в наличии'})"
        markup.add(InlineKeyboardButton(label, callback_data=f"view:{name}"))
    await message.answer("📦 Товары:", reply_markup=markup)

@dp.callback_query_handler(lambda c: c.data.startswith("view:"))
async def view_product(callback: types.CallbackQuery):
    name = callback.data.split("view:")[1]
    cursor.execute("SELECT description, quantity FROM products WHERE name = ?", (name,))
    row = cursor.fetchone()
    if row:
        description, qty = row
        markup = InlineKeyboardMarkup()
        if qty > 0:
            markup.add(InlineKeyboardButton("🛒 В корзину", callback_data=f"buy:{name}"))
        else:
            markup.add(InlineKeyboardButton("🔔 Уведомить при наличии", callback_data=f"wait:{name}"))
        await callback.message.answer(f"""📝 {name}
{description}
Остаток: {qty}""", reply_markup=markup)

@dp.callback_query_handler(lambda c: c.data.startswith("buy:"))
async def add_to_cart(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    item = callback.data.split("buy:")[1]
    user_cart.setdefault(user_id, []).append(item)
    await callback.answer("Добавлено в корзину ✅")

@dp.callback_query_handler(lambda c: c.data.startswith("wait:"))
async def add_to_waitlist(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    item = callback.data.split("wait:")[1]
    cursor.execute("INSERT INTO waitlist (user_id, product_name) VALUES (?, ?)", (user_id, item))
    conn.commit()
    await callback.answer("Добавлен в лист ожидания 🔔")

@dp.message_handler(lambda m: m.text == "🧺 Корзина")
async def show_cart(message: types.Message):
    cart = user_cart.get(message.from_user.id, [])
    if not cart:
        await message.answer("Корзина пуста.")
        return
    order_text = "\n".join(cart)
    await message.answer(f"🧺 Ваша корзина:\n{order_text}\n(Оплата временно отключена. Заказ оформляется вручную.)")

# --- Админ-функции ---
@dp.message_handler(lambda m: m.text == "📦 Остатки" and str(m.from_user.id) in ADMINS)
async def show_stock(message: types.Message):
    cursor.execute("SELECT name, quantity FROM products")
    text = "\n".join(f"{name}: {qty}" for name, qty in cursor.fetchall())
    await message.answer("📦 Склад:\n" + text)
