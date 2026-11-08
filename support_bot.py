import asyncio
import logging
import os
import html
import json
import uuid
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web, ClientSession

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '621860210'))
APK_URL = 'https://disk.yandex.ru/d/II9QBaio8oE8jQ' 
PRIVACY_URL = 'https://telegra.ph/Politika-konfidencialnosti-EchoVPN-11-07'
OFFER_URL = 'https://telegra.ph/Polzovatelskoe-soglashenie-EchoVPN-11-07'

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_SERVICE_KEY = os.getenv('SUPABASE_SERVICE_KEY')

AMYPAY_API_KEY = os.getenv('AMYPAY_API_KEY', '')
AMYPAY_SECRET_WORD_2 = os.getenv('AMYPAY_SECRET_WORD_2', '')

logging.basicConfig(level=logging.INFO)
router = Router()
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
dp.include_router(router)

class UserState(StatesGroup):
    waiting_for_email_buy = State()
    confirming_email_buy = State()
    waiting_for_email_status = State()

user_temp_data = {}
admin_reply_map = {}

async def check_amypay_token():
    if not AMYPAY_API_KEY: return
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

def get_user_menu():
    keyboard = [
        [InlineKeyboardButton(text="🛒 Купить подписку", callback_data="buy_menu")],
        [InlineKeyboardButton(text="👤 Моя подписка", callback_data="check_status")],
        [InlineKeyboardButton(text="📥 Скачать приложение", url=APK_URL)],
        [InlineKeyboardButton(text="💬 Помощь", callback_data="help_mode")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

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
        "Если вы еще не зарегистрированы, введите email, который планируете использовать. "
        "Оплата сохранится и начислится автоматически после регистрации в приложении.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Отмена", callback_data="main_menu")]])
    )

@router.message(UserState.waiting_for_email_buy)
async def process_email_for_buy(message: Message, state: FSMContext):
    email = message.text.strip().lower()
    
    # Сохраняем email во временные данные и переходим к подтверждению
    user_temp_data[message.from_user.id] = {"email": email}
    await state.set_state(UserState.confirming_email_buy)
    
    await message.answer(
        f"📧 Вы ввели: <b>{email}</b>\n\n"
        f"Все верно? Если да, нажмите кнопку ниже. Если ошиблись — введите email заново.",
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

    headers_sb = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"
    }
    
    async with ClientSession() as session:
        async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?email=eq.{email}&select=id", headers=headers_sb) as resp:
            data = await resp.json()
            
    if data:
        user_temp_data[callback.from_user.id]["target_id"] = data[0]['id']
    else:
        user_temp_data[callback.from_user.id]["target_id"] = f"waiting_{email}"
        
    await show_plans(callback.message)

