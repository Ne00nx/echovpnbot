import asyncio
import logging
import os
import html
import json
import uuid
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
# ВЕБ-СЕРВЕР И API ДЛЯ СОЗДАНИЯ VPN
# ==========================================
async def handle(request):
    return web.Response(text="EchoVPN bot is running!")

async def handle_create_vpn(request):
    logging.info("!!! ПОЛУЧЕН ЗАПРОС НА СОЗДАНИЕ VPN !!!")
    
    # ДИАГНОСТИКА 1: Проверяем, дошел ли заголовок
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        logging.error(f"❌ ОШИБКА: Заголовок Authorization отсутствует или неверного формата. Получено: {auth_header}")
        return web.Response(text=json.dumps({"error": "Missing or invalid Authorization header"}), status=401, content_type='application/json')
    
    token = auth_header.split(' ')[1]
    logging.info(f"✅ Заголовок получен. Токен начинается с: {token[:15]}...")

    try:
        data = await request.json()
        user_id = data.get('userId')
        days = data.get('days', 30)
        logging.info(f"📦 Данные из запроса: userId={user_id}, days={days}")

        if not user_id:
            return web.Response(text=json.dumps({"error": "No userId"}), status=400, content_type='application/json')

        headers_sb = {
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"
        }
        
        async with ClientSession() as session:
            # ДИАГНОСТИКА 2: Проверяем токен через Supabase
            sb_url = SUPABASE_URL.rstrip('/')
            auth_url = f"{sb_url}/auth/v1/user"
            
            async with session.get(auth_url, headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {token}"}) as auth_resp:
                if auth_resp.status != 200:
                    err_body = await auth_resp.text()
                    logging.error(f"❌ ОШИБКА SUPABASE AUTH: Статус {auth_resp.status}. Ответ: {err_body}")
                    return web.Response(text=json.dumps({"error": "Unauthorized"}), status=401, content_type='application/json')
                
                auth_data = await auth_resp.json()
                real_user_id = auth_data.get('id')
                logging.info(f"✅ Токен валиден. Пользователь: {real_user_id}")

                if real_user_id != user_id:
                    logging.error(f"❌ ОШИБКА: Несовпадение ID! В токене: {real_user_id}, В запросе: {user_id}")
                    return web.Response(text=json.dumps({"error": "User ID mismatch"}), status=403, content_type='application/json')

            # 3. Получаем email и текущую дату подписки
            async with session.get(f"{sb_url}/rest/v1/profiles?id=eq.{user_id}&select=email,subscription_expires_at", headers=headers_sb) as resp:
                profile_data = await resp.json()
                user_email = profile_data[0].get('email', 'user') if profile_data else "user"
                current_expires_str = profile_data[0].get('subscription_expires_at') if profile_data else None
            
            safe_email = user_email.split('@')[0].replace('.', '_').replace('-', '_')
            client_name = safe_email

            # 4. Считаем новую дату и дни для панели H1Cloud
            now = datetime.now(timezone.utc)
            if current_expires_str:
                current_expires = datetime.fromisoformat(current_expires_str.replace('Z', '+00:00'))
                base_date = current_expires if current_expires > now else now
            else:
                base_date = now
            
            new_expires = base_date + timedelta(days=days)
            new_expires_iso = new_expires.isoformat()

            total_days_for_panel = max(1, (new_expires - now).days)

            # 5. Получаем сервера
            async with session.get(f"{sb_url}/rest/v1/servers?select=*", headers=headers_sb) as resp:
                servers = await resp.json()

            generated_urls = {}
            client_uuid = str(uuid.uuid4())

            # 6. Создаем клиентов
            async def create_on_server(server):
                api_url = server['api_url']
                api_token = server['api_token']
                inbound_id = server['inbound_id']
                base_url = api_url.replace('/create', '')
                
                headers_xui = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_token}",
                    "X-API-Key": api_token
                }

                try:
                    encoded_name = quote(client_name, safe='')
                    async with session.delete(f"{base_url}/clients/{encoded_name}", headers=headers_xui, timeout=5) as del_resp:
                        pass 
                except Exception:
                    pass

                payload = {
                    "name": client_name,
                    "uuid": client_uuid,
                    "days": total_days_for_panel,
                    "inbound_ids": [inbound_id]
                }

                try:
                    async with session.post(api_url, json=payload, headers=headers_xui, timeout=5) as resp:
                        if resp.status in [200, 201]:
                            domain = server['domain']
                            pbk = server['pbk']
                            sid = server['sid']
                            
                            vless_url = f"vless://{client_uuid}@{domain}:443?type=tcp&security=reality&sni={domain}&fp=firefox&pbk={pbk}&sid={sid}&spx=%2F&encryption=none&flow=xtls-rprx-vision#EchoVPN-{server['name']}"
                            vless_url = vless_url.replace(' ', '').replace('\n', '').replace('\r', '')
                            return server['id'], vless_url
                        else:
                            err = await resp.text()
                            logging.error(f"Ошибка на сервере {server['name']}: {err}")
                            return None
                except Exception as e:
                    logging.error(f"Сетевая ошибка на сервере {server['name']}: {e}")
                    return None

            tasks = [create_on_server(s) for s in servers]
            results = await asyncio.gather(*tasks)

            for result in results:
                if result:
                    server_id, url = result
                    generated_urls[server_id] = url

            if not generated_urls:
                return web.Response(text=json.dumps({"error": "Все сервера недоступны"}), status=500, content_type='application/json')

            # 7. Обновляем базу
            update_data = {
                "vless_urls": generated_urls,
                "subscription_plan": "premium",
                "subscription_expires_at": new_expires_iso
            }
            headers_patch = {**headers_sb, "Content-Type": "application/json", "Prefer": "return=minimal"}
            
            async with session.patch(f"{sb_url}/rest/v1/profiles?id=eq.{user_id}", json=update_data, headers=headers_patch) as resp:
                if resp.status not in [200, 204]:
                    err = await resp.text()
                    logging.error(f"Ошибка Supabase при обновлении: {err}")
                    return web.Response(text=json.dumps({"error": f"Supabase error: {err}"}), status=500, content_type='application/json')

        return web.Response(text=json.dumps({"success": True}), status=200, content_type='application/json')

    except Exception as e:
        logging.error(f"Критическая ошибка API: {e}")
        return web.Response(text=json.dumps({"error": str(e)}), status=500, content_type='application/json')

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    app.router.add_post('/api/create-vpn', handle_create_vpn)
    
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
