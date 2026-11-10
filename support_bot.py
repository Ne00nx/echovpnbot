import asyncio
import logging
import os
import html
import json
import uuid
import hashlib
import time
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web, ClientSession

# --- FIREBASE IMPORTS ---
import firebase_admin
from firebase_admin import credentials, messaging
# ------------------------

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '621860210'))
APK_URL = 'https://disk.yandex.ru/d/II9QBaio8oE8jQ' 

SUPABASE_URL = (os.getenv('SUPABASE_URL') or '').rstrip('/')
SUPABASE_SERVICE_KEY = os.getenv('SUPABASE_SERVICE_KEY')

AMYPAY_API_KEY = os.getenv('AMYPAY_API_KEY', '')
AMYPAY_SECRET_WORD_2 = os.getenv('AMYPAY_SECRET_WORD_2', '')

IS_TEST_MODE = os.getenv('IS_TEST_MODE', 'false').lower() == 'true'
PUBLIC_URL = os.getenv('RENDER_EXTERNAL_URL', 'http://localhost:8080').rstrip('/')

logging.basicConfig(level=logging.INFO)
router = Router()
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
dp.include_router(router)

# --- ИНИЦИАЛИЗАЦИЯ FIREBASE ---
try:
    cred = credentials.Certificate('/etc/secrets/firebase-service-account.json')
    firebase_admin.initialize_app(cred)
    logging.info("Firebase Admin успешно инициализирован.")
except Exception as e:
    logging.warning(f"Не удалось инициализировать Firebase Admin: {e}. Пуши работать не будут.")
# ------------------------------

class UserState(StatesGroup):
    waiting_for_email_buy = State()
    confirming_email_buy = State()
    waiting_for_email_status = State()

user_temp_data = {}
admin_reply_map = {}
user_last_message_time = {}

# --- ХЕЛПЕРЫ ---

async def get_user_from_token(request):
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return None, None
    token = auth_header.split(' ')[1]
    
    headers_sb = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {token}"}
    
    try:
        async with ClientSession() as session:
            async with session.get(f"{SUPABASE_URL}/auth/v1/user", headers=headers_sb, timeout=5) as resp:
                if resp.status == 200:
                    user_data = await resp.json()
                    return user_data.get('id'), user_data.get('email')
                else:
                    logging.error(f"Ошибка проверки JWT: {resp.status} - {await resp.text()}")
    except Exception as e:
        logging.error(f"Ошибка валидации JWT: {e}")
    return None, None

async def get_plan_from_db(plan_id):
    headers_sb = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    try:
        async with ClientSession() as session:
            async with session.get(f"{SUPABASE_URL}/rest/v1/plans?id=eq.{plan_id}&is_active=eq.true&select=*", headers=headers_sb, timeout=5) as resp:
                data = await resp.json()
                if data and isinstance(data, list) and len(data) > 0:
                    return data[0]
                elif not isinstance(data, list):
                    logging.error(f"Ошибка получения тарифа: {data}")
    except Exception as e:
        logging.error(f"Ошибка получения тарифа: {e}")
    return None

async def check_amypay_token():
    if not AMYPAY_API_KEY or IS_TEST_MODE: return
    url = "https://amypay.ru/api/v2/ping"
    headers = {"Authorization": f"Bearer {AMYPAY_API_KEY}"}
    try:
        async with ClientSession() as session:
            async with session.get(url, headers=headers, timeout=5) as resp:
                if resp.status == 200:
                    logging.info("Amypay API ключ валиден!")
                else:
                    logging.error(f"Amypay API ключ НЕВАЛИДЕН! Статус: {resp.status}")
    except Exception as e:
        logging.error(f"Ошибка проверки ключа Amypay: {e}")

async def send_push_notification(fcm_token, title, body):
    if not fcm_token: return
    try:
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            token=fcm_token,
        )
        await asyncio.to_thread(messaging.send, message)
        logging.info(f"Пуш успешно отправлен на токен {fcm_token[:10]}...")
    except Exception as e:
        logging.error(f"Ошибка отправки пуша: {e}")

def get_user_menu():
    keyboard = [
        [InlineKeyboardButton(text="🛒 Купить подписку", callback_data="buy_menu")],
        [InlineKeyboardButton(text="👤 Моя подписка", callback_data="check_status")],
        [InlineKeyboardButton(text="📥 Скачать приложение", url=APK_URL)],
        [InlineKeyboardButton(text="💬 Помощь", callback_data="help_mode")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# --- ОБРАБОТЧИКИ БОТА ---

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        f"👋 <b>Добро пожаловать в EchoVPN!</b>\n\n"
        f"Здесь вы можете управлять своей подпиской и покупать доступ.\n\n"
        f"Выберите действие ниже:",
        reply_markup=get_user_menu()
    )

@router.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        f"👋 <b>Главное меню EchoVPN</b>\n\nВыберите действие:",
        reply_markup=get_user_menu()
    )

@router.callback_query(F.data == "buy_menu")
async def ask_email_for_buy(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserState.waiting_for_email_buy)
    await callback.message.edit_text(
        "📧 <b>Введите ваш Email</b>, указанный при регистрации в приложении.\n\n"
        "Если вы еще не зарегистрированы, введите email, который планируете использовать.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Отмена", callback_data="main_menu")]])
    )

@router.message(UserState.waiting_for_email_buy)
async def process_email_for_buy(message: Message, state: FSMContext):
    email = message.text.strip().lower()
    user_temp_data[message.from_user.id] = {"email": email}
    await state.set_state(UserState.confirming_email_buy)
    await message.answer(
        f"📧 Вы ввели: <b>{email}</b>\n\nВсе верно?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, все верно", callback_data="confirm_email_yes")],
            [InlineKeyboardButton(text="❌ Нет, ввести заново", callback_data="confirm_email_no")]
        ])
    )

