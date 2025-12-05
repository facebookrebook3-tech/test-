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
BOT_TOKEN = os.getenv('BOT_TOKEN')
MERCHANT_PUBLIC_KEY = os.getenv('MERCHANT_PUBLIC_KEY') # ID проекта (например 378)
MERCHANT_SECRET_KEY = os.getenv('MERCHANT_SECRET_KEY')

# ВАЖНО: Укажите здесь ваш домен на Render без слеша в конце
# Пример: https://my-app.onrender.com
WEBHOOK_HOST = os.getenv('RENDER_EXTERNAL_URL') # Render сам создает эту переменную, но проверьте
if not WEBHOOK_HOST:
    # Если переменной нет, впишите вручную свой домен ниже:
    WEBHOOK_HOST = "https://ВАШ-ПРОЕКТ.onrender.com"

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

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
    """
    base_url = "https://api.pay4bit.net/pay" # Или api.4bill.io (зависит от вашей платежки)
    account = str(user_id)
    amount_formatted = "{:.2f}".format(amount_val)
    desc = f"Order_{user_id}"
    currency = "UAH"
    
    # Строка для подписи создания ссылки (SHA256)
    # Формула: account + currency + desc + sum + secret (Порядок важен, сверьте с докой!)
    # Обычно для 4Bill/Pay4Bit порядок: account + sum + currency + desc + secret? 
    # В ВАШЕМ СТАРОМ КОДЕ БЫЛО: desc + account + sum + secret. Оставляю как у вас работало.
    raw = desc + account + amount_formatted + MERCHANT_SECRET_KEY
    sign = hashlib.sha256(raw.encode()).hexdigest()
    
    params = {
        'public_key': MERCHANT_PUBLIC_KEY,
        'account': account,
        'sum': amount_formatted,
        'desc': desc,
        'currency': currency,
        'sign': sign,
        'result_url': WEBHOOK_URL # <--- Важно: говорим платежке, куда слать ответ
    }
    return f"{base_url}?{urllib.parse.urlencode(params)}"

# --- ОБРАБОТЧИК ВЕБХУКА (ОПЛАТА) ---
async def pay4bit_handler(request):
    try:
        # Получаем данные (поддержка GET и POST/JSON)
        if request.method == 'POST':
            try:
                data = await request.json()
            except:
                data = await request.post()
        else:
            data = request.query
        
        logging.info(f"Incoming webhook: {data}")

        # Извлекаем данные
        payment_id = get_param(data, 'paymentId') or get_param(data, 'localpayId')
        account_id = get_param(data, 'account')
        amount = get_param(data, 'amount') or get_param(data, 'sum')
        req_sign = get_param(data, 'sign')
        method = data.get('method') # check или pay

        # Если это просто пинг корневой страницы
        if not payment_id and not account_id:
             return web.Response(text="Bot is running", status=200)

        # Проверка наличия обязательных полей
        if not all([payment_id, account_id, amount, req_sign]):
            logging.error("Missing required params in webhook")
            return web.Response(text="Bad Request: Missing params", status=400)

        # --- ПРОВЕРКА ПОДПИСИ (MD5) ---
        # Формируем строку для проверки. Платежка может слать "10" или "10.00".
        # Проверяем оба варианта, чтобы наверняка.
        
        try:
            # Вариант 1: Как пришло (например "10")
            raw_str_1 = f"{payment_id}{account_id}{amount}{MERCHANT_SECRET_KEY}"
            sign_1 = hashlib.md5(raw_str_1.encode()).hexdigest()

            # Вариант 2: Принудительно с .00 (например "10.00")
            amount_float = float(amount)
            amount_formatted = "{:.2f}".format(amount_float)
            raw_str_2 = f"{payment_id}{account_id}{amount_formatted}{MERCHANT_SECRET_KEY}"
            sign_2 = hashlib.md5(raw_str_2.encode()).hexdigest()
        except Exception as e:
            logging.error(f"Error calculating hash: {e}")
            sign_1 = "error"
            sign_2 = "error"

        # Сравниваем пришедшую подпись с нашими вариантами
        is_valid = (req_sign.lower() == sign_1.lower()) or (req_sign.lower() == sign_2.lower())

        if is_valid:
            # 1. ОБРАБОТКА 'CHECK'
            if method == 'check':
                logging.info(f"Check passed for user {account_id}")
                return web.Response(text="OK", status=200)

            # 2. ОБРАБОТКА 'PAY' (ОПЛАТА)
            elif method == 'pay' or method is None:
                logging.info(f"Payment success for user {account_id}, amount {amount}")

                # --- ЗАЩИТА ОТ ТЕСТОВЫХ ЗАПРОСОВ ---
                if str(account_id).lower() == "test":
                    logging.info("Test payment received. Skipping Telegram notification.")
                    return web.Response(text="OK", status=200)
                
                try:
                    # Уведомляем пользователя
                    await bot.send_message(
                        chat_id=account_id,
                        text=f"✅ Оплата {amount} UAH прошла успешно!"
                    )
                    
                    # --- ВЫДАЧА ТОВАРА ---
                    product_text = "Ета типа ваш тавар пака"
                    await bot.send_message(chat_id=account_id, text=product_text)
                    # ---------------------
                    
                except Exception as e:
                    # Логируем ошибку, но платежке отвечаем ОК, так как деньги мы получили
                    logging.error(f"Failed to send message to user: {e}")
                
                return web.Response(text="OK", status=200)
            
            else:
                logging.warning(f"Unknown method: {method}")
                return web.Response(text="OK", status=200)

        else:
            logging.error(f"Sign mismatch! Req: {req_sign}. Calc1: {sign_1}, Calc2: {sign_2}")
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
    url = generate_link(callback.from_user.id, 25.00)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оплатить 25 UAH", url=url)]
    ])
    await callback.message.answer("Ссылка для оплаты сформирована:", reply_markup=kb)
    await callback.answer()

# --- ЗАПУСК ---
async def start_bot_polling(app):
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    app = web.Application()
    app.router.add_route('*', WEBHOOK_PATH, pay4bit_handler)
    app.on_startup.append(start_bot_polling)
    
    port = int(os.environ.get("PORT", 8080))
    logging.info(f"Starting server on port {port}")
    web.run_app(app, host="0.0.0.0", port=port)
