import os
import hashlib
import logging
import urllib.parse
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- НАСТРОЙКИ ---
# Убедитесь, что эти переменные добавлены в Environment Variables на Render
BOT_TOKEN = os.getenv('BOT_TOKEN')
MERCHANT_PUBLIC_KEY = os.getenv('MERCHANT_PUBLIC_KEY')
MERCHANT_SECRET_KEY = os.getenv('MERCHANT_SECRET_KEY')

# Путь, на который платежка шлет запросы
WEBHOOK_PATH = "/webhook"

# --- ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_param(data, key):
    """
    Извлекает значение из данных запроса.
    Ищет ключ как в чистом виде 'key', так и в формате массива 'params[key]'.
    """
    if key in data:
        return data[key]
    if f'params[{key}]' in data:
        return data[f'params[{key}]']
    return None

def generate_link(user_id, amount_val):
    """
    Генерирует ссылку на оплату.
    Использует SHA256 для подписи при создании заказа.
    """
    base_url = "https://api.pay4bit.net/pay"
    account = str(user_id)
    amount_formatted = "{:.2f}".format(amount_val)
    desc = f"Order_{user_id}"
    currency = "UAH"
    
    # Строка для подписи создания ссылки (SHA256)
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

# --- ОБРАБОТЧИК ВЕБХУКА (ОПЛАТА) ---
async def pay4bit_handler(request):
    try:
        # Получаем данные (поддержка GET и POST)
        data = request.query if request.method == 'GET' else await request.post()
        
        logging.info(f"Incoming webhook: {data}")

        # Извлекаем данные с помощью умной функции
        # Платежка может слать paymentId или localpayId
        payment_id = get_param(data, 'paymentId') or get_param(data, 'localpayId')
        account_id = get_param(data, 'account')
        # Сумма может быть amount или sum
        amount = get_param(data, 'amount') or get_param(data, 'sum')
        req_sign = get_param(data, 'sign')
        method = data.get('method') # check или pay

        # Проверка наличия обязательных полей
        if not all([payment_id, account_id, amount, req_sign]):
            logging.error("Missing required params in webhook")
            return web.Response(text="Bad Request: Missing params", status=400)

        # Проверка подписи (Для колбеков используется MD5!)
        # Формула: md5(id + account + sum + SECRET)
        check_str = f"{payment_id}{account_id}{amount}{MERCHANT_SECRET_KEY}"
        my_sign = hashlib.md5(check_str.encode()).hexdigest()

        # Сравнение подписей (регистронезависимое)
        if req_sign.lower() == my_sign.lower():
            
            # 1. ОБРАБОТКА 'CHECK' (Предварительная проверка от системы)
            if method == 'check':
                logging.info(f"Check passed for user {account_id}")
                return web.Response(text="OK", status=200)

            # 2. ОБРАБОТКА 'PAY' (Фактическая оплата)
            elif method == 'pay' or method is None:
                logging.info(f"Payment success for user {account_id}, amount {amount}")
                
                try:
                    # Уведомляем пользователя
                    await bot.send_message(
                        chat_id=account_id,
                        text=f"✅ Оплата {amount} UAH прошла успешно!"
                    )
                    
                    # --- ЗДЕСЬ ВЫДАЕМ ТОВАР ---
                    product_text = "Спасибо за покупку! Вот ваша ссылка: https://t.me/+AbCdEfGhIjKlMnOp"
                    await bot.send_message(chat_id=account_id, text=product_text)
                    # --------------------------
                    
                except Exception as e:
                    logging.error(f"Failed to send message to user: {e}")
                
                # Всегда возвращаем OK платежке, если подпись верна
                return web.Response(text="OK", status=200)
            
            else:
                logging.warning(f"Unknown method: {method}")
                return web.Response(text="OK", status=200)

        else:
            logging.error(f"Sign mismatch! Req: {req_sign}, Calc: {my_sign}")
            return web.Response(text="Sign Error", status=403)

    except Exception as e:
        logging.error(f"Critical error in webhook handler: {e}")
        return web.Response(text="Internal Error", status=500)

# --- ЛОГИКА БОТА ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Купить за 25 грн", callback_data="buy_25")]
    ])
    await message.answer("Добро пожаловать в магазин!", reply_markup=kb)

@dp.callback_query(F.data == "buy_25")
async def cb_buy(callback: types.CallbackQuery):
    # Генерируем ссылку для юзера на 25 грн
    url = generate_link(callback.from_user.id, 25.00)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оплатить 25 UAH", url=url)]
    ])
    await callback.message.answer("Ссылка для оплаты сформирована:", reply_markup=kb)
    await callback.answer()

# --- ЗАПУСК ПРИЛОЖЕНИЯ ---
async def start_bot_polling(app):
    # Удаляем вебхук, чтобы сбросить конфликты (ConflictError)
    await bot.delete_webhook(drop_pending_updates=True)
    # Запускаем бота в режиме polling (на фоне)
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    app = web.Application()
    
    # Регистрируем маршрут для вебхука
    app.router.add_route('*', WEBHOOK_PATH, pay4bit_handler)
    
    # Добавляем задачу запуска бота при старте сервера
    app.on_startup.append(start_bot_polling)
    
    # Получаем порт от Render (или используем 8080 по умолчанию)
    port = int(os.environ.get("PORT", 8080))
    
    # Запускаем веб-сервер
    web.run_app(app, host="0.0.0.0", port=port)