async def show_plans(message: Message):
    headers_sb = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"
    }
    
    async with ClientSession() as session:
        async with session.get(f"{SUPABASE_URL}/rest/v1/plans?is_active=eq.true&order=duration_days.asc", headers=headers_sb) as resp:
            plans = await resp.json()

    if not plans:
        await message.answer("❌ Тарифы временно недоступны.", reply_markup=get_user_menu())
        return

    keyboard = []
    for plan in plans:
        keyboard.append([InlineKeyboardButton(
            text=f"{plan['name']} — {plan['price_rub']} ₽", 
            callback_data=f"pay_{plan['duration_days']}_{plan['price_rub']}"
        )])
    
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")])
    await message.answer(
        "💳 <b>Выберите тариф для покупки:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@router.callback_query(F.data.startswith("pay_"))
async def process_payment(callback: CallbackQuery):
    _, days_str, amount_str = callback.data.split("_")
    days = int(days_str)
    amount = float(amount_str)
    
    target_id = user_temp_data.get(callback.from_user.id, {}).get("target_id")
    
    if not target_id:
        await callback.message.edit_text("❌ Сессия истекла. Пожалуйста, начните заново.", reply_markup=get_user_menu())
        return

    api_url = "https://amypay.ru/api/v2/orders"
    headers = {
        "Authorization": f"Bearer {AMYPAY_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Добавляем chat_id в order_id для отправки уведомления после оплаты!
    order_id = f"{target_id}|{days}|{int(datetime.now().timestamp())}|{callback.from_user.id}"
    
    payload = {
        "amount": int(amount),
        "external_order_id": order_id,
        "description": f"EchoVPN Subscription ({days} days)"
    }
    
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
                    await callback.message.edit_text(
                        f"✅ Заказ на <b>{days} дней</b> (Сумма: {amount} ₽) создан!\n\n"
                        f"Нажмите кнопку ниже, чтобы оплатить.",
                        reply_markup=keyboard
                    )
                    return
    
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
    
    headers_sb = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"
    }
    
    async with ClientSession() as session:
        async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?email=eq.{email}&select=subscription_plan,subscription_expires_at", headers=headers_sb) as resp:
            data = await resp.json()
            
    if not data:
        await message.answer("❌ Пользователь с таким email не найден.", reply_markup=get_user_menu())
        return
        
    profile = data[0]
    expires_at_str = profile.get('subscription_expires_at')
    plan = profile.get('subscription_plan')
    
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
            
    await message.answer(
        "❌ <b>Подписка не активна.</b>\n\nВы можете приобрести её в главном меню.",
        reply_markup=get_user_menu()
    )

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

    await message.answer(f"⏳ Ищу пользователя {email} и начисляю {days} дней...")

    headers_sb = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"
    }

    async with ClientSession() as session:
        async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?email=eq.{email}&select=id", headers=headers_sb) as resp:
            data = await resp.json()
            if not data:
                await message.answer("❌ Пользователь с таким email не найден.")
                return
            user_id = data[0]['id']

    await process_successful_payment(user_id, days)
    await message.answer(f"✅ Подписка на {days} дней успешно выдана для {email}!")

@router.message(F.chat.id != ADMIN_ID)
async def forward_to_admin(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return

    user = message.from_user
    safe_name = html.escape(user.full_name)
    
    admin_text_header = (
        f"<b>📩 Новое сообщение в поддержку</b>\n\n"
        f"👤 От: {safe_name}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"🔗 Username: @{user.username or 'нет'}\n\n"
    )
    
    try:
        sent_msg = None
        if message.text:
            text = html.escape(message.text)
            sent_msg = await bot.send_message(chat_id=ADMIN_ID, text=admin_text_header + f"💬 Текст:\n{text}")
        elif message.photo:
            caption = html.escape(message.caption) if message.caption else "Без подписи"
            sent_msg = await bot.send_photo(chat_id=ADMIN_ID, photo=message.photo[-1].file_id, caption=admin_text_header + f"🖼 Фото:\n{caption}")
        elif message.document:
            caption = html.escape(message.caption) if message.caption else "Без подписи"
            sent_msg = await bot.send_document(chat_id=ADMIN_ID, document=message.document.file_id, caption=admin_text_header + f"📄 Документ:\n{caption}")
        elif message.video:
            sent_msg = await bot.send_video(chat_id=ADMIN_ID, video=message.video.file_id, caption=admin_text_header + "🎥 Видео")
        else:
            sent_msg = await bot.send_message(chat_id=ADMIN_ID, text=admin_text_header + "❓ Неизвестный тип сообщения")

        if sent_msg:
            admin_reply_map[sent_msg.message_id] = user.id
            
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

    try:
        if message.text:
            safe_reply = html.escape(message.text)
            await bot.send_message(chat_id=target_user_id, text=f"<b>📬 Ответ поддержки EchoVPN:</b>\n\n{safe_reply}")
            await message.answer("✅ Ответ отправлен пользователю.")
        else:
            await message.answer("⚠️ Пожалуйста, отвечайте текстом.")
    except Exception as e:
        await message.answer(f"❌ Ошибка при отправке ответа: {e}")

async def process_successful_payment(user_id, days):
    logging.info(f"Активация подписки для {user_id} на {days} дней")
    
    headers_sb = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"
    }
    
    async with ClientSession() as session:
        async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=email,subscription_expires_at", headers=headers_sb) as resp:
            profile_data = await resp.json()
            if not profile_data:
                logging.error(f"Профиль {user_id} не найден в базе.")
                return
            user_email = profile_data[0].get('email', 'user')
            current_expires_str = profile_data[0].get('subscription_expires_at')
        
        safe_email = user_email.split('@')[0].replace('.', '_').replace('-', '_')
        client_name = safe_email

        now = datetime.now(timezone.utc)
        if current_expires_str:
            current_expires = datetime.fromisoformat(current_expires_str.replace('Z', '+00:00'))
            base_date = current_expires if current_expires > now else now
        else:
            base_date = now
        
        new_expires = base_date + timedelta(days=days)
        new_expires_iso = new_expires.isoformat()
        total_days_for_panel = max(1, (new_expires - now).days)

        async with session.get(f"{SUPABASE_URL}/rest/v1/servers?select=*", headers=headers_sb) as resp:
            servers = await resp.json()

        generated_urls = {}
        client_uuid = str(uuid.uuid4())

        async def create_on_server(server):
            api_url = server['api_url']
            api_token = server['api_token']
            inbound_id = server['inbound_id']
            base_url = api_url.replace('/create', '')
            
            headers_xui = {"Content-Type": "application/json", "Authorization": f"Bearer {api_token}", "X-API-Key": api_token}

            try:
                encoded_name = quote(client_name, safe='')
                async with session.delete(f"{base_url}/clients/{encoded_name}", headers=headers_xui, timeout=5): pass 
            except Exception: pass

            payload = {"name": client_name, "uuid": client_uuid, "days": total_days_for_panel, "inbound_ids": [inbound_id]}

            try:
                async with session.post(api_url, json=payload, headers=headers_xui, timeout=5) as resp:
                    if resp.status in [200, 201]:
                        domain = server['domain']
                        pbk = server['pbk']
                        sid = server['sid']
                        
                        if server.get('country_code') == 'ES':
                            vless_url = f"vless://{client_uuid}@{domain}:443?type=tcp&security=reality&sni={domain}&fp=firefox&pbk={pbk}&sid={sid}&spx=%2F&encryption=none#EchoVPN-{server['name']}"
                        else:
                            vless_url = f"vless://{client_uuid}@{domain}:443?type=tcp&security=reality&sni={domain}&fp=firefox&pbk={pbk}&sid={sid}&spx=%2F&encryption=none&flow=xtls-rprx-vision#EchoVPN-{server['name']}"
                        
                        vless_url = vless_url.replace(' ', '').replace('\n', '').replace('\r', '')
                        return server['id'], vless_url
            except Exception: pass
            return None

        tasks = [create_on_server(s) for s in servers]
        results = await asyncio.gather(*tasks)

        for result in results:
            if result:
                server_id, url = result
                generated_urls[server_id] = url

        if generated_urls:
            update_data = {"vless_urls": generated_urls, "subscription_plan": "premium", "subscription_expires_at": new_expires_iso}
            headers_patch = {**headers_sb, "Content-Type": "application/json", "Prefer": "return=minimal"}
            async with session.patch(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}", json=update_data, headers=headers_patch) as resp:
                if resp.status not in [200, 204]:
                    logging.error(f"Ошибка Supabase: {await resp.text()}")

async def handle_create_amypay_payment(request):
    try:
        data = await request.json()
        user_id = data.get('userId')
        days = data.get('days', 30)
        amount = float(data.get('amount', 249.00))

        if not user_id:
            return web.Response(text=json.dumps({"error": "No userId"}), status=400, content_type='application/json')

        order_id = f"{user_id}|{days}|{int(datetime.now().timestamp())}"
        
        api_url = "https://amypay.ru/api/v2/orders"
        headers = {
            "Authorization": f"Bearer {AMYPAY_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "amount": int(amount), 
            "external_order_id": order_id,
            "description": f"EchoVPN Subscription ({days} days)"
        }
        
        async with ClientSession() as session:
            async with session.post(api_url, json=payload, headers=headers, timeout=10) as resp:
                resp_data = await resp.json()
                
                if resp.status in [200, 201] and resp_data.get('ok'):
                    pay_url = resp_data.get('order', {}).get('pay_url')
                    if pay_url:
                        return web.Response(
                            text=json.dumps({"payment_url": pay_url}),
                            status=200,
                            content_type='application/json'
                        )
                
                return web.Response(text=json.dumps({"error": "Failed to create order"}), status=500, content_type='application/json')
                
    except Exception as e:
        return web.Response(text=json.dumps({"error": str(e)}), status=500, content_type='application/json')

async def handle_amypay_webhook(request):
    try:
        data = await request.post()
        external_order_id = data.get('external_order_id')
        
        if external_order_id:
            parts = external_order_id.split('|')
            if len(parts) >= 2:
                target_id = parts[0]
                days = int(parts[1])
                chat_id = int(parts[3]) if len(parts) > 3 else None # Получаем chat_id
                
                if target_id.startswith("waiting_"):
                    email = target_id.replace("waiting_", "")
                    logging.info(f"Отложенная оплата для {email} на {days} дней. Сохраняем в БД.")
                    
                    headers_sb = {
                        "apikey": SUPABASE_SERVICE_KEY,
                        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                        "Content-Type": "application/json",
                        "Prefer": "return=minimal"
                    }
                    
                    async with ClientSession() as session:
                        payload = {"email": email, "days": days}
                        await session.post(f"{SUPABASE_URL}/rest/v1/pending_payments", json=payload, headers=headers_sb)
                        
                    if chat_id:
                        await bot.send_message(
                            chat_id=chat_id,
                            text="✅ <b>Оплата получена!</b>\n\n"
                                 "Мы сохранили вашу подписку. Она начислится автоматически, как только вы зарегистрируетесь в приложении с этим email."
                        )
                else:
                    asyncio.create_task(process_successful_payment(target_id, days))
                    
                    if chat_id:
                        await bot.send_message(
                            chat_id=chat_id,
                            text="✅ <b>Оплата успешно получена!</b>\n\n"
                                 "Ваша подписка активирована. Откройте приложение EchoVPN, обновите экран подписки, и вы увидите новые дни."
                        )
                    
                return web.Response(text="OK", status=200)
        
        return web.Response(text="OK", status=200)

    except Exception as e:
        logging.error(f"Ошибка обработки вебхука: {e}")
        return web.Response(text="Error", status=500)

async def handle_check_pending(request):
    try:
        data = await request.json()
        user_id = data.get('userId')
        email = data.get('email')
        
        if not user_id or not email:
            return web.Response(text="Bad Request", status=400)
            
        headers_sb = {
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"
        }
        
        async with ClientSession() as session:
            async with session.get(f"{SUPABASE_URL}/rest/v1/pending_payments?email=eq.{email}&select=id,days", headers=headers_sb) as resp:
                pending = await resp.json()
                
            if not pending:
                return web.Response(text="No pending payments", status=404)
                
            for p in pending:
                days = p['days']
                await process_successful_payment(user_id, days)
                
                async with session.delete(f"{SUPABASE_URL}/rest/v1/pending_payments?id=eq.{p['id']}", headers=headers_sb) as del_resp:
                    pass
                    
            return web.Response(text="OK", status=200)
            
    except Exception as e:
        logging.error(f"Ошибка проверки отложенных платежей: {e}")
        return web.Response(text="Error", status=500)

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="EchoVPN bot is running!"))
    app.router.add_post('/api/amypay/create-payment', handle_create_amypay_payment)
    app.router.add_post('/api/amypay/webhook', handle_amypay_webhook)
    app.router.add_post('/api/check-pending', handle_check_pending)

    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

async def main():
    await asyncio.sleep(10)
    await check_amypay_token()
    await asyncio.gather(start_web_server(), dp.start_polling(bot))

if __name__ == "__main__":
    asyncio.run(main())
