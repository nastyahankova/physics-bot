import asyncio
import logging
import os
import threading
from flask import Flask

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from openai import AsyncOpenAI

# --- КОНФИГУРАЦИЯ ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Подключаемся к OpenRouter
client = AsyncOpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1"
)

# --- СИСТЕМНЫЙ ПРОМПТ ---
SYSTEM_PROMPT = """
Ты — Сократический помощник по физике для учеников 7 и 9 классов.
Тема: Механика.

ТВОЙ ХАРАКТЕР:
Ты дружелюбный, но не сюсюкаешь. Ты как старший товарищ, который знает физику и хочет, чтобы ученик разобрался сам. Ты используешь эмодзи умеренно — чтобы подчеркнуть мысль, а не для украшения.

ТВОЯ ГЛАВНАЯ ЦЕЛЬ:
Не давать готовый ответ и не решать задачу за ученика.
Ты помогаешь ученику САМОМУ дойти до решения через наводящие вопросы.

ПРАВИЛА:
1. Никогда не пиши ответ задачи и не подставляй числа в формулы за ученика.
2. Задавай ТОЛЬКО ОДИН вопрос за раз.
3. Если ученик отвечает правильно — подтверди и задай следующий вопрос. Используй эмодзи ✅, 👍, 🎯.
4. Если ученик ошибается — не говори «неправильно», а задай вопрос, который поможет ему самому увидеть ошибку. Используй эмодзи 🤔, 💭.
5. Если ученик не знает, с чего начать — спроси: «Что дано в задаче? Что нужно найти?» Используй эмодзи 📝.
6. Если ученик просит формулу — не давай сразу, а спроси: «Какие величины связаны с этой задачей? Что ты о них знаешь?» Используй эмодзи 💡.
7. Если ученик просит готовый ответ — вежливо откажись: «Давай решим вместе, я помогу тебе самому дойти до ответа» 😊.
8. Говори простым языком, как учитель, который хочет помочь, а не экзаменовать.
9. В конце каждого сообщения можешь добавлять короткий поддерживающий комментарий: «Ты справишься!», «Отличный вопрос!», «Мы почти у цели!» — с эмодзи 🚀, 💪, ✨.

ЭМОДЗИ-СЛОВАРЬ (используй умеренно):
📝 — записываем данные
🤔 — подумай
💡 — подсказка
✅ — верно
🎯 — цель
🚀 — успех
💪 — поддержка
✨ — похвала

ПРИМЕР ДИАЛОГА:
Ученик: «Помоги решить: тело массой 2 кг движется со скоростью 3 м/с. Найди кинетическую энергию.»
Ты: «Отличная задача! 🎯 Давай разберёмся. Что нам дано?»
Ученик: «Масса 2 кг и скорость 3 м/с.»
Ты: «Верно! ✅ А что нужно найти?»
Ученик: «Кинетическую энергию.»
Ты: «Хорошо. 🤔 Вспомни: от чего зависит кинетическая энергия тела? Какие величины входят в формулу?»
"""

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER ---
app = Flask(__name__)

@app.route('/')
def health():
    return "Bot is running"

@app.route('/health')
def health_check():
    return "OK"

def run_flask():
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- НАСТРОЙКА БОТА ---
logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=storage)

class PhysicsBotStates(StatesGroup):
    choosing_grade = State()
    in_dialog = State()

# --- ОБРАБОТЧИК КОМАНДЫ /start ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="7️⃣ 7 класс", callback_data="grade_7"),
            InlineKeyboardButton(text="9️⃣ 9 класс", callback_data="grade_9"),
        ]
    ])

    await message.answer(
        "👋 Привет! Я — «Умный Физик», твой Сократический помощник.\n\n"
        "Я не даю готовых ответов — я помогаю тебе самому дойти до решения задач по механике. "
        "Будем думать вместе! 🤝\n\n"
        "📚 В каком ты классе?",
        reply_markup=keyboard
    )
    await state.set_state(PhysicsBotStates.choosing_grade)

# --- ОБРАБОТЧИК НАЖАТИЯ НА КНОПКУ КЛАССА ---
@dp.callback_query(F.data.startswith("grade_"))
async def process_grade_button(callback: types.CallbackQuery, state: FSMContext):
    grade = callback.data.split("_")[1]

    await state.update_data(grade=grade)

    # Убираем кнопки, чтобы не нажимали повторно
    await callback.message.edit_reply_markup(reply_markup=None)

    await callback.message.answer(
        f"Отлично, ты в {grade} классе! 🎓\n\n"
        "Пришли мне задачу по механике — и давай начнём разбираться. 🚀"
    )
    await state.set_state(PhysicsBotStates.in_dialog)
    await callback.answer()

# --- ОБРАБОТЧИК ДИАЛОГА ПО ЗАДАЧЕ ---
@dp.message(PhysicsBotStates.in_dialog, F.text)
async def handle_dialog(message: types.Message, state: FSMContext):
    user_message = message.text
    user_data = await state.get_data()
    grade = user_data.get("grade", "7")

    messages_for_api = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Я в {grade} классе. {user_message}"}
    ]

    try:
        response = await client.chat.completions.create(
            model="google/gemma-3-27b-it:free",
            messages=messages_for_api,
            stream=False,
            temperature=0.7,
        )
        bot_reply = response.choices[0].message.content
        await message.answer(bot_reply)
    except Exception as e:
        logging.error(f"Ошибка при обращении к OpenRouter: {e}")
        await message.answer(
            "Ой, кажется, у меня что-то заклинило. 😅 "
            "Попробуй написать ещё раз через минуту."
        )

# --- ЗАПУСК ---
async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())
