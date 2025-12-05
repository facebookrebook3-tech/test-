import os
import hashlib
import logging
import urllib.parse
import asyncio
from aiohttp import web

# Библиотеки aiogram
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MERCHANT_PUBLIC_KEY = os.getenv('MERCHANT_PUBLIC_KEY')
MERCHANT_SECRET_KEY = os.getenv('MERCHANT_SECRET_KEY')

# URL вашего приложения на Render
WEBHOOK_HOST = os.getenv('RENDER_EXTERNAL_URL')
if not WEBHOOK_HOST:
    WEBHOOK_HOST = "https://test-u8ew.onrender.com"

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

# --- ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
logging.basicConfig(level=logging.INFO)

# --- СОСТОЯНИЯ (FSM) ---
class TopUpState(StatesGroup):
    waiting_for_currency = State() # Ожидание выбора валюты
    waiting_for_amount = State()   # Ожидание ввода суммы

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_param(data, key):
    """Извлекает значение ключа из data"""
    if key in data:
        return data[key]
    if f'params[{key}]' in data:
        return data[f'params[{key}]']
    return None

def generate_link(user_id, amount_val, currency_code="UAH"):
    """
    Генерирует ссылку на оплату с учетом валюты.
    """
    base_url = "https://api.pay4bit.net/pay" # Или api.4bill.io
    account = str(user_id)
    # Форматируем сумму всегда с 2 знаками (25 -> 25.00)
    amount_formatted = "{:.2f}".format(float(amount_val))
    desc = f"TopUp_{user_id}"
    
    # Подпись
    raw = desc + account + amount_formatted + MERCHANT_SECRET_KEY
    sign = hashlib.sha256(raw.encode()).hexdigest()
    
    params = {
        'public_key': MERCHANT_PUBLIC_KEY,
        'account': account,
        'sum': amount_formatted,
        'desc': desc,
        'currency': currency_code,
        'sign': sign,
        'result_url': WEBHOOK_URL
    }
    return f"{base_url}?{urllib.parse.urlencode(params)}"

# --- ОБРАБОТЧИК ВЕБХУКА (ОПЛАТА) ---
async def pay4bit_handler(request):
    try:
        if request.method == 'POST':
            try:
                data = await request.json()
            except:
                data = await request.post()
        else:
            data = request.query
        
        logging.info(f"Incoming webhook: {data}")

        payment_id = get_param(data, 'paymentId') or get_param(data, 'localpayId')
        account_id = get_param(data, 'account')
        req_sign = get_param(data, 'sign')
        method = data.get('method')
        
        currency_in_resp = get_param(data, 'currency') or "UAH"
        val_sum = get_param(data, 'sum')
        val_amount = get_param(data, 'amount')

        if not payment_id and not account_id:
             return web.Response(text="Bot is running", status=200)

        if not all([payment_id, account_id, req_sign]):
            return web.Response(text="Bad Request", status=400)

        # --- ПРОВЕРКА ПОДПИСИ ---
        candidates = []
        if val_sum: candidates.append(val_sum)
        if val_amount: candidates.append(val_amount)
        if val_sum: 
            try: candidates.append("{:.2f}".format(float(val_sum)))
            except: pass
        if val_amount:
            try:
                if str(val_amount).endswith('.00'):
                    candidates.append(str(val_amount)[:-3])
                else:
                    candidates.append(str(int(float(val_amount))))
            except: pass

        unique_amounts = list(set(candidates))
        is_valid = False
        valid_amount_str = "0"

        for amt in unique_amounts:
            check_str = f"{payment_id}{account_id}{amt}{MERCHANT_SECRET_KEY}"
            my_sign = hashlib.md5(check_str.encode()).hexdigest()
            if my_sign.lower() == req_sign.lower():
                is_valid = True
                valid_amount_str = amt
                break

        if is_valid:
            if method == 'check':
                return web.Response(text="OK", status=200)

            elif method == 'pay' or method is None:
                if str(account_id).lower() == "test":
                    logging.info("Test payment confirmed.")
                    return web.Response(text="OK", status=200)

                try:
                    await bot.send_message(
                        chat_id=account_id,
                        text=f"✅ Баланс успешно пополнен на <b>{valid_amount_str} {currency_in_resp}</b>",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logging.error(f"Telegram error: {e}")
                
                return web.Response(text="OK", status=200)
        else:
            logging.error(f"Sign ERROR. Req: {req_sign}. Variants: {unique_amounts}")
            return web.Response(text="Sign Error", status=403)

    except Exception as e:
        logging.error(f"Handler error: {e}")
        return web.Response(text="Error", status=500)

# --- ЛОГИКА БОТА ---

# 1. СТАРТ
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇺🇦 UAH", callback_data="curr_UAH"),
            InlineKeyboardButton(text="🇪🇺 EUR", callback_data="curr_EUR")
        ]
    ])
    
    await message.answer("Выберите валюту для пополнения:", reply_markup=kb)
    await state.set_state(TopUpState.waiting_for_currency)

# 2. ВЫБОР ВАЛЮТЫ
@dp.callback_query(F.data.startswith("curr_"))
async def process_currency_selection(callback: types.CallbackQuery, state: FSMContext):
    chosen_currency = callback.data.split("_")[1]
    
    await state.update_data(currency=chosen_currency)
    
    # Определяем текст минимальной суммы для сообщения
    min_sum_text = "25" if chosen_currency == "UAH" else "1"
    
    await callback.message.edit_text(
        f"Выбрано: <b>{chosen_currency}</b>.\nТеперь введите сумму пополнения минимум {min_sum_text}:", 
        parse_mode="HTML"
    )
    await state.set_state(TopUpState.waiting_for_amount)

# 3. ВВОД СУММЫ
@dp.message(TopUpState.waiting_for_amount)
async def process_amount(message: types.Message, state: FSMContext):
    user_text = message.text.replace(',', '.')
    
    try:
        amount = float(user_text)
        
        # Получаем выбранную валюту
        data = await state.get_data()
        currency = data.get('currency', 'UAH')
        
        # --- ПРОВЕРКА ЛИМИТОВ ---
        # Если UAH -> минимум 25, Если EUR -> минимум 1
        min_limit = 25 if currency == "UAH" else 1

        if amount < min_limit:
            await message.answer(
                f"⚠️ Минимальная сумма для {currency} — <b>{min_limit}</b>. Введите сумму снова:", 
                parse_mode="HTML"
            )
            return
        
        if amount > 100000:
            await message.answer("Слишком большая сумма. Введите снова:")
            return

        # Генерация ссылки
        pay_url = generate_link(message.from_user.id, amount, currency)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Оплатить {amount} {currency}", url=pay_url)]
        ])
        
        await message.answer(
            f"Сума оплаты: <b>{amount} {currency}</b>\n"
            "Нажмите кнопку ниже для перехода к оплате.",
            reply_markup=kb,
            parse_mode="HTML"
        )
        
        await state.clear()

    except ValueError:
        await message.answer("❌ Это не число. Введите сумму цифрами (например: 50):")

# --- ЗАПУСК ---
async def start_bot_polling(app):
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    app = web.Application()
    app.router.add_route('*', WEBHOOK_PATH, pay4bit_handler)
    app.on_startup.append(start_bot_polling)
    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, host="0.0.0.0", port=port)
