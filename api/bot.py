import os
import json
import logging
from typing import Dict, List, Optional
from functools import wraps

from supabase import create_client, Client
from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler, 
    ContextTypes, 
    MessageHandler,
    filters
)
from awsgi import response

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Получение переменных окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN')
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_ANON_KEY = os.environ.get('SUPABASE_ANON_KEY')
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET')

# Инициализация Supabase клиента
supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# Кэш меню (для уменьшения запросов к БД)
menu_cache = {
    'products': None,
    'last_updated': None
}

# Декоратор для проверки ролей
def role_required(allowed_roles: List[str]):
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user_id = update.effective_user.id
            try:
                user_profile = supabase.table('profiles').select('*').eq('user_id', user_id).execute()
                if not user_profile.data or user_profile.data[0]['role'] not in allowed_roles:
                    if update.callback_query:
                        await update.callback_query.answer('❌ У вас нет доступа к этой команде.')
                    else:
                        await update.message.reply_text('❌ У вас нет доступа к этой команде.')
                    return
                return await func(update, context, *args, **kwargs)
            except Exception as e:
                logger.error(f"Ошибка проверки роли: {e}")
                if update.callback_query:
                    await update.callback_query.answer('❌ Произошла ошибка.')
                else:
                    await update.message.reply_text('❌ Произошла ошибка.')
        return wrapper
    return decorator

# Получение меню с кэшированием
def get_products():
    global menu_cache
    # Кэшируем на 5 минут
    if menu_cache['products'] and menu_cache['last_updated'] and (os.times().elapsed - menu_cache['last_updated'] < 300):
        return menu_cache['products']
    
    try:
        products = supabase.table('products').select('*').eq('is_available', True).execute()
        menu_cache['products'] = products.data
        menu_cache['last_updated'] = os.times().elapsed
        return products.data
    except Exception as e:
        logger.error(f"Ошибка получения продуктов: {e}")
        return []

# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username
    full_name = user.full_name
    
    try:
        # Проверяем или создаем профиль пользователя
        profile = supabase.table('profiles').select('*').eq('user_id', user_id).execute()
        
        if not profile.data:
            supabase.table('profiles').insert({
                'user_id': user_id,
                'username': username,
                'full_name': full_name,
                'role': 'customer'
            }).execute()
        else:
            # Обновляем информацию, если изменилась
            supabase.table('profiles').update({
                'username': username,
                'full_name': full_name
            }).eq('user_id', user_id).execute()
        
        # Получаем актуальный профиль
        profile = supabase.table('profiles').select('*').eq('user_id', user_id).execute().data[0]
        role = profile['role']
        
        # Проверяем активные заказы
        if role == 'customer':
            active_orders = supabase.table('orders').select('*').eq('customer_id', user_id).in_('status', ['pending', 'preparing', 'ready_for_pickup']).execute()
            if active_orders.data:
                order = active_orders.data[0]
                status_text = {
                    'pending': 'ожидает обработки',
                    'preparing': 'готовится',
                    'ready_for_pickup': 'готов к выдаче'
                }.get(order['status'], order['status'])
                
                await update.message.reply_text(
                    f"У вас есть активный заказ №{order['id']}.\n"
                    f"Статус: {status_text}\n"
                    f"Пункт выдачи: {'Левый буфет' if order['pickup_location'] == 'left_buffer' else 'Правый буфет'}\n"
                    f"Сумма: {order['total_amount']}₽\n\n"
                    "Дождитесь выполнения текущего заказа."
                )
                return
        
        # Показываем соответствующее меню
        if role == 'customer':
            await show_customer_menu(update)
        elif role in ['seller_left', 'seller_right']:
            await show_seller_menu(update, role)
            
    except Exception as e:
        logger.error(f"Ошибка в команде /start: {e}")
        await update.message.reply_text('❌ Произошла ошибка при запуске бота.')

async def show_customer_menu(update: Update):
    keyboard = [
        [InlineKeyboardButton("🍿 Сделать заказ", callback_data="make_order")],
        [InlineKeyboardButton("🛒 Корзина", callback_data="view_cart")],
        [InlineKeyboardButton("📋 Мои заказы", callback_data="my_orders")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text("Добро пожаловать! Выберите действие:", reply_markup=reply_markup)
    else:
        await update.callback_query.edit_message_text("Добро пожаловать! Выберите действие:", reply_markup=reply_markup)

async def show_seller_menu(update: Update, role: str):
    buffer = "левого" if role == "seller_left" else "правого"
    keyboard = [
        [InlineKeyboardButton("📥 Новые заказы", callback_data="new_orders")],
        [InlineKeyboardButton("👨‍🍳 В работе", callback_data="preparing_orders")],
        [InlineKeyboardButton("✅ Готовые", callback_data="ready_orders")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(f"Панель управления {buffer} буфета. Выберите раздел:", reply_markup=reply_markup)
    else:
        await update.callback_query.edit_message_text(f"Панель управления {buffer} буфета. Выберите раздел:", reply_markup=reply_markup)

# Главный обработчик кнопок
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    user_id = update.effective_user.id
    
    try:
        profile = supabase.table('profiles').select('*').eq('user_id', user_id).execute()
        if not profile.data:
            await query.edit_message_text("❌ Профиль не найден. Нажмите /start для создания.")
            return
            
        role = profile.data[0]['role']
        
        if data == "make_order":
            await show_categories(update)
        elif data == "view_cart":
            await show_cart(update, user_id)
        elif data == "my_orders":
            await show_my_orders(update, user_id)
        elif data == "back_to_main":
            if role == 'customer':
                await show_customer_menu(update)
            else:
                await show_seller_menu(update, role)
        elif data.startswith("category_"):
            category = data.split("_")[1]
            await show_products(update, category)
        elif data.startswith("add_to_cart_"):
            product_id = int(data.split("_")[3])
            await add_to_cart(update, user_id, product_id)
        elif data.startswith("remove_from_cart_"):
            item_id = int(data.split("_")[3])
            await remove_from_cart(update, user_id, item_id)
        elif data == "checkout":
            await request_pickup_location(update)
        elif data.startswith("confirm_order_"):
            location = data.split("_")[2]
            await confirm_order(update, user_id, location)
        elif data == "new_orders":
            await show_new_orders(update, role)
        elif data.startswith("take_order_"):
            order_id = int(data.split("_")[2])
            await take_order(update, order_id, user_id)
        elif data.startswith("order_ready_"):
            order_id = int(data.split("_")[2])
            await mark_order_ready(update, order_id)
        elif data.startswith("order_completed_"):
            order_id = int(data.split("_")[2])
            await mark_order_completed(update, order_id)
            
    except Exception as e:
        logger.error(f"Ошибка обработки callback: {e}")
        await query.edit_message_text("❌ Произошла ошибка при обработке запроса.")

# Показ категорий товаров
async def show_categories(update: Update):
    products = get_products()
    categories = set([product['category'] for product in products])
    
    keyboard = []
    for category in categories:
        if category == "popcorn":
            text = "🍿 Попкорн"
        elif category == "drinks":
            text = "🥤 Напитки"
        elif category == "cotton_candy":
            text = "🍭 Сладкая вата"
        else:
            text = category
        keyboard.append([InlineKeyboardButton(text, callback_data=f"category_{category}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text("Выберите категорию:", reply_markup=reply_markup)

# Показ товаров в категории
async def show_products(update: Update, category: str):
    products = get_products()
    category_products = [p for p in products if p['category'] == category]
    
    keyboard = []
    for product in category_products:
        button_text = f"{product['name']} - {product['price']}₽"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"add_to_cart_{product['id']}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="make_order")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    category_name = "попкорна" if category == "popcorn" else "напитков" if category == "drinks" else "сладкой ваты"
    await update.callback_query.edit_message_text(f"Выберите {category_name}:", reply_markup=reply_markup)

# Добавление товара в корзину
async def add_to_cart(update: Update, user_id: int, product_id: int):
    try:
        # Находим или создаем корзину
        cart = supabase.table('orders').select('*').eq('customer_id', user_id).eq('status', 'cart').execute()
        
        if not cart.data:
            cart = supabase.table('orders').insert({
                'customer_id': user_id,
                'status': 'cart',
                'total_amount': 0
            }).execute()
            order_id = cart.data[0]['id']
        else:
            order_id = cart.data[0]['id']
        
        # Получаем информацию о товаре
        product = supabase.table('products').select('*').eq('id', product_id).execute().data[0]
        
        # Проверяем, есть ли уже этот товар в корзине
        existing_item = supabase.table('order_items').select('*').eq('order_id', order_id).eq('product_id', product_id).execute()
        
        if existing_item.data:
            # Увеличиваем количество
            new_quantity = existing_item.data[0]['quantity'] + 1
            supabase.table('order_items').update({
                'quantity': new_quantity
            }).eq('id', existing_item.data[0]['id']).execute()
        else:
            # Добавляем новый товар
            supabase.table('order_items').insert({
                'order_id': order_id,
                'product_id': product_id,
                'quantity': 1,
                'price_at_time': product['price']
            }).execute()
        
        # Обновляем общую сумму
        total = calculate_order_total(order_id)
        supabase.table('orders').update({
            'total_amount': total
        }).eq('id', order_id).execute()
        
        await update.callback_query.answer(f"{product['name']} добавлен в корзину!")
        await show_categories(update)
        
    except Exception as e:
        logger.error(f"Ошибка добавления в корзину: {e}")
        await update.callback_query.answer("❌ Ошибка при добавлении в корзину")

# Показ корзины
async def show_cart(update: Update, user_id: int):
    try:
        cart = supabase.table('orders').select('*').eq('customer_id', user_id).eq('status', 'cart').execute()
        
        if not cart.data:
            await update.callback_query.edit_message_text("🛒 Ваша корзина пуста!")
            return
        
        order_id = cart.data[0]['id']
        items = supabase.table('order_items').select('*, products(name)').eq('order_id', order_id).execute()
        
        if not items.data:
            await update.callback_query.edit_message_text("🛒 Ваша корзина пуста!")
            return
        
        message = "🛒 Ваша корзина:\n\n"
        total = 0
        
        keyboard = []
        for item in items.data:
            product_name = item['products']['name']
            quantity = item['quantity']
            price = item['price_at_time']
            item_total = quantity * price
            total += item_total
            message += f"{product_name} x{quantity} - {item_total}₽\n"
            keyboard.append([
                InlineKeyboardButton(f"❌ Удалить {product_name}", callback_data=f"remove_from_cart_{item['id']}")
            ])
        
        message += f"\n💵 Итого: {total}₽"
        
        keyboard.append([InlineKeyboardButton("✅ Оформить заказ", callback_data="checkout")])
        keyboard.append([InlineKeyboardButton("📦 Продолжить покупки", callback_data="make_order")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(message, reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"Ошибка показа корзины: {e}")
        await update.callback_query.edit_message_text("❌ Ошибка при загрузке корзины")

# Удаление товара из корзины
async def remove_from_cart(update: Update, user_id: int, item_id: int):
    try:
        # Удаляем товар из корзины
        supabase.table('order_items').delete().eq('id', item_id).execute()
        
        # Обновляем общую сумму корзины
        cart = supabase.table('orders').select('*').eq('customer_id', user_id).eq('status', 'cart').execute()
        if cart.data:
            order_id = cart.data[0]['id']
            total = calculate_order_total(order_id)
            supabase.table('orders').update({
                'total_amount': total
            }).eq('id', order_id).execute()
        
        await update.callback_query.answer("Товар удален из корзины")
        await show_cart(update, user_id)
        
    except Exception as e:
        logger.error(f"Ошибка удаления из корзины: {e}")
        await update.callback_query.answer("❌ Ошибка при удалении из корзины")

# Запрос места получения заказа
async def request_pickup_location(update: Update):
    keyboard = [
        [InlineKeyboardButton("Левый буфет", callback_data="confirm_order_left")],
        [InlineKeyboardButton("Правый буфет", callback_data="confirm_order_right")],
        [InlineKeyboardButton("🔙 Назад", callback_data="view_cart")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text("Выберите буфет для получения заказа:", reply_markup=reply_markup)

# Подтверждение заказа
async def confirm_order(update: Update, user_id: int, location: str):
    try:
        # Находим корзину
        cart = supabase.table('orders').select('*').eq('customer_id', user_id).eq('status', 'cart').execute()
        
        if not cart.data:
            await update.callback_query.edit_message_text("Ваша корзина пуста!")
            return
        
        order_id = cart.data[0]['id']
        
        # Обновляем заказ
        supabase.table('orders').update({
            'status': 'pending',
            'pickup_location': f"{location}_buffer",
            'updated_at': 'now()'
        }).eq('id', order_id).execute()
        
        # Уведомляем продавцов
        await notify_sellers(order_id, location)
        
        await update.callback_query.edit_message_text(
            "✅ Ваш заказ оформлен! Ожидайте уведомления о готовности.\n\n"
            "Вы можете отслеживать статус заказа в разделе «Мои заказы»."
        )
        
    except Exception as e:
        logger.error(f"Ошибка оформления заказа: {e}")
        await update.callback_query.edit_message_text("❌ Ошибка при оформлении заказа")

# Уведомление продавцов о новом заказе
async def notify_sellers(order_id: int, location: str):
    try:
        # Получаем информацию о заказе
        order = supabase.table('orders').select('*, order_items(*, products(name))').eq('id', order_id).execute().data[0]
        
        # Формируем сообщение для продавца
        message = f"📥 Новый заказ №{order_id} для {'левого' if location == 'left' else 'правого'} буфета.\n"
        message += f"💵 Сумма: {order['total_amount']}₽\n\n"
        message += "Состав заказа:\n"
        
        for item in order['order_items']:
            message += f"• {item['products']['name']} x{item['quantity']}\n"
        
        # Находим продавцов для этого буфета
        seller_role = f"seller_{location}"
        sellers = supabase.table('profiles').select('*').eq('role', seller_role).execute()
        
        # Отправляем уведомление каждому продавцу
        for seller in sellers.data:
            try:
                keyboard = [[InlineKeyboardButton("👨‍🍳 Взять в работу", callback_data=f"take_order_{order_id}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await application.bot.send_message(
                    chat_id=seller['user_id'],
                    text=message,
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление продавцу {seller['user_id']}: {e}")
                
    except Exception as e:
        logger.error(f"Ошибка уведомления продавцов: {e}")

# Показ новых заказов для продавца
@role_required(['seller_left', 'seller_right'])
async def show_new_orders(update: Update, role: str):
    try:
        buffer_location = "left_buffer" if role == "seller_left" else "right_buffer"
        orders = supabase.table('orders').select('*, profiles(full_name)').eq('status', 'pending').eq('pickup_location', buffer_location).execute()
        
        if not orders.data:
            await update.callback_query.edit_message_text("Новых заказов нет.")
            return
        
        keyboard = []
        for order in orders.data:
            button_text = f"Заказ №{order['id']} от {order['profiles']['full_name']} - {order['total_amount']}₽"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"take_order_{order['id']}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text("Новые заказы:", reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"Ошибка показа новых заказов: {e}")
        await update.callback_query.edit_message_text("❌ Ошибка при загрузке заказов")

# Взятие заказа в работу продавцом
@role_required(['seller_left', 'seller_right'])
async def take_order(update: Update, order_id: int, seller_id: int):
    try:
        # Используем транзакцию для избежания конкуренции
        # Получаем заказ с блокировкой
        order = supabase.table('orders').select('*').eq('id', order_id).execute()
        
        if not order.data:
            await update.callback_query.answer("Заказ не найден!")
            return
            
        order = order.data[0]
        
        if order['status'] != 'pending':
            await update.callback_query.answer("Этот заказ уже взят другим продавцом!")
            return
        
        # Обновляем статус заказа
        supabase.table('orders').update({
            'status': 'preparing',
            'updated_at': 'now()'
        }).eq('id', order_id).execute()
        
        # Уведомляем покупателя
        try:
            await application.bot.send_message(
                chat_id=order['customer_id'],
                text=f"Ваш заказ №{order_id} взят в работу!"
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить покупателя {order['customer_id']}: {e}")
        
        # Обновляем сообщение у продавца
        await update.callback_query.edit_message_text(
            text=f"✅ Вы взяли в работу заказ №{order_id}.\nИспользуйте кнопки ниже для управления статусом.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Заказ готов", callback_data=f"order_ready_{order_id}")],
                [InlineKeyboardButton("🔙 Назад", callback_data="new_orders")]
            ])
        )
        
    except Exception as e:
        logger.error(f"Ошибка взятия заказа: {e}")
        await update.callback_query.answer("❌ Произошла ошибка. Попробуйте еще раз.")

# Отметка заказа как готового
@role_required(['seller_left', 'seller_right'])
async def mark_order_ready(update: Update, order_id: int):
    try:
        # Обновляем статус заказа
        supabase.table('orders').update({
            'status': 'ready_for_pickup',
            'updated_at': 'now()'
        }).eq('id', order_id).execute()
        
        # Уведомляем покупателя
        order = supabase.table('orders').select('*').eq('id', order_id).execute().data[0]
        
        try:
            await application.bot.send_message(
                chat_id=order['customer_id'],
                text=f"✅ Ваш заказ №{order_id} готов! Забирайте у {'Левого' if order['pickup_location'] == 'left_buffer' else 'Правого'} буфета."
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить покупателя {order['customer_id']}: {e}")
        
        # Обновляем сообщение у продавца
        await update.callback_query.edit_message_text(
            text=f"✅ Заказ №{order_id} готов к выдаче.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📦 Заказ выдан", callback_data=f"order_completed_{order_id}")],
                [InlineKeyboardButton("🔙 Назад", callback_data="preparing_orders")]
            ])
        )
        
    except Exception as e:
        logger.error(f"Ошибка отметки готовности заказа: {e}")
        await update.callback_query.answer("❌ Произошла ошибка. Попробуйте еще раз.")

# Отметка заказа как завершенного
@role_required(['seller_left', 'seller_right'])
async def mark_order_completed(update: Update, order_id: int):
    try:
        # Обновляем статус заказа
        supabase.table('orders').update({
            'status': 'completed',
            'updated_at': 'now()'
        }).eq('id', order_id).execute()
        
        # Обновляем сообщение у продавца
        await update.callback_query.edit_message_text(
            text=f"✅ Заказ №{order_id} завершен.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="ready_orders")]
            ])
        )
        
    except Exception as e:
        logger.error(f"Ошибка завершения заказа: {e}")
        await update.callback_query.answer("❌ Произошла ошибка. Попробуйте еще раз.")

# Показ истории заказов пользователя
async def show_my_orders(update: Update, user_id: int):
    try:
        orders = supabase.table('orders').select('*').eq('customer_id', user_id).order('created_at', desc=True).execute()
        
        if not orders.data:
            await update.callback_query.edit_message_text("У вас еще нет заказов.")
            return
        
        message = "📋 История ваших заказов:\n\n"
        
        for order in orders.data:
            status_text = {
                'cart': 'Корзина',
                'pending': 'Ожидает обработки',
                'preparing': 'Готовится',
                'ready_for_pickup': 'Готов к выдаче',
                'completed': 'Завершен',
                'cancelled': 'Отменен'
            }.get(order['status'], order['status'])
            
            message += f"Заказ №{order['id']} - {status_text}\n"
            message += f"Сумма: {order['total_amount']}₽\n"
            message += f"Дата: {order['created_at'][:10]}\n\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(message, reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"Ошибка показа истории заказов: {e}")
        await update.callback_query.edit_message_text("❌ Ошибка при загрузке истории заказов")

# Функция для расчета общей суммы заказа
def calculate_order_total(order_id: int) -> float:
    try:
        items = supabase.table('order_items').select('*').eq('order_id', order_id).execute()
        total = 0
        
        for item in items.data:
            total += item['quantity'] * item['price_at_time']
        
        return total
    except Exception as e:
        logger.error(f"Ошибка расчета суммы заказа: {e}")
        return 0

# Обработчик вебхука для Vercel
async def webhook_handler(event, context):
    if event.get('httpMethod') == 'POST':
        # Проверяем секрет вебхука, если задан
        if WEBHOOK_SECRET:
            token = event.get('headers', {}).get('X-Telegram-Bot-Api-Secret-Token')
            if token != WEBHOOK_SECRET:
                return {'statusCode': 403, 'body': 'Forbidden'}
        
        # Обрабатываем обновление
        try:
            update = Update.de_json(json.loads(event['body']), application.bot)
            await application.process_update(update)
        except Exception as e:
            logger.error(f"Ошибка обработки обновления: {e}")
            return {'statusCode': 500, 'body': 'Error'}
        
        return {'statusCode': 200, 'body': 'OK'}
    
    return {'statusCode': 404, 'body': 'Not Found'}

# Инициализация приложения
application = Application.builder().token(BOT_TOKEN).build()

# Добавляем обработчики
application.add_handler(CommandHandler('start', start))
application.add_handler(CallbackQueryHandler(button_handler))

# Lambda handler для Vercel
def vercel_handler(event, context):
    return response(webhook_handler, event, context)
