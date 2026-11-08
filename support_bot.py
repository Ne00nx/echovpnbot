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
from aiogram.filters import CommandStart
from aiogram.client.default import DefaultBotProperties
from aiohttp import web, ClientSession

# ==========================================
# НАСТРОЙКИ
# ==========================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '621860210'))
APK_URL = 'https://disk.yandex.ru/d/II9QBaio8oE8jQ' 
PRIVACY_URL = 'https://telegra.ph/Politika-konfidencialnosti-EchoVPN-11-07'
OFFER_URL = 'https://telegra.ph/Polzovatelskoe-soglashenie-EchoVPN-11-07'

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_SERVICE_KEY = os.getenv('SUPABASE_SERVICE_KEY')

# --- НАСТРОЙКИ AMYPAY ---
# Твой новый ключ кассы из панели (тот, что на скриншоте)
AMYPAY_API_KEY = "197b0dc212cd479917d6f786b055353" 
# Обязательно вставь секретное слово №2 из панели Amypay!
AMYPAY_SECRET_WORD_2 = "0e6052d0663024aaa9c9f5d8" 
# ==========================================

logging.basicConfig(level=logging.INFO)
router = Router()
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
dp.include_router(router)

def get_main_menu():
    keyboard = [
        [InlineKeyboardButton(text="📥 Скачать приложение", url=APK_URL)],
        [InlineKeyboardButton(text="📄 Документы", callback_data="docs_menu")],
        [InlineKeyboardButton(text="🆘 Помощь", callback_data="help_mode")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_docs_menu():
    keyboard = [
        [InlineKeyboardButton(text="Политика конфиденциальности", url=PRIVACY_URL)],
        [InlineKeyboardButton(text="Пользовательское соглашение", url=OFFER_URL)],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer("👋 Добро пожаловать в EchoVPN!\n\nВыберите нужный раздел:", reply_markup=get_main_menu())

@router.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery):
    await callback.message.edit_text("👋 Добро пожаловать в EchoVPN!\n\nВыберите нужный раздел:", reply_markup=get_main_menu())

@router.callback_query(F.data == "docs_menu")
async def show_docs(callback: CallbackQuery):
    await callback.message.edit_text("📄 Выберите документ для ознакомления:", reply_markup=get_docs_menu())

@router.callback_query(F.data == "help_mode")
async def help_mode(callback: CallbackQuery):
    await callback.message.edit_text("💬 Вы перешли в режим поддержки.\n\nОпишите вашу проблему одним сообщением, и мы ответим вам в ближайшее время.")

@router.message()
async def forward_to_admin(message: Message):
    user = message.from_user
    safe_name = html.escape(user.full_name)
    safe_text = html.escape(message.text) if message.text else "Нет текста (возможно, вложение)"
    
    admin_text = (
        f"<b>📩 Новое сообщение в поддержку</b>\n\n"
        f"👤 От: {safe_name}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"🔗 Username: @{user.username or 'нет'}\n\n"
        f"💬 Текст:\n{safe_text}"
    )
    
    try:
        await bot.send_message(chat_id=ADMIN_ID, text=admin_text)
        await message.answer("✅ Ваше сообщение передано в поддержку. Ожидайте ответа.")
    except Exception as e:
        logging.error(f"Ошибка отправки админу: {e}")
        await message.answer("⚠️ В данный момент поддержка недоступна. Попробуйте позже.")

@router.message(F.chat.id == ADMIN_ID, F.reply_to_message)
async def reply_to_user(message: Message):
    try:
        original_text = message.reply_to_message.text
        if original_text and "🆔 ID: <code>" in original_text:
            user_id_str = original_text.split("🆔 ID: <code>")[1].split("</code>")[0]
            user_id = int(user_id_str)
            safe_reply = html.escape(message.text)
            await bot.send_message(
                chat_id=user_id,
                text=f"<b>📬 Ответ поддержки EchoVPN:</b>\n\n{safe_reply}"
            )
            await message.answer("✅ Ответ отправлен пользователю.")
        else:
            await message.answer("❌ Не удалось найти ID пользователя в сообщении.")
    except Exception as e:
        await message.answer(f"❌ Ошибка при отправке: {e}")

# ==========================================
# ФУНКЦИЯ АКТИВАЦИИ ПОДПИСКИ И СОЗДАНИЯ КЛЮЧЕЙ
# ==========================================
async def process_successful_payment(user_id, days):
    logging.info(f"🚀 Активация подписки для {user_id} на {days} дней")
    
    headers_sb = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"
    }
    
    async with ClientSession() as session:
        async with session.get(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=email,subscription_expires_at", headers=headers_sb) as resp:
            profile_data = await resp.json()
            user_email = profile_data[0].get('email', 'user') if profile_data else "user"
            current_expires_str = profile_data[0].get('subscription_expires_at') if profile_data else None
        
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
            except Exception as e:
                logging.error(f"Сетевая ошибка на {server['name']}: {e}")
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
                else:
                    logging.info(f"✅ Подписка для {user_id} успешно активирована!")

# ==========================================
# API ДЛЯ ПРИЛОЖЕНИЯ (Создание платежа через Amypay)
# ==========================================
async def handle_create_amypay_payment(request):
    try:
        data = await request.json()
        user_id = data.get('userId')
        days = data.get('days', 30)
        amount = float(data.get('amount', 249.00))

        if not user_id:
            return web.Response(text=json.dumps({"error": "No userId"}), status=400, content_type='application/json')

        # Формируем уникальный ID заказа (user_id_days_timestamp)
        external_order_id = f"{user_id}_{days}_{int(datetime.now().timestamp())}"
        
        api_url = "https://amypay.ru/api/v2/orders"
        headers = {
            "Authorization": f"Bearer {AMYPAY_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "amount": amount,
            "external_order_id": external_order_id,
            "description": f"EchoVPN Subscription ({days} days)"
        }
        
        async with ClientSession() as session:
            async with session.post(api_url, json=payload, headers=headers, timeout=10) as resp:
                resp_data = await resp.json()
                logging.info(f"Ответ от Amypay API: {resp_data}")
                
                if resp.status == 201 and resp_data.get('ok'):
                    pay_url = resp_data['order']['pay_url']
                    return web.Response(
                        text=json.dumps({"payment_url": pay_url}),
                        status=200,
                        content_type='application/json'
                    )
                else:
                    error_msg = resp_data.get('error', 'Unknown error')
                    logging.error(f"Ошибка Amypay API: {error_msg}")
                    return web.Response(text=json.dumps({"error": error_msg}), status=500, content_type='application/json')
                
    except Exception as e:
        logging.error(f"Критическая ошибка создания платежа: {e}")
        return web.Response(text=json.dumps({"error": str(e)}), status=500, content_type='application/json')

# ==========================================
# ВЕБХУК ОТ AMYPAY (Прием уведомлений)
# ==========================================
async def handle_amypay_webhook(request):
    try:
        data = await request.post()
        logging.info(f"Получен вебхук от Amypay: {data}")
        
        order_number = data.get('order_number')
        amount = data.get('amount')
        status = data.get('status')
        external_order_id = data.get('external_order_id')
        signature = data.get('signature')
        
        # Проверка подписи (согласно документации Amypay)
        if order_number and amount and signature:
            sign_str = f"{AMYPAY_API_KEY}:{order_number}:{amount}:{AMYPAY_SECRET_WORD_2}"
            expected_sign = hashlib.sha256(sign_str.encode()).hexdigest()
            
            if signature != expected_sign:
                logging.warning(f"❌ Неверная подпись вебхука! Ожидалось: {expected_sign}, Получено: {signature}")
                return web.Response(text="Invalid signature", status=403)
            
            logging.info(f"✅ Подпись вебхука верна для заказа {order_number}")
        
        if status == 'paid' and external_order_id:
            parts = external_order_id.split('_')
            if len(parts) >= 2:
                user_id = parts[0]
                days = int(parts[1])
                asyncio.create_task(process_successful_payment(user_id, days))
                logging.info(f"✅ Оплата по заказу {order_number} успешна! Запущена активация для {user_id} на {days} дней.")
        else:
            logging.warning(f"❌ Статус заказа {order_number}: {status}")
            
        return web.Response(text="OK", status=200)

    except Exception as e:
        logging.error(f"Ошибка обработки вебхука: {e}")
        return web.Response(text="Error", status=500)

# ==========================================
# ЗАПУСК СЕРВЕРА
# ==========================================
async def start_web_server():
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="EchoVPN bot is running!"))
    app.router.add_post('/api/amypay/create-payment', handle_create_amypay_payment)
    app.router.add_post('/api/amypay/webhook', handle_amypay_webhook)

    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

async def main():
    logging.info("Ожидание завершения старого деплоя (10 сек)...")
    await asyncio.sleep(10)
    await asyncio.gather(start_web_server(), dp.start_polling(bot))

if __name__ == "__main__":
    asyncio.run(main())
