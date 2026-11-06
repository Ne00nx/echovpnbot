import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.default import DefaultBotProperties
from aiohttp import web

# ==========================================
# НАСТРОЙКИ
# ==========================================
# На Render мы зададим эти переменные в настройках (Environment Variables).
# Если вы запускаете локально, можете вписать их сюда вручную.
BOT_TOKEN = os.getenv('BOT_TOKEN', 'ВАШ_ТОКЕН_ОТ_BOTFATHER')
ADMIN_ID = int(os.getenv('ADMIN_ID', '123456789')) # Ваш числовой ID
APK_URL = os.getenv('APK_URL', 'https://example.com/echovpn.apk')

# Прокси нужен только для локальной разработки. На Render оставляем None.
PROXY_URL = None # Если тестируете дома, раскомментируйте строку ниже:
# PROXY_URL = 'socks5://127.0.0.1:3067'
# ==========================================

logging.basicConfig(level=logging.INFO)

router = Router()

# Настраиваем сессию (с прокси или без)
session = AiohttpSession(proxy=PROXY_URL) if PROXY_URL else None

# Инициализируем бота
bot = Bot(
    token=BOT_TOKEN, 
    session=session,
    default=DefaultBotProperties(parse_mode="Markdown")
)
dp = Dispatcher()
dp.include_router(router)

# --- ГЛАВНОЕ МЕНЮ ---
def get_main_menu():
    keyboard = [
        [InlineKeyboardButton(text="📥 Скачать приложение", url=APK_URL)],
        [InlineKeyboardButton(text="📄 Документы", callback_data="docs_menu")],
        [InlineKeyboardButton(text="🆘 Помощь", callback_data="help_mode")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# --- МЕНЮ ДОКУМЕНТОВ ---
def get_docs_menu():
    keyboard = [
        [InlineKeyboardButton(text="Политика конфиденциальности", url='https://telegra.ph/POLITIKA-KONFIDENCIALNOSTI-08-12-99')],
        [InlineKeyboardButton(text="Пользовательское соглашение", url='https://telegra.ph/PUBLICHNAYA-OFERTA-08-12-15')],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# --- ОБРАБОТЧИК КОМАНДЫ /start ---
@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Добро пожаловать в EchoVPN!\n\nВыберите нужный раздел:",
        reply_markup=get_main_menu()
    )

# --- ОБРАБОТЧИКИ НАЖАТИЙ НА КНОПКИ ---
@router.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery):
    await callback.message.edit_text(
        "👋 Добро пожаловать в EchoVPN!\n\nВыберите нужный раздел:",
        reply_markup=get_main_menu()
    )
    await callback.answer()

@router.callback_query(F.data == "docs_menu")
async def show_docs(callback: CallbackQuery):
    await callback.message.edit_text(
        "📄 Выберите документ для ознакомления:",
        reply_markup=get_docs_menu()
    )
    await callback.answer()

@router.callback_query(F.data == "help_mode")
async def help_mode(callback: CallbackQuery):
    await callback.message.edit_text(
        "💬 Вы перешли в режим поддержки.\n\nОпишите вашу проблему одним сообщением, и мы ответим вам в ближайшее время."
    )
    await callback.answer()

# --- ПЕРЕСЫЛКА СООБЩЕНИЙ АДМИНИСТРАТОРУ ---
@router.message()
async def forward_to_admin(message: Message):
    user = message.from_user
    
    admin_text = (
        f"📩 **Новое сообщение в поддержку**\n\n"
        f"👤 От: {user.full_name}\n"
        f"🆔 ID: `{user.id}`\n"
        f"🔗 Username: @{user.username or 'нет'}\n\n"
        f"💬 Текст:\n{message.text}"
    )
    
    try:
        await bot.send_message(chat_id=ADMIN_ID, text=admin_text)
        await message.answer("✅ Ваше сообщение передано в поддержку. Ожидайте ответа.")
    except Exception as e:
        logging.error(f"Ошибка отправки админу: {e}")
        await message.answer("⚠️ В данный момент поддержка недоступна. Попробуйте позже.")

# --- ОТВЕТ АДМИНИСТРАТОРА ПОЛЬЗОВАТЕЛЮ ---
@router.message(F.chat.id == ADMIN_ID, F.reply_to_message)
async def reply_to_user(message: Message):
    try:
        original_text = message.reply_to_message.text
        
        if original_text and "🆔 ID: `" in original_text:
            user_id_str = original_text.split("🆔 ID: `")[1].split("`")[0]
            user_id = int(user_id_str)
            
            await bot.send_message(
                chat_id=user_id,
                text=f"📬 **Ответ поддержки EchoVPN:**\n\n{message.text}"
            )
            await message.answer("✅ Ответ отправлен пользователю.")
        else:
            await message.answer("❌ Не удалось найти ID пользователя в сообщении.")
    except Exception as e:
        await message.answer(f"❌ Ошибка при отправке: {e}")

# ==========================================
# ВЕБ-СЕРВЕР ДЛЯ RENDER
# ==========================================
async def handle(request):
    """Обработчик для проверки работоспособности (health check)."""
    return web.Response(text="EchoVPN bot is running!")

async def start_web_server():
    """Запускает веб-сервер на порту, который выдает Render."""
    app = web.Application()
    app.router.add_get('/', handle)
    
    # Render передает порт через переменную окружения PORT
    port = int(os.environ.get("PORT", 8080))
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"Веб-сервер запущен на порту {port}")

# --- ГЛАВНАЯ ФУНКЦИЯ ЗАПУСКА ---
async def main():
    logging.info("Запуск бота EchoVPN...")
    # Запускаем веб-сервер и бота одновременно
    await asyncio.gather(
        start_web_server(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    asyncio.run(main())