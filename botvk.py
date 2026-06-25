import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
import sqlite3
import random
from datetime import datetime
import os
from dotenv import load_dotenv
from openai import OpenAI

# ============ ЗАГРУЗКА ПЕРЕМЕННЫХ ============
load_dotenv()

# VK
VK_TOKEN = os.getenv('VK_TOKEN')
VK_GROUP_ID = int(os.getenv('VK_GROUP_ID', 0))
VK_MANAGER_ID = int(os.getenv('VK_MANAGER_ID', 0))

# AITunnel (ключ AI, общий с другими ботами)
GEMINI_KEY = os.getenv('GEMINI_KEY')

# Режим
DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'

# Проверка обязательных переменных
if not VK_TOKEN:
    raise ValueError("❌ VK_TOKEN не найден в .env файле!")
if not GEMINI_KEY:
    raise ValueError("❌ GEMINI_KEY не найден в .env файле!")
if not VK_MANAGER_ID:
    print("⚠️ ВНИМАНИЕ: VK_MANAGER_ID не задан! Уведомления не будут работать.")

# ============ ИНИЦИАЛИЗАЦИЯ AI (AITunnel, openai-формат) ============
client = OpenAI(
    api_key=GEMINI_KEY,
    base_url="https://api.aitunnel.ru/v1/"
)

# Модель (та же, что работает в других ботах через AITunnel)
AI_MODEL = "gemini-2.5-flash-lite-preview-09-2025"

# Системная инструкция (роль ассистента)
SYSTEM_PROMPT = """Ты - AI-ассистент лесоторговой компании "Древесина-Про".

Твоя роль:
1. Помогать клиентам с выбором древесины для их задач
2. Отвечать на вопросы о ценах, наличии и свойствах
3. Консультировать по применению разных пород (строительство, мебель, отделка)
4. Если клиент хочет заказать - направляй к оформлению через каталог
5. Будь вежливым, профессиональным и дружелюбным

Доступные породы:
- 🌲 Сосна: 1500 руб/куб, в наличии 50 куб.м
- 🌳 Дуб: 4500 руб/куб, в наличии 20 куб.м
- 🌿 Береза: 2000 руб/куб, в наличии 35 куб.м

Важно: не пытайся оформить заказ самостоятельно, направляй к кнопке "Каталог" в меню.
Отвечай на русском языке, кратко и по делу."""

# ============ БАЗА ДАННЫХ ============
def init_db():
    conn = sqlite3.connect('wood_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            price INTEGER,
            stock REAL,
            description TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_name TEXT,
            wood_type TEXT,
            quantity REAL,
            total_price INTEGER,
            status TEXT,
            created_at TIMESTAMP,
            comment TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            last_name TEXT,
            registered_at TIMESTAMP,
            total_orders INTEGER DEFAULT 0
        )
    ''')
    
    # Добавляем товары если их нет
    cursor.execute('SELECT COUNT(*) FROM products')
    if cursor.fetchone()[0] == 0:
        products = [
            ('сосна', 1500, 50, '🌲 Хвойная порода, легкая, смолистая. Идеально для строительства домов, бань и террас.'),
            ('дуб', 4500, 20, '🌳 Твердая лиственная порода, долговечная и красивая. Для мебели, паркета и дверей.'),
            ('береза', 2000, 35, '🌿 Светлая древесина, хорошо гнется. Для фанеры, поделок и декоративных изделий.')
        ]
        cursor.executemany('INSERT INTO products (name, price, stock, description) VALUES (?, ?, ?, ?)', products)
    
    conn.commit()
    conn.close()
    if DEBUG:
        print("✅ База данных инициализирована")

def get_product(name):
    conn = sqlite3.connect('wood_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT name, price, stock, description FROM products WHERE name = ?', (name,))
    result = cursor.fetchone()
    conn.close()
    return result

def get_all_products():
    conn = sqlite3.connect('wood_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT name, price, stock FROM products')
    result = cursor.fetchall()
    conn.close()
    return result

def update_stock(name, quantity):
    conn = sqlite3.connect('wood_bot.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE products SET stock = stock - ? WHERE name = ?', (quantity, name))
    conn.commit()
    conn.close()

def save_order(user_id, user_name, wood_type, quantity, total_price, comment=""):
    conn = sqlite3.connect('wood_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO orders (user_id, user_name, wood_type, quantity, total_price, status, created_at, comment)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, user_name, wood_type, quantity, total_price, 'новый', datetime.now(), comment))
    order_id = cursor.lastrowid
    
    # Обновляем счетчик заказов пользователя
    cursor.execute('UPDATE users SET total_orders = total_orders + 1 WHERE user_id = ?', (user_id,))
    
    conn.commit()
    conn.close()
    return order_id

def save_user(user_id, first_name, last_name):
    conn = sqlite3.connect('wood_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, first_name, last_name, registered_at)
        VALUES (?, ?, ?, ?)
    ''', (user_id, first_name, last_name, datetime.now()))
    conn.commit()
    conn.close()

# ============ AI АССИСТЕНТ (GEMINI 2.5 FLASH) ============
def get_ai_response(user_message, user_context=""):
    """Запрос к AI через AITunnel (openai-формат)"""
    try:
        # Добавляем контекст пользователя если есть
        if user_context:
            full_prompt = f"Контекст пользователя: {user_context}\n\nВопрос: {user_message}"
        else:
            full_prompt = user_message

        response = client.chat.completions.create(
            model=AI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": full_prompt}
            ],
            max_tokens=800,
            temperature=0.7
        )

        answer = response.choices[0].message.content

        if answer:
            # Обрезаем слишком длинные ответы
            if len(answer) > 2000:
                return answer[:2000] + "...\n\n(Ответ сокращен из-за длины)"
            return answer
        else:
            return "❌ Не удалось получить ответ от AI. Попробуйте переформулировать вопрос."

    except Exception as e:
        error_msg = f"❌ Ошибка AI: {str(e)}"
        if DEBUG:
            print(error_msg)
        return "❌ Извините, произошла ошибка при обращении к AI. Попробуйте позже или обратитесь к менеджеру."

# ============ ОТПРАВКА МЕНЕДЖЕРУ ============
def send_to_manager(vk, order_data):
    """Отправка заявки менеджеру в ЛС"""
    if not VK_MANAGER_ID:
        if DEBUG:
            print("⚠️ MANAGER_ID не задан, пропускаем отправку")
        return
    
    msg = f"🆕 *НОВАЯ ЗАЯВКА!*\n\n"
    msg += f"👤 Клиент: {order_data['user_name']}\n"
    msg += f"🆔 ID: {order_data['user_id']}\n"
    msg += f"🌲 Порода: {order_data['wood_type'].capitalize()}\n"
    msg += f"📦 Количество: {order_data['quantity']} куб.м\n"
    msg += f"💰 Сумма: {order_data['total_price']} руб\n"
    msg += f"📝 Комментарий: {order_data.get('comment', 'Нет')}\n"
    msg += f"🕐 Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
    msg += "Для обработки зайдите в админ-панель."
    
    try:
        vk.messages.send(
            user_id=VK_MANAGER_ID,
            message=msg,
            random_id=random.randint(1, 2**31),
            keyboard=get_manager_keyboard()
        )
        if DEBUG:
            print(f"✅ Заявка отправлена менеджеру {VK_MANAGER_ID}")
    except Exception as e:
        print(f"❌ Ошибка отправки менеджеру: {e}")

def get_manager_keyboard():
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button("📊 Статистика", color=VkKeyboardColor.PRIMARY)
    keyboard.add_button("📋 Все заказы", color=VkKeyboardColor.SECONDARY)
    keyboard.add_line()
    keyboard.add_button("✅ Подтвердить заказ", color=VkKeyboardColor.POSITIVE)
    keyboard.add_button("❌ Отменить заказ", color=VkKeyboardColor.NEGATIVE)
    return keyboard.get_keyboard()

# ============ КЛАВИАТУРЫ ============
def get_main_keyboard():
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button("📦 Каталог", color=VkKeyboardColor.PRIMARY)
    keyboard.add_button("🛒 Корзина", color=VkKeyboardColor.SECONDARY)
    keyboard.add_line()
    keyboard.add_button("🤖 Спросить AI", color=VkKeyboardColor.PRIMARY)
    keyboard.add_button("📞 Контакты", color=VkKeyboardColor.NEGATIVE)
    return keyboard.get_keyboard()

def get_catalog_keyboard():
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button("🌲 Сосна", color=VkKeyboardColor.PRIMARY)
    keyboard.add_button("🌳 Дуб", color=VkKeyboardColor.PRIMARY)
    keyboard.add_button("🌿 Береза", color=VkKeyboardColor.PRIMARY)
    keyboard.add_line()
    keyboard.add_button("🔙 Назад", color=VkKeyboardColor.NEGATIVE)
    return keyboard.get_keyboard()

def get_order_keyboard(wood_type):
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button(f"✅ Заказать {wood_type}", color=VkKeyboardColor.POSITIVE)
    keyboard.add_button("🔙 Назад", color=VkKeyboardColor.NEGATIVE)
    return keyboard.get_keyboard()

# ============ ОСНОВНЫЕ ФУНКЦИИ ============
def send_message(vk, user_id, message, keyboard=None):
    params = {
        'user_id': user_id,
        'message': message,
        'random_id': random.randint(1, 2**31)
    }
    if keyboard is not None:
        params['keyboard'] = keyboard
    vk.messages.send(**params)

def handle_catalog(vk, user_id, wood_type=None):
    if wood_type:
        product = get_product(wood_type)
        if product:
            name, price, stock, description = product
            msg = f"🌲 {name.capitalize()}\n"
            msg += f"💰 Цена: {price} руб/куб\n"
            msg += f"📦 В наличии: {stock} куб.м\n"
            msg += f"📝 {description}\n\n"
            msg += "Введите количество в куб.м (например: 5)"
            send_message(vk, user_id, msg, get_order_keyboard(wood_type))
    else:
        products = get_all_products()
        msg = "📦 *Каталог древесины*\n\n"
        for name, price, stock in products:
            msg += f"🌲 {name.capitalize()}\n"
            msg += f"   💰 {price} руб/куб\n"
            msg += f"   📦 {stock} куб.м\n\n"
        send_message(vk, user_id, msg, get_catalog_keyboard())

def handle_order(vk, user_id, user_name, wood_type, quantity, comment=""):
    product = get_product(wood_type)
    if not product:
        send_message(vk, user_id, "❌ Такой породы нет в каталоге")
        return False
    
    name, price, stock, description = product
    
    try:
        quantity = float(quantity)
        if quantity <= 0:
            send_message(vk, user_id, "❌ Количество должно быть больше 0!")
            return False
    except ValueError:
        send_message(vk, user_id, "❌ Введите число! Например: 5")
        return False
    
    if quantity > stock:
        send_message(vk, user_id, 
                    f"❌ Извините, в наличии только {stock} куб.м\n"
                    f"Попробуйте меньшее количество.")
        return False
    
    total = quantity * price
    
    # Сохраняем в БД
    order_id = save_order(user_id, user_name, wood_type, quantity, total, comment)
    update_stock(wood_type, quantity)
    
    # Отправляем клиенту
    msg = f"✅ *Заказ #{order_id} оформлен!*\n\n"
    msg += f"🌲 Порода: {wood_type.capitalize()}\n"
    msg += f"📦 Количество: {quantity} куб.м\n"
    msg += f"💰 Сумма: {total} руб\n"
    msg += f"📝 Комментарий: {comment or 'Нет'}\n\n"
    msg += "С вами свяжется менеджер для подтверждения."
    
    send_message(vk, user_id, msg, get_main_keyboard())
    
    # Отправляем менеджеру
    order_data = {
        'user_id': user_id,
        'user_name': user_name,
        'wood_type': wood_type,
        'quantity': quantity,
        'total_price': total,
        'comment': comment
    }
    send_to_manager(vk, order_data)
    
    return True

def handle_cart(vk, user_id):
    conn = sqlite3.connect('wood_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, wood_type, quantity, total_price, status, created_at 
        FROM orders 
        WHERE user_id = ? AND status != 'отменен'
        ORDER BY created_at DESC
        LIMIT 5
    ''', (user_id,))
    orders = cursor.fetchall()
    conn.close()
    
    if not orders:
        send_message(vk, user_id, "🛒 У вас нет активных заказов", get_main_keyboard())
        return
    
    msg = "🛒 *Ваши последние заказы:*\n\n"
    total_sum = 0
    
    for order in orders:
        order_id, wood, quantity, total, status, created = order
        msg += f"#{order_id} {wood.capitalize()}\n"
        msg += f"   📦 {quantity} куб.м\n"
        msg += f"   💰 {total} руб\n"
        msg += f"   Статус: {status}\n"
        msg += f"   🕐 {created[:16]}\n\n"
        total_sum += total
    
    msg += f"💰 *Итого: {total_sum} руб*"
    
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button("🔙 Назад", color=VkKeyboardColor.NEGATIVE)
    
    send_message(vk, user_id, msg, keyboard.get_keyboard())

def handle_ai_query(vk, user_id, query):
    """Обработка запроса к Gemini 2.5 Flash"""
    send_message(vk, user_id, "🤔 Думаю...")
    
    # Получаем ответ от AI
    response = get_ai_response(query)
    
    # Проверяем, не хочет ли AI оформить заказ
    if "заказ" in response.lower() or "оформить" in response.lower():
        response += "\n\n💡 Для оформления заказа используйте кнопку 'Каталог' в меню."
    
    send_message(vk, user_id, response, get_catalog_keyboard())

# ============ АДМИН-ФУНКЦИИ ============
def handle_manager_commands(vk, user_id, message):
    """Обработка команд от менеджера"""
    if user_id != VK_MANAGER_ID:
        return False
    
    if message == "📊 статистика":
        conn = sqlite3.connect('wood_bot.db')
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM orders')
        total_orders = cursor.fetchone()[0]
        
        cursor.execute('SELECT SUM(total_price) FROM orders WHERE status = "новый"')
        total_sum = cursor.fetchone()[0] or 0
        
        cursor.execute('SELECT COUNT(*) FROM orders WHERE status = "новый"')
        active_orders = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM users')
        total_users = cursor.fetchone()[0]
        
        conn.close()
        
        msg = f"📊 *Статистика компании*\n\n"
        msg += f"👥 Всего клиентов: {total_users}\n"
        msg += f"📋 Всего заказов: {total_orders}\n"
        msg += f"🔄 Активных: {active_orders}\n"
        msg += f"💰 Сумма активных: {total_sum} руб"
        
        send_message(vk, user_id, msg, get_manager_keyboard())
        return True
    
    elif message == "📋 все заказы":
        conn = sqlite3.connect('wood_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, user_name, wood_type, quantity, total_price, status, created_at 
            FROM orders 
            WHERE status = "новый"
            ORDER BY created_at DESC
            LIMIT 10
        ''')
        orders = cursor.fetchall()
        conn.close()
        
        if not orders:
            send_message(vk, user_id, "📋 Новых заказов нет", get_manager_keyboard())
            return True
        
        msg = "📋 *Новые заказы:*\n\n"
        for order in orders:
            order_id, name, wood, quantity, total, status, created = order
            msg += f"#{order_id} | {name}\n"
            msg += f"🌲 {wood.capitalize()} {quantity} куб.м = {total} руб\n"
            msg += f"🕐 {created[:16]}\n\n"
        
        send_message(vk, user_id, msg, get_manager_keyboard())
        return True
    
    elif message.startswith("✅ подтвердить заказ"):
        send_message(vk, user_id, "Введите номер заказа для подтверждения:", get_manager_keyboard())
        return True
    
    return False

# ============ ГЛАВНАЯ ФУНКЦИЯ ============
def main():
    init_db()
    
    vk_session = vk_api.VkApi(token=VK_TOKEN)
    vk = vk_session.get_api()
    longpoll = VkLongPoll(vk_session)
    
    print("🌲 Бот 'Древесина-Про' запущен!")
    print(f"🤖 AI: Google Gemini 2.5 Flash")
    print(f"👤 Менеджер ID: {VK_MANAGER_ID if VK_MANAGER_ID else 'НЕ ЗАДАН!'}")
    print(f"📊 Режим DEBUG: {DEBUG}")
    print("Ожидаю сообщения...\n")
    
    user_state = {}
    
    for event in longpoll.listen():
        if event.type == VkEventType.MESSAGE_NEW and event.to_me:
            user_id = event.user_id
            message = event.text.strip()
            message_lower = message.lower()
            
            if DEBUG:
                print(f"📩 Сообщение от {user_id}: {message}")
            
            # ===== ОБРАБОТКА МЕНЕДЖЕРА =====
            if handle_manager_commands(vk, user_id, message):
                continue
            
            # ===== ПОЛУЧАЕМ ДАННЫЕ ПОЛЬЗОВАТЕЛЯ =====
            try:
                user_info = vk.users.get(user_ids=user_id)[0]
                user_name = f"{user_info['first_name']} {user_info['last_name']}"
                save_user(user_id, user_info['first_name'], user_info['last_name'])
            except:
                user_name = f"User{user_id}"
            
            # ===== ОБРАБОТКА СОСТОЯНИЙ =====
            if user_id in user_state:
                state = user_state[user_id]

                # --- Режим AI: отвечаем на вопросы подряд ---
                if state['state'] == 'waiting_ai':
                    # Выход из режима AI по кнопкам меню
                    if message_lower in ["🔙 назад", "назад", "привет", "старт", "начать", "start"]:
                        del user_state[user_id]
                        send_message(vk, user_id, "🔙 Главное меню:", get_main_keyboard())
                        continue
                    # Иначе — обрабатываем как вопрос к AI и остаёмся в режиме
                    handle_ai_query(vk, user_id, message)
                    continue

                # --- Шаг 1: ждём количество ---
                elif state['state'] == 'waiting_quantity':
                    # сохраняем количество, переходим к комментарию
                    user_state[user_id] = {
                        'state': 'waiting_comment',
                        'wood': state['wood'],
                        'quantity': message
                    }
                    send_message(vk, user_id, "📝 Добавьте комментарий к заказу (или напишите 'нет'):")
                    continue

                # --- Шаг 2: ждём комментарий, оформляем заказ ---
                elif state['state'] == 'waiting_comment':
                    comment = message if message.lower() != 'нет' else ''
                    handle_order(vk, user_id, user_name, state['wood'], state['quantity'], comment)
                    del user_state[user_id]
                    continue
            
            # ===== ГЛАВНОЕ МЕНЮ =====
            if message_lower in ["привет", "старт", "начать", "start", "hi"]:
                send_message(vk, user_id, 
                            "🌲 *Добро пожаловать в лесоторговую компанию 'Древесина-Про'!*\n\n"
                            "🏢 Мы продаем качественную древесину для строительства и производства.\n\n"
                            "📌 Доступные команды:\n"
                            "📦 Каталог - посмотреть ассортимент\n"
                            "🛒 Корзина - ваши заказы\n"
                            "🤖 Спросить AI - задать вопрос ассистенту\n"
                            "📞 Контакты - связаться с нами\n\n"
                            "Выберите действие:",
                            get_main_keyboard())
            
            elif message_lower == "📦 каталог" or message_lower == "каталог":
                handle_catalog(vk, user_id)
            
            elif message_lower == "🌲 сосна":
                handle_catalog(vk, user_id, 'сосна')

            elif message_lower == "🌳 дуб":
                handle_catalog(vk, user_id, 'дуб')

            elif message_lower == "🌿 береза":
                handle_catalog(vk, user_id, 'береза')

            elif message.startswith("✅ заказать"):
                wood_type = message.replace("✅ заказать", "").strip()
                if wood_type in ['сосна', 'дуб', 'береза']:
                    user_state[user_id] = {'state': 'waiting_quantity', 'wood': wood_type}
                    send_message(vk, user_id, "📦 Введите количество в куб.м (например: 5):")
            
            elif message_lower == "🛒 корзина" or message_lower == "корзина":
                handle_cart(vk, user_id)
            
            elif message_lower == "🤖 спросить ai" or message_lower == "ai" or message_lower == "🤖 ai":
                user_state[user_id] = {'state': 'waiting_ai'}
                send_message(vk, user_id,
                            "🤖 Задайте ваш вопрос AI-ассистенту.\n"
                            "Можете спрашивать подряд — я отвечу на каждый.\n"
                            "Чтобы выйти, нажмите «🔙 Назад».",
                            get_catalog_keyboard())
            
            elif message_lower == "📞 контакты" or message_lower == "контакты":
                send_message(vk, user_id,
                            "📞 *Наши контакты:*\n\n"
                            "☎️ Телефон: +7 (999) 123-45-67\n"
                            "📧 Email: wood@company.ru\n"
                            "📍 Адрес: г. Москва, ул. Лесная, 15\n\n"
                            "🕐 Режим работы: Пн-Пт 9:00-18:00\n"
                            "💬 Ответим в течение 15 минут",
                            get_main_keyboard())
            
            elif message_lower == "🔙 назад":
                send_message(vk, user_id, "🔙 Главное меню:", get_main_keyboard())
            
            else:
                # Предлагаем AI для непонятных сообщений
                send_message(vk, user_id,
                            "❌ Не понимаю команду.\n\n"
                            "💡 Используйте кнопки меню или спросите у AI-ассистента (кнопка '🤖 Спросить AI').\n"
                            "📌 Или напишите 'Привет' для начала.",
                            get_main_keyboard())

if __name__ == "__main__":
    main()