@router.callback_query(F.data == "confirm_email_no")
async def confirm_email_no(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserState.waiting_for_email_buy)
    await callback.message.edit_text(
        "📧 <b>Введите ваш Email</b> заново:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Отмена", callback_data="main_menu")]])
    )

@router.callback_query(F.data == "confirm_email_yes")
async def confirm_email_yes(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    email = user_temp_data.get(callback.from_user.id, {}).get("email")
    if not email:
        await callback.message.edit_text("❌ Ошибка сессии. Начните заново.", reply_markup=get_user_menu())
        return

    headers_sb = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    async with ClientSession() as session:
        async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?email=eq.{email}&select=id", headers=headers_sb) as resp:
            data = await resp.json()
            
    if data and isinstance(data, list):
        user_temp_data[callback.from_user.id]["target_id"] = data[0]['id']
    else:
        user_temp_data[callback.from_user.id]["target_id"] = f"waiting_{email}"
        
    await show_plans(callback.message)

async def show_plans(message: Message):
    headers_sb = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    async with ClientSession() as session:
        async with session.get(f"{SUPABASE_URL}/rest/v1/plans?is_active=eq.true&order=duration_days.asc", headers=headers_sb) as resp:
            plans = await resp.json()

    if not plans or not isinstance(plans, list):
        await message.answer("❌ Тарифы временно недоступны.", reply_markup=get_user_menu())
        return

    keyboard = []
    for plan in plans:
        keyboard.append([InlineKeyboardButton(
            text=f"{plan['name']} — {plan['price_rub']} ₽", 
            callback_data=f"pay_{plan['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")])
    await message.answer("💳 <b>Выберите тариф для покупки:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))

@router.callback_query(F.data.startswith("pay_"))
async def process_payment(callback: CallbackQuery):
    plan_id = int(callback.data.split("_")[1])
    target_id = user_temp_data.get(callback.from_user.id, {}).get("target_id")
    
    if not target_id:
        await callback.message.edit_text("❌ Сессия истекла. Пожалуйста, начните заново.", reply_markup=get_user_menu())
        return

    plan = await get_plan_from_db(plan_id)
    if not plan:
        await callback.message.edit_text("❌ Ошибка тарифа. Попробуйте позже.", reply_markup=get_user_menu())
        return

    days = plan['duration_days']
    amount = plan['price_rub']
    is_family = 'Семейная' in plan['name']
    order_id = f"{target_id}|{days}|{int(datetime.now().timestamp())}|{callback.from_user.id}|{str(is_family).lower()}|sub"
    
    pay_url = ""
    if IS_TEST_MODE:
        pay_url = f"{PUBLIC_URL}/api/fake-pay?order_id={quote(order_id)}&amount={amount}"
    else:
        api_url = "https://amypay.ru/api/v2/orders"
        headers = {"Authorization": f"Bearer {AMYPAY_API_KEY}", "Content-Type": "application/json"}
        payload = {"amount": int(amount), "external_order_id": order_id, "description": f"EchoVPN Subscription ({days} days)"}
        async with ClientSession() as session:
            async with session.post(api_url, json=payload, headers=headers, timeout=10) as resp:
                resp_data = await resp.json()
                if resp.status in [200, 201] and resp_data.get('ok'):
                    pay_url = resp_data.get('order', {}).get('pay_url')

    if pay_url:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Перейти к оплате", url=pay_url)],
            [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
        ])
        await callback.message.edit_text(f"✅ Заказ на <b>{days} дней</b> (Сумма: {amount} ₽) создан!\n\nНажмите кнопку ниже, чтобы оплатить.", reply_markup=keyboard)
    else:
        await callback.message.edit_text("❌ Ошибка создания платежа. Попробуйте позже.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]]))

@router.callback_query(F.data == "check_status")
async def ask_email_status(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserState.waiting_for_email_status)
    await callback.message.edit_text(
        "📧 <b>Введите ваш Email</b>, который вы указывали при регистрации в приложении.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Отмена", callback_data="main_menu")]])
    )

@router.message(UserState.waiting_for_email_status)
async def process_email_status(message: Message, state: FSMContext):
    email = message.text.strip().lower()
    await state.clear()
    
    headers_sb = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    async with ClientSession() as session:
        async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?email=eq.{email}&select=subscription_plan,subscription_expires_at,family_owner_id", headers=headers_sb) as resp:
            data = await resp.json()
            
    if not data or not isinstance(data, list):
        await message.answer("❌ Пользователь с таким email не найден.", reply_markup=get_user_menu())
        return
        
    profile = data[0]
    expires_at_str = profile.get('subscription_expires_at')
    plan = profile.get('subscription_plan')
    
    if profile.get('family_owner_id'):
        async with ClientSession() as session:
            async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{profile['family_owner_id']}&select=subscription_expires_at", headers=headers_sb) as resp:
                owner_data = await resp.json()
                if owner_data and isinstance(owner_data, list) and owner_data[0].get('subscription_expires_at'):
                    expires_at_str = owner_data[0]['subscription_expires_at']
                    plan = 'premium'

    if plan == 'premium' and expires_at_str:
        expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        if expires_at > now:
            days_left = (expires_at - now).days
            await message.answer(
                f"✅ <b>Подписка активна!</b>\n\n"
                f"📅 Осталось дней: <b>{days_left}</b>\n"
                f"⏳ Действует до: <b>{expires_at.strftime('%d.%m.%Y')}</b>",
                reply_markup=get_user_menu()
            )
            return
            
    await message.answer("❌ <b>Подписка не активна.</b>\n\nВы можете приобрести её в главном меню.", reply_markup=get_user_menu())

@router.callback_query(F.data == "help_mode")
async def help_mode(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "💬 <b>Режим поддержки</b>\n\n"
        "Опишите вашу проблему одним сообщением (можно прикрепить фото). Мы ответим вам в ближайшее время.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]])
    )

@router.message(Command("give_days"), F.from_user.id == ADMIN_ID)
async def cmd_give_days(message: Message):
    args = message.text.split()
    if len(args) != 3:
        await message.answer("⚠️ Использование: <code>/give_days email@example.com 30</code>")
        return

    email = args[1]
    try:
        days = int(args[2])
    except ValueError:
        await message.answer("⚠️ Количество дней должно быть числом.")
        return

    headers_sb = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    async with ClientSession() as session:
        async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?email=eq.{email}&select=id", headers=headers_sb) as resp:
            data = await resp.json()
            if not data or not isinstance(data, list):
                await message.answer("❌ Пользователь с таким email не найден.")
                return
            user_id = data[0]['id']

    await process_successful_payment(user_id, days)
    await message.answer(f"✅ Подписка на {days} дней успешно выдана для {email}!")

@router.message(Command("reset_device"), F.from_user.id == ADMIN_ID)
async def cmd_reset_device(message: Message):
    args = message.text.split()
    if len(args) != 2:
        await message.answer("⚠️ Использование: <code>/reset_device email@example.com</code>")
        return

    email = args[1].strip().lower()
    headers_sb = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}

    async with ClientSession() as session:
        async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?email=eq.{email}&select=id", headers=headers_sb) as resp:
            data = await resp.json()
            if not data or not isinstance(data, list):
                await message.answer("❌ Пользователь не найден.")
                return
            user_id = data[0]['id']

        async with session.patch(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}", json={"active_devices": []}, headers={**headers_sb, "Content-Type": "application/json", "Prefer": "return=minimal"}) as resp:
            pass

    await message.answer(f"✅ Все устройства для {email} успешно отвязаны.")

@router.message(F.chat.id != ADMIN_ID)
async def forward_to_admin(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None: return

    user = message.from_user
    now = time.time()
    last_time = user_last_message_time.get(user.id, 0)
    if now - last_time < 30:
        await message.answer("⚠️ Пожалуйста, не спамьте. Мы уже получили ваше сообщение и скоро ответим.")
        return
    user_last_message_time[user.id] = now

    safe_name = html.escape(user.full_name)
    admin_text_header = (
        f"<b>📩 Новое сообщение в поддержку</b>\n\n"
        f"👤 От: {safe_name}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"🔗 Username: @{user.username or 'нет'}\n\n"
    )
    close_keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Закрыть обращение", callback_data=f"close_admin_{user.id}")]])
    
    try:
        sent_msg = None
        if message.text:
            sent_msg = await bot.send_message(chat_id=ADMIN_ID, text=admin_text_header + f"💬 Текст:\n{html.escape(message.text)}", reply_markup=close_keyboard)
        elif message.photo:
            caption = html.escape(message.caption) if message.caption else "Без подписи"
            sent_msg = await bot.send_photo(chat_id=ADMIN_ID, photo=message.photo[-1].file_id, caption=admin_text_header + f"🖼 Фото:\n{caption}", reply_markup=close_keyboard)
        elif message.document:
            caption = html.escape(message.caption) if message.caption else "Без подписи"
            sent_msg = await bot.send_document(chat_id=ADMIN_ID, document=message.document.file_id, caption=admin_text_header + f"📄 Документ:\n{caption}", reply_markup=close_keyboard)
        elif message.video:
            sent_msg = await bot.send_video(chat_id=ADMIN_ID, video=message.video.file_id, caption=admin_text_header + "🎥 Видео", reply_markup=close_keyboard)
        
        if sent_msg: admin_reply_map[sent_msg.message_id] = user.id
        await message.answer("✅ Ваше сообщение передано в поддержку. Ожидайте ответа.")
    except Exception as e:
        logging.error(f"Ошибка отправки админу: {e}")
        await message.answer("⚠️ В данный момент поддержка недоступна. Попробуйте позже.")

@router.message(F.chat.id == ADMIN_ID, F.reply_to_message)
async def reply_to_user(message: Message):
    reply_to_msg_id = message.reply_to_message.message_id
    target_user_id = admin_reply_map.get(reply_to_msg_id)

    if not target_user_id:
        await message.answer("❌ Не могу определить, кому отправить этот ответ.")
        return

    user_close_keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Обращение закрыто", callback_data=f"close_user_{target_user_id}")]])

    try:
        if message.text:
            await bot.send_message(chat_id=target_user_id, text=f"<b>📬 Ответ поддержки EchoVPN:</b>\n\n{html.escape(message.text)}", reply_markup=user_close_keyboard)
            await message.answer("✅ Ответ отправлен пользователю.")
    except Exception as e:
        await message.answer(f"❌ Ошибка при отправке ответа: {e}")

@router.callback_query(F.data.startswith("close_admin_"))
async def close_ticket_admin(callback: CallbackQuery):
    target_user_id = int(callback.data.split("_")[2])
    for msg_id, u_id in list(admin_reply_map.items()):
        if u_id == target_user_id: del admin_reply_map[msg_id]
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Обращение закрыто.")
    try: await bot.send_message(chat_id=target_user_id, text="🔒 <b>Ваше обращение в поддержку было закрыто.</b>")
    except: pass

@router.callback_query(F.data.startswith("close_user_"))
async def close_ticket_user(callback: CallbackQuery):
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Спасибо! Обращение закрыто.")
    try: await bot.send_message(chat_id=ADMIN_ID, text=f"✅ Пользователь @{callback.from_user.username or callback.from_user.id} закрыл обращение.")
    except: pass


# --- БИЗНЕС-ЛОГИКА И API ---

async def process_successful_payment(user_id, days, is_bonus=False, is_family_plan=False, family_owner_expires=None):
    logging.info(f"Активация подписки для {user_id} на {days} дней (is_bonus={is_bonus}, family={is_family_plan})")
    headers_sb = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    
    try:
        async with ClientSession() as session:
            async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=email,subscription_expires_at,referred_by,referral_bonus_paid,family_code,fcm_token", headers=headers_sb) as resp:
                profile_data = await resp.json()
                
                # ЗАЩИТА ОТ ОШИБОК СУПАБЕЙС
                if not isinstance(profile_data, list):
                    logging.error(f"Ошибка Supabase при получении профиля: {profile_data}")
                    return
                if not profile_data:
                    logging.error(f"Профиль {user_id} не найден")
                    return
                
                p = profile_data[0]
                current_expires_str = p.get('subscription_expires_at')
                referred_by = p.get('referred_by')
                bonus_paid = p.get('referral_bonus_paid', False)
                current_family_code = p.get('family_code')
                user_fcm_token = p.get('fcm_token')
            
            client_name = f"u_{user_id[:8]}"
            now = datetime.now(timezone.utc)
            
            if family_owner_expires:
                new_expires = family_owner_expires
            else:
                base_date = datetime.fromisoformat(current_expires_str.replace('Z', '+00:00')) if current_expires_str else now
                new_expires = (base_date if base_date > now else now) + timedelta(days=days)
            
            new_expires_iso = new_expires.isoformat()
            total_days_for_panel = max(1, (new_expires - now).days)
            client_uuid = str(uuid.uuid4())

            async with session.get(f"{SUPABASE_URL}/rest/v1/servers?select=*", headers=headers_sb) as resp:
                servers = await resp.json()
                
            if not isinstance(servers, list):
                logging.error(f"Ошибка получения серверов: {servers}")
                return

            generated_urls = {}

            async def create_on_server(server):
                headers_xui = {"Content-Type": "application/json", "Authorization": f"Bearer {server['api_token']}", "X-API-Key": server['api_token']}
                base_url = server['api_url'].replace('/create', '')
                
                try:
                    encoded_name = quote(client_name, safe='')
                    async with session.delete(f"{base_url}/clients/{encoded_name}", headers=headers_xui, timeout=5): pass 
                except: pass

                payload = {"name": client_name, "uuid": client_uuid, "days": total_days_for_panel, "inbound_ids": [server['inbound_id']]}
                try:
                    async with session.post(server['api_url'], json=payload, headers=headers_xui, timeout=5) as resp:
                        if resp.status in [200, 201]:
                            domain, pbk, sid = server['domain'], server['pbk'], server['sid']
                            flow_str = "&flow=xtls-rprx-vision" if server.get('country_code') != 'ES' else ""
                            url = f"vless://{client_uuid}@{domain}:443?type=tcp&security=reality&sni={domain}&fp=firefox&pbk={pbk}&sid={sid}&spx=%2F&encryption=none{flow_str}#EchoVPN-{server['name']}"
                            return server['id'], url.replace(' ', '').replace('\n', '')
                except Exception as e:
                    logging.error(f"Ошибка создания на сервере {server.get('name')}: {e}")
                return None

            results = await asyncio.gather(*[create_on_server(s) for s in servers])
            for res in results:
                if res: generated_urls[res[0]] = res[1]

            if generated_urls:
                update_data = {"vless_urls": generated_urls, "subscription_plan": "premium", "subscription_expires_at": new_expires_iso}
                
                if is_family_plan and not current_family_code:
                    update_data["family_code"] = hashlib.md5(f"{user_id}{datetime.now().timestamp()}".encode()).hexdigest()[:8]

                if not is_bonus and referred_by and not bonus_paid:
                    update_data["referral_bonus_paid"] = True
                    asyncio.create_task(process_successful_payment(referred_by, 7, is_bonus=True))

                async with session.patch(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}", json=update_data, headers={**headers_sb, "Content-Type": "application/json"}) as r: pass

                if not is_bonus and user_fcm_token:
                    await send_push_notification(user_fcm_token, "✅ Подписка активирована!", "Ваш VPN готов к использованию. Приятного серфинга!")

                if is_family_plan or current_family_code:
                    async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?family_owner_id=eq.{user_id}&select=id", headers=headers_sb) as resp:
                        members = await resp.json()
                        if isinstance(members, list):
                            for m in members:
                                await session.patch(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{m['id']}", json={"subscription_expires_at": new_expires_iso}, headers={**headers_sb, "Content-Type": "application/json"})
            else:
                logging.error(f"Не удалось сгенерировать ключи ни на одном сервере для {user_id}")
                
    except Exception as e:
        logging.error(f"Критическая ошибка в process_successful_payment: {e}", exc_info=True)

async def handle_create_amypay_payment(request):
    try:
        real_user_id, _ = await get_user_from_token(request)
        if not real_user_id: return web.Response(text=json.dumps({"error": "Unauthorized"}), status=401, content_type='application/json')

        headers_sb = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        async with ClientSession() as session:
            async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{real_user_id}&select=family_owner_id", headers=headers_sb) as resp:
                data = await resp.json()
                if isinstance(data, list) and len(data) > 0 and data[0].get('family_owner_id'):
                    return web.Response(text=json.dumps({"error": "Вы состоите в семейной подписке. Выйдите из семьи, чтобы купить личную."}), status=403, content_type='application/json')

        data = await request.json()
        plan_id = data.get('planId') 
        promo_code = data.get('promoCode', '').strip().upper()

        if not plan_id: return web.Response(text=json.dumps({"error": "No planId"}), status=400, content_type='application/json')

        plan = await get_plan_from_db(plan_id)
        if not plan: return web.Response(text=json.dumps({"error": "Invalid plan"}), status=400, content_type='application/json')

        days = plan['duration_days']
        amount = float(plan['price_rub'])
        is_family = 'Семейная' in plan['name']

        if promo_code:
            async with ClientSession() as session:
                async with session.get(f"{SUPABASE_URL}/rest/v1/promocodes?code=eq.{promo_code}&is_active=eq.true", headers=headers_sb) as resp:
                    promo_data = await resp.json()
                    if isinstance(promo_data, list) and len(promo_data) > 0:
                        amount = amount - (amount * promo_data[0]['discount_percent'] / 100)

        order_id = f"{real_user_id}|{days}|{int(datetime.now().timestamp())}|0|{str(is_family).lower()}|sub"
        
        if IS_TEST_MODE:
            fake_url = f"{PUBLIC_URL}/api/fake-pay?order_id={quote(order_id)}&amount={int(amount)}"
            return web.Response(text=json.dumps({"payment_url": fake_url}), status=200, content_type='application/json')

        api_url = "https://amypay.ru/api/v2/orders"
        headers = {"Authorization": f"Bearer {AMYPAY_API_KEY}", "Content-Type": "application/json"}
        payload = {"amount": int(amount), "external_order_id": order_id, "description": f"EchoVPN Subscription ({days} days)"}
        
        async with ClientSession() as session:
            async with session.post(api_url, json=payload, headers=headers, timeout=10) as resp:
                resp_data = await resp.json()
                if resp.status in [200, 201] and resp_data.get('ok'):
                    return web.Response(text=json.dumps({"payment_url": resp_data.get('order', {}).get('pay_url')}), status=200, content_type='application/json')
                return web.Response(text=json.dumps({"error": "Failed to create order"}), status=500, content_type='application/json')
                
    except Exception as e:
        logging.error(f"Error create payment: {e}")
        return web.Response(text=json.dumps({"error": str(e)}), status=500, content_type='application/json')

async def handle_buy_device_slot(request):
    try:
        real_user_id, _ = await get_user_from_token(request)
        if not real_user_id: return web.Response(text=json.dumps({"error": "Unauthorized"}), status=401, content_type='application/json')
            
        amount = 99 
        order_id = f"{real_user_id}|0|{int(datetime.now().timestamp())}|0|false|device" 
        
        if IS_TEST_MODE:
            fake_url = f"{PUBLIC_URL}/api/fake-pay?order_id={quote(order_id)}&amount={amount}"
            return web.Response(text=json.dumps({"payment_url": fake_url}), status=200, content_type='application/json')

        api_url = "https://amypay.ru/api/v2/orders"
        headers = {"Authorization": f"Bearer {AMYPAY_API_KEY}", "Content-Type": "application/json"}
        payload = {"amount": amount, "external_order_id": order_id, "description": "EchoVPN Extra Device Slot"}
        
        async with ClientSession() as session:
            async with session.post(api_url, json=payload, headers=headers, timeout=10) as resp:
                resp_data = await resp.json()
                if resp.status in [200, 201] and resp_data.get('ok'):
                    return web.Response(text=json.dumps({"payment_url": resp_data.get('order', {}).get('pay_url')}), status=200, content_type='application/json')
        return web.Response(text=json.dumps({"error": "Failed"}), status=500, content_type='application/json')
    except Exception as e:
        return web.Response(text=json.dumps({"error": str(e)}), status=500, content_type='application/json')

async def handle_fake_pay(request):
    order_id = request.query.get('order_id', '')
    amount = request.query.get('amount', '0')
    
    sig = hashlib.sha256(f"{order_id}:paid:{amount}:{AMYPAY_SECRET_WORD_2}".encode()).hexdigest()
    
    page = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>EchoVPN Test Pay</title></head>
    <body style="background:#0F172A;color:white;font-family:sans-serif;text-align:center;padding:60px 20px">
      <h1>🧪 ТЕСТОВАЯ ОПЛАТА (STAGE)</h1>
      <p style="color:#94a3b8">Это симуляция успешной оплаты. В продакшене будет Amypay.</p>
      <p>Order ID: <code style="background:#1E293B;padding:4px 8px;border-radius:4px">{order_id}</code></p>
      <p>Сумма: <b>{amount} ₽</b></p>
      <form method="POST" action="/api/amypay/webhook" style="margin-top:40px">
        <input type="hidden" name="external_order_id" value="{order_id}">
        <input type="hidden" name="number" value="{order_id}">
        <input type="hidden" name="status" value="paid">
        <input type="hidden" name="amount" value="{amount}">
        <input type="hidden" name="signature" value="{sig}">
        <button type="submit" style="padding:20px 40px;font-size:18px;background:#06B6D4;color:white;border:none;border-radius:10px;cursor:pointer;font-weight:bold">
          ✅ Симулировать успешную оплату
        </button>
      </form></body></html>"""
    return web.Response(text=page, content_type='text/html')

async def handle_amypay_webhook(request):
    try:
        if request.content_type == 'application/x-www-form-urlencoded':
            data = await request.post()
        else:
            data = await request.json()
            
        status = data.get('status')
        if status and status != 'paid':
            return web.Response(text="OK", status=200)

        number = data.get('number')
        amount = data.get('amount')
        signature = data.get('signature')
        
        if AMYPAY_SECRET_WORD_2 and signature:
            expected_sig = hashlib.sha256(f"{number}:{status}:{amount}:{AMYPAY_SECRET_WORD_2}".encode()).hexdigest()
            if signature != expected_sig:
                logging.warning(f"Webhook: Неверная подпись! Data: {data}")
                return web.Response(text="Bad signature", status=403)
        
        external_order_id = data.get('external_order_id')
        if external_order_id:
            parts = external_order_id.split('|')
            if len(parts) >= 2:
                target_id = parts[0]
                days = int(parts[1])
                chat_id = int(parts[3]) if len(parts) > 3 and parts[3] != '0' else None
                is_family = parts[4].lower() == 'true' if len(parts) > 4 else False
                action_type = parts[5] if len(parts) > 5 else "sub"
                
                headers_sb = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}", "Prefer": "return=representation,resolution=ignore-duplicates"}
                async with ClientSession() as session:
                    async with session.post(f"{SUPABASE_URL}/rest/v1/payments", json={
                        "external_order_id": external_order_id, 
                        "user_id": target_id if not target_id.startswith("waiting_") else None,
                        "amount_rub": int(float(amount)), 
                        "days": days, 
                        "action_type": action_type, 
                        "status": "paid"
                    }, headers={**headers_sb, "Content-Type": "application/json"}) as resp:
                        inserted_rows = await resp.json()
                        if isinstance(inserted_rows, list) and not inserted_rows:
                            logging.info(f"Webhook: Заказ {external_order_id} уже обработан. Пропускаем.")
                            return web.Response(text="OK", status=200)

                asyncio.create_task(process_webhook_background(target_id, days, chat_id, is_family, action_type))
                return web.Response(text="OK", status=200)
        return web.Response(text="OK", status=200)

    except Exception as e:
        logging.error(f"Ошибка обработки вебхука: {e}", exc_info=True)
        return web.Response(text="Error", status=500)

async def process_webhook_background(target_id, days, chat_id, is_family, action_type="sub"):
    try:
        if target_id.startswith("waiting_"):
            email = target_id.replace("waiting_", "")
            headers_sb = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}", "Content-Type": "application/json"}
            async with ClientSession() as session:
                await session.post(f"{SUPABASE_URL}/rest/v1/pending_payments", json={"email": email, "days": days}, headers=headers_sb)
            if chat_id: await bot.send_message(chat_id=chat_id, text="✅ <b>Оплата получена!</b>\n\nНачислим при регистрации.")
        else:
            if action_type == "device":
                headers_sb = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
                async with ClientSession() as session:
                    async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{target_id}&select=max_devices_allowed,fcm_token", headers=headers_sb) as resp:
                        p_data = await resp.json()
                        if not isinstance(p_data, list) or not p_data:
                            logging.error(f"Не удалось получить профиль для слота: {p_data}")
                            return
                        p = p_data[0]
                    new_max = min(p.get('max_devices_allowed', 2) + 1, 5)
                    await session.patch(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{target_id}", json={"max_devices_allowed": new_max}, headers={**headers_sb, "Content-Type": "application/json"})
                if p.get('fcm_token'): await send_push_notification(p['fcm_token'], "✅ Слот добавлен!", "Теперь вы можете войти еще на одном устройстве.")
                if chat_id: await bot.send_message(chat_id=chat_id, text="✅ <b>Слот устройства добавлен!</b>")
            else:
                await process_successful_payment(target_id, days, is_family_plan=is_family)
                if chat_id: await bot.send_message(chat_id=chat_id, text="✅ <b>Оплата успешно получена!</b>\n\nПодписка активирована.")
    except Exception as e:
        logging.error(f"Ошибка фоновой обработки вебхука: {e}", exc_info=True)

async def handle_check_pending(request):
    try:
        real_user_id, user_email = await get_user_from_token(request)
        if not real_user_id or not user_email: return web.Response(text="Unauthorized", status=401)
            
        headers_sb = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        async with ClientSession() as session:
            async with session.get(f"{SUPABASE_URL}/rest/v1/pending_payments?email=eq.{user_email}&select=id,days", headers=headers_sb) as resp:
                pending = await resp.json()
                
            if not isinstance(pending, list) or not pending: return web.Response(text="No pending payments", status=404)
                
            for p in pending:
                async with session.delete(f"{SUPABASE_URL}/rest/v1/pending_payments?id=eq.{p['id']}", headers=headers_sb) as del_resp:
                    if del_resp.status == 204:
                        await process_successful_payment(real_user_id, p['days'])
            return web.Response(text="OK", status=200)
    except Exception as e:
        logging.error(f"Ошибка проверки отложенных платежей: {e}")
        return web.Response(text="Error", status=500)

async def handle_check_device(request):
    try:
        real_user_id, _ = await get_user_from_token(request)
        if not real_user_id: return web.Response(text=json.dumps({"error": "Unauthorized"}), status=401, content_type='application/json')
            
        data = await request.json()
        device_id = data.get('deviceId')
        device_name = data.get('deviceName', 'Устройство') 
        if not device_id: return web.Response(text=json.dumps({"error": "No deviceId"}), status=400, content_type='application/json')

        headers_sb = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        async with ClientSession() as session:
            async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{real_user_id}&select=active_devices,max_devices_allowed", headers=headers_sb) as resp:
                profile = await resp.json()
                
            if not isinstance(profile, list) or not profile: return web.Response(text=json.dumps({"error": "Profile not found"}), status=404, content_type='application/json')
            
            active_devices = profile[0].get('active_devices') or []
            max_allowed = profile[0].get('max_devices_allowed') or 2
            
            if not any(d.get('id') == device_id for d in active_devices):
                if len(active_devices) >= max_allowed:
                    return web.Response(text=json.dumps({"error": "Device limit reached"}), status=403, content_type='application/json')
                
                active_devices.append({"id": device_id, "name": device_name})
                async with session.patch(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{real_user_id}", json={"active_devices": active_devices}, headers={**headers_sb, "Content-Type": "application/json", "Prefer": "return=minimal"}) as patch_resp: pass
        return web.Response(text=json.dumps({"success": True}), status=200, content_type='application/json')
    except Exception as e:
        logging.error(f"Error check device: {e}")
        return web.Response(text=json.dumps({"error": str(e)}), status=500, content_type='application/json')

async def handle_trial(request):
    try:
        real_user_id, user_email = await get_user_from_token(request)
        if not real_user_id: return web.Response(text=json.dumps({"error": "Unauthorized"}), status=401, content_type='application/json')

        data = await request.json()
        device_id = data.get('deviceId', '')
        if not device_id: return web.Response(text=json.dumps({"error": "Device ID required"}), status=400, content_type='application/json')

        client_ip = request.headers.get('X-Real-IP', 'unknown')
        if client_ip == 'unknown':
            forwarded = request.headers.get('X-Forwarded-For')
            if forwarded: client_ip = forwarded.split(',')[0].strip()

        headers_sb = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}

        async with ClientSession() as session:
            async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{real_user_id}&select=has_used_trial", headers=headers_sb) as resp:
                profile = await resp.json()
                if not isinstance(profile, list) or not profile or profile[0].get('has_used_trial'):
                    return web.Response(text=json.dumps({"error": "Trial already used"}), status=403, content_type='application/json')

            async with session.get(f"{SUPABASE_URL}/rest/v1/trials_log?device_id=eq.{device_id}", headers=headers_sb) as resp:
                d_logs = await resp.json()
                if isinstance(d_logs, list) and d_logs:
                    return web.Response(text=json.dumps({"error": "Trial already used on this device"}), status=403, content_type='application/json')
            
            async with session.get(f"{SUPABASE_URL}/rest/v1/trials_log?email=eq.{user_email}", headers=headers_sb) as resp:
                e_logs = await resp.json()
                if isinstance(e_logs, list) and e_logs:
                    return web.Response(text=json.dumps({"error": "Trial already used with this email"}), status=403, content_type='application/json')

            if client_ip != 'unknown':
                async with session.get(f"{SUPABASE_URL}/rest/v1/trials_log?ip_address=eq.{client_ip}", headers=headers_sb) as resp:
                    ip_logs = await resp.json()
                    if isinstance(ip_logs, list) and len(ip_logs) >= 3:
                        return web.Response(text=json.dumps({"error": "Too many trials from this IP"}), status=403, content_type='application/json')

            await process_successful_payment(real_user_id, 7)

            log_data = {"user_id": real_user_id, "device_id": device_id, "ip_address": client_ip, "email": user_email}
            await session.post(f"{SUPABASE_URL}/rest/v1/trials_log", json=log_data, headers={**headers_sb, "Content-Type": "application/json"})
            await session.patch(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{real_user_id}", json={"has_used_trial": True}, headers={**headers_sb, "Content-Type": "application/json"})

        return web.Response(text=json.dumps({"success": True}), status=200, content_type='application/json')
    except Exception as e:
        logging.error(f"Ошибка активации триала: {e}")
        return web.Response(text=json.dumps({"error": str(e)}), status=500, content_type='application/json')

async def handle_apply_referral(request):
    try:
        real_user_id, _ = await get_user_from_token(request)
        if not real_user_id: return web.Response(text="Unauthorized", status=401)

        data = await request.json()
        referral_code = data.get('referralCode', '').strip().lower()
        if not re.fullmatch(r'[a-f0-9]{8}', referral_code):
            return web.Response(text=json.dumps({"error": "Invalid code format"}), status=400, content_type='application/json')
            
        headers_sb = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        
        async with ClientSession() as session:
            async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{real_user_id}&select=referred_by", headers=headers_sb) as resp:
                u_data = await resp.json()
                if isinstance(u_data, list) and len(u_data) > 0 and u_data[0].get('referred_by'):
                    return web.Response(text=json.dumps({"error": "Referral already applied"}), status=400, content_type='application/json')
                    
            async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?referral_code=eq.{referral_code}&select=id", headers=headers_sb) as resp:
                referrer_data = await resp.json()
                if not isinstance(referrer_data, list) or not referrer_data:
                    return web.Response(text=json.dumps({"error": "Invalid referral code"}), status=404, content_type='application/json')
                
                referrer_id = referrer_data[0]['id']
                if referrer_id == real_user_id:
                    return web.Response(text=json.dumps({"error": "Cannot refer yourself"}), status=400, content_type='application/json')

            await session.patch(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{real_user_id}", json={"referred_by": referrer_id}, headers={**headers_sb, "Content-Type": "application/json"})
                
        return web.Response(text=json.dumps({"success": True}), status=200, content_type='application/json')
    except Exception as e:
        return web.Response(text=json.dumps({"error": str(e)}), status=500, content_type='application/json')

async def handle_join_family(request):
    try:
        real_user_id, _ = await get_user_from_token(request)
        if not real_user_id: return web.Response(text="Unauthorized", status=401)

        data = await request.json()
        family_code = data.get('familyCode', '').strip().lower()
        if not family_code: return web.Response(text="Bad Request", status=400)
            
        headers_sb = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        
        async with ClientSession() as session:
            async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{real_user_id}&select=family_owner_id", headers=headers_sb) as resp:
                u_data = await resp.json()
                if isinstance(u_data, list) and len(u_data) > 0 and u_data[0].get('family_owner_id'):
                    return web.Response(text=json.dumps({"error": "Already in a family"}), status=400, content_type='application/json')
                    
            async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?family_code=eq.{family_code}&select=id,subscription_expires_at", headers=headers_sb) as resp:
                owner_data = await resp.json()
                if not isinstance(owner_data, list) or not owner_data:
                    return web.Response(text=json.dumps({"error": "Invalid family code"}), status=404, content_type='application/json')
                
                owner_id = owner_data[0]['id']
                owner_expires_str = owner_data[0].get('subscription_expires_at')
                
                if owner_id == real_user_id:
                    return web.Response(text=json.dumps({"error": "Cannot join your own family"}), status=400, content_type='application/json')
                if not owner_expires_str:
                     return web.Response(text=json.dumps({"error": "Owner has no active subscription"}), status=400, content_type='application/json')

            async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?family_owner_id=eq.{owner_id}&select=id", headers=headers_sb) as resp:
                members = await resp.json()
                if isinstance(members, list) and len(members) >= 2:
                    return web.Response(text=json.dumps({"error": "Family is full (max 3 accounts)"}), status=400, content_type='application/json')

                now = datetime.now(timezone.utc)
                owner_expires = datetime.fromisoformat(owner_expires_str.replace('Z', '+00:00'))
                days_left = (owner_expires - now).days
                
                if days_left <= 0:
                    return web.Response(text=json.dumps({"error": "Owner subscription expired"}), status=400, content_type='application/json')

            await session.patch(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{real_user_id}", json={"family_owner_id": owner_id}, headers={**headers_sb, "Content-Type": "application/json"})
            await process_successful_payment(real_user_id, days_left, is_bonus=True, family_owner_expires=owner_expires)
                
        return web.Response(text=json.dumps({"success": True}), status=200, content_type='application/json')
    except Exception as e:
        return web.Response(text=json.dumps({"error": str(e)}), status=500, content_type='application/json')

async def handle_remove_family_member(request):
    try:
        real_user_id, _ = await get_user_from_token(request)
        if not real_user_id: return web.Response(text="Unauthorized", status=401)

        data = await request.json()
        member_id = data.get('memberId')
        if not member_id: return web.Response(text="Bad Request", status=400)
            
        headers_sb = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        
        async with ClientSession() as session:
            async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{member_id}&select=family_owner_id", headers=headers_sb) as resp:
                m_data = await resp.json()
                if not isinstance(m_data, list) or not m_data or m_data[0].get('family_owner_id') != real_user_id:
                    return web.Response(text=json.dumps({"error": "User is not in your family"}), status=400, content_type='application/json')

            await session.patch(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{member_id}", json={"family_owner_id": None, "subscription_plan": None, "vless_urls": None, "subscription_expires_at": None}, headers={**headers_sb, "Content-Type": "application/json"})
                
        return web.Response(text=json.dumps({"success": True}), status=200, content_type='application/json')
    except Exception as e:
        return web.Response(text=json.dumps({"error": str(e)}), status=500, content_type='application/json')

async def handle_leave_family(request):
    try:
        real_user_id, _ = await get_user_from_token(request)
        if not real_user_id: return web.Response(text="Unauthorized", status=401)

        headers_sb = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        async with ClientSession() as session:
            await session.patch(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{real_user_id}", json={"family_owner_id": None, "subscription_plan": None, "vless_urls": None, "subscription_expires_at": None}, headers={**headers_sb, "Content-Type": "application/json"})
                
        return web.Response(text=json.dumps({"success": True}), status=200, content_type='application/json')
    except Exception as e:
        return web.Response(text=json.dumps({"error": str(e)}), status=500, content_type='application/json')

async def handle_cron_expiring(request):
    try:
        headers_sb = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        async with ClientSession() as session:
            async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?fcm_token=not.is.null&subscription_expires_at=not.is.null&select=email,fcm_token,subscription_expires_at", headers=headers_sb) as resp:
                profiles = await resp.json()
                
        if not isinstance(profiles, list):
            return web.Response(text="Error fetching profiles", status=500)

        now = datetime.now(timezone.utc)
        count = 0
        for profile in profiles:
            expires_at = datetime.fromisoformat(profile['subscription_expires_at'].replace('Z', '+00:00'))
            days_left = (expires_at - now).days
            
            if days_left in [3, 1]:
                await send_push_notification(profile['fcm_token'], "⚠️ Подписка истекает!", f"У вас осталось {days_left} дн. Продлите сейчас, чтобы не остаться без VPN.")
                count += 1
                
        return web.Response(text=f"Cron executed. Pushes sent: {count}", status=200)
    except Exception as e:
        logging.error(f"Ошибка крона пушей: {e}")
        return web.Response(text=str(e), status=500)


# --- ЗАПУСК ---

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="EchoVPN bot is running!"))
    app.router.add_post('/api/amypay/create-payment', handle_create_amypay_payment)
    app.router.add_post('/api/amypay/webhook', handle_amypay_webhook)
    app.router.add_get('/api/fake-pay', handle_fake_pay)
    app.router.add_post('/api/check-pending', handle_check_pending)
    app.router.add_post('/api/trial', handle_trial)
    app.router.add_post('/api/apply-referral', handle_apply_referral)
    app.router.add_post('/api/join-family', handle_join_family)
    app.router.add_post('/api/remove-family-member', handle_remove_family_member)
    app.router.add_post('/api/leave-family', handle_leave_family)
    app.router.add_post('/api/check-device', handle_check_device)
    app.router.add_post('/api/buy-device-slot', handle_buy_device_slot)
    app.router.add_post('/api/cron/notify-expiring', handle_cron_expiring)

    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

async def main():
    await asyncio.sleep(2)
    await check_amypay_token()
    try:
        await asyncio.gather(start_web_server(), dp.start_polling(bot))
    except Exception as e:
        logging.critical(f"Фатальная ошибка: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    asyncio.run(main())
