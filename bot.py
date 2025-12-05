import telebot
from telebot import types
import hashlib
import urllib.parse
from flask import Flask, request
import threading

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = '8074643658:AAHG5ji4KS6c76X0P6Gjhz4t5fzsyXpyEvA'
MERCHANT_PUBLIC_KEY = '87948-378'
MERCHANT_SECRET_KEY = '94f0f4c5fa8396533189513d4532e92f'

# Настройки для приема уведомлений (Webhook)
WEBHOOK_HOST = '0.0.0.0'
WEBHOOK_PORT = 5000

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# --- ФУНКЦИИ ГЕНЕРАЦИИ ССЫЛКИ ---
def generate_pay4bit_link(user_id, amount_val):
    base_url = "https://api.pay4bit.net/pay"
    account = str(user_id)
    amount_formatted = "{:.2f}".format(amount_val)
    desc = f"Payment for User {user_id}"
    currency = "UAH"

    # Формируем подпись запроса (SHA256)
    raw_string = desc + account + amount_formatted + MERCHANT_SECRET_KEY
    sign = hashlib.sha256(raw_string.encode('utf-8')).hexdigest()

    params = {
        'public_key': MERCHANT_PUBLIC_KEY,
        'account': account,
        'sum': amount_formatted,
        'desc': desc,
        'currency': currency,
        'sign': sign
    }
    query_string = urllib.parse.urlencode(params)
    return f"{base_url}?{query_string}"

# --- ОБРАБОТЧИК УВЕДОМЛЕНИЙ ОТ PAY4BIT (FLASK) ---
@app.route('/callback', methods=['POST', 'GET'])
def payment_callback():
    # Pay4Bit отправляет данные либо в POST, либо в GET параметрах
    # Обычно это GET параметры, но проверим оба варианта
    data = request.args if request.method == 'GET' else request.form
    
    # Получаем параметры из уведомления
    payment_id = data.get('paymentId')
    account_id = data.get('account') # Это наш user_id
    amount = data.get('amount')      # Сумма платежа (может прийти как 'sum' или 'amount')
    if not amount: amount = data.get('sum')
    
    req_sign = data.get('sign')      # Подпись от Pay4Bit для проверки

    if not payment_id or not account_id or not req_sign:
        return "Missing parameters", 400

    # ПРОВЕРКА ПОДПИСИ (Безопасность)
    # Согласно документации для Callback используется MD5!
    # Формула: md5($paymentid.$account.$sum.$merchant_secret_key)
    raw_check = f"{payment_id}{account_id}{amount}{MERCHANT_SECRET_KEY}"
    my_sign = hashlib.md5(raw_check.encode('utf-8')).hexdigest()

    if req_sign == my_sign:
        # --- УСПЕШНАЯ ОПЛАТА ---
        print(f"✅ Оплата прошла! User: {account_id}, Сумма: {amount}")
        
        try:
            # Отправляем сообщение пользователю в Telegram
            bot.send_message(account_id, f"🎉 Оплата получена!\nСумма: {amount} UAH зачислена на ваш счет.")
            
            # ТУТ МОЖНО ДОБАВИТЬ ЛОГИКУ ЗАЧИСЛЕНИЯ В БАЗУ ДАННЫХ
            # database.add_balance(account_id, amount)
            
        except Exception as e:
            print(f"Ошибка отправки сообщения ботом: {e}")

        return "OK", 200
    else:
        print(f"❌ Ошибка подписи! Пришло: {req_sign}, Ждали: {my_sign}")
        return "Sign Error", 400

def run_flask():
    app.run(host=WEBHOOK_HOST, port=WEBHOOK_PORT)

# --- БОТ ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.InlineKeyboardMarkup()
    pay_btn = types.InlineKeyboardButton("Оплатить 100 грн", callback_data="init_payment")
    markup.add(pay_btn)
    bot.reply_to(message, "Тест.", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "init_payment")
def handle_payment(call):
    try:
        amount = 100.00
        user_id = call.from_user.id
        payment_url = generate_pay4bit_link(user_id, amount)
        
        markup = types.InlineKeyboardMarkup()
        url_btn = types.InlineKeyboardButton(f"Оплатить {amount} UAH", url=payment_url)
        markup.add(url_btn)
        
        bot.send_message(call.message.chat.id, f"Сумма на {amount} грн создан.\n.", reply_markup=markup)
        bot.answer_callback_query(call.id)
    except Exception as e:
        bot.send_message(call.message.chat.id, f"Ошибка: {e}")

if __name__ == '__main__':
    # Запускаем Flask сервер в отдельном потоке, чтобы бот не завис
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    print("Бот и Webhook-сервер запущены...")
    bot.infinity_polling()