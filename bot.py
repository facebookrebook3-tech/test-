import os
import hashlib
import logging
import urllib.parse
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- НАСТРОЙКИ БЕРЕМ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ (Безопасно) ---
# Если запускаешь локально, можно вписать значения по умолчанию вторым аргументом
BOT_TOKEN = os.getenv('BOT_TOKEN')
MERCHANT_PUBLIC_KEY = os.getenv('MERCHANT_PUBLIC_KEY')
MERCHANT_SECRET_KEY = os.getenv('MERCHANT_SECRET_KEY')

# URL путь для приема уведомлений
WEBHOOK_PATH = "/webhook"

# --- ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# --- ГЕНЕРАТОР ССЫЛКИ ---
def generate_link(user_id, amount_val):
    base_url = "https://api.pay4bit.net/pay"
    account = str(user_id)
    amount_formatted = "{:.2f}".format(amount_val)
    desc = f"Order_{user_id}"
    currency = "UAH"
    
    raw = desc + account + amount_formatted + MERCHANT_SECRET_KEY
    sign = hashlib.sha256(raw.encode()).hexdigest()
    
    params = {
        'public_key': MERCHANT_PUBLIC_KEY,
        'account': account,
        'sum': amount_formatted,
        'desc': desc,
        'currency': currency,
        'sign': sign
    }
    return f"{base_url}?{urllib.parse.urlencode(params)}"

# --- ОБРАБОТЧИК ОПЛАТЫ (AIOHTTP) ---
async def pay4bit_handler(request):
    # Pay4Bit обычно шлет GET параметры
    data = request.query if request.method == 'GET' else await request.post()
    
    payment_id = data.get('paymentId')
    account_id = data.get('account') 
    amount = data.get('amount') or data.get('sum')
    req_sign = data.get('sign')

    if not all([payment_id, account_id, amount, req_sign]):
        return web.Response(text="Bad Request", status=400)

    # Проверка подписи (MD5)
    check_str = f"{payment_id}{account_id}{amount}{MERCHANT_SECRET_KEY}"
    my_sign = hashlib.md5(check_str.encode()).hexdigest()

    if req_sign == my_sign:
        try:
            await bot.send_message(
                chat_id=account_id,
                text=f"✅ Оплата {amount} UAH получена! Спасибо."
            )
            return web.Response(text="OK", status=200)
        except Exception as e:
            logging.error(f"Error sending msg: {e}")
            return web.Response(text="Bot Error", status=500)
    else:
        return web.Response(text="Sign Error", status=403)

# --- БОТ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Купить за 100 грн", callback_data="buy_100")]
    ])
    await message.answer("Бот работает на Render!", reply_markup=kb)

@dp.callback_query(F.data == "buy_100")
async def cb_buy(callback: types.CallbackQuery):
    url = generate_link(callback.from_user.id, 100.00)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Перейти к оплате", url=url)]
    ])
    await callback.message.answer("Ссылка готова:", reply_markup=kb)
    await callback.answer()

# --- ФОНОВАЯ ЗАДАЧА ДЛЯ БОТА ---
async def start_bot_polling(app):
    # Запускаем поллинг в фоне
    asyncio.create_task(dp.start_polling(bot))

# --- ЗАПУСК ---
if __name__ == "__main__":
    app = web.Application()
    app.router.add_route('*', WEBHOOK_PATH, pay4bit_handler)
    app.on_startup.append(start_bot_polling)
    
    # ВАЖНО: Render выдает порт через переменную окружения PORT
    port = int(os.environ.get("PORT", 8080))
    
    web.run_app(app, host="0.0.0.0", port=port)