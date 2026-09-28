import asyncio
import logging
import os
import threading
from collections import deque
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

client = AsyncOpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1"
)

# --- МОДЕЛЬ ---
MODEL_NAME = "openrouter/free"

# --- ПАМЯТЬ ДИАЛОГОВ ---
MAX_HISTORY = 15
user_histories = {}

def get_history(user_id: int):
    if user_id not in user_histories:
        user_histories[user_id] = deque(maxlen=MAX_HISTORY * 2)
    return user_histories[user_id]

def reset_history(user_id: int):
    if user_id in user_histories:
        user_histories[user_id].clear()

# --- СИСТЕМНЫЙ ПРОМПТ ---
SYSTEM_PROMPT = """
Ты — Сократический помощник по физике для учеников 7 и 9 классов.
Тема: Механика.

ТВОЙ ХАРАКТЕР:
Ты дружелюбный, но не сюсюкаешь. Ты как старший товарищ, который знает физику и хочет, чтобы ученик разобрался сам. Используешь эмодзи умеренно.

ТВОЯ ГЛАВНАЯ ЦЕЛЬ:
Не давать готовый ответ и не решать задачу за ученика. Ты помогаешь ученику САМОМУ дойти до решения через наводящие вопросы.

🔴 КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО:
1. Писать числовой ответ (например, «9 Дж», «15 Н», «3 м/с», «Ответ: ...»).
2. Подставлять числа в формулу за ученика.
3. Писать финальный результат вычислений.
4. Самому отвечать на свои же вопросы.
5. Использовать символы ½, ², √, ×. Пиши словами: «одна вторая», «в квадрате», «корень», «умножить».
6. Уходить в длинные лекции и списки тем.
7. Повторять вопросы, которые уже задавал.

✅ ЧТО ТЫ ДЕЛАЕШЬ:
1. Задаёшь ТОЛЬКО ОДИН вопрос за раз.
2. Если ученик ответил правильно — подтверждаешь: «Верно! ✅» — и задаёшь следующий вопрос.
3. Если ученик ошибся — НЕ говоришь «неправильно», а задаёшь вопрос, который поможет ему самому увидеть ошибку. 🤔
4. Если ученик почти у цели — НЕ подставляешь числа, а говоришь: «Отлично! А теперь подставь числа сам. Что получится?» 🎯
5. Если ученик просит ответ — вежливо отказываешься: «Давай решим вместе, я помогу тебе самому дойти» 😊

ВАЖНО — КОНТЕКСТ ДИАЛОГА:
Ты ВИДИШЬ всю предыдущую переписку с учеником. ОБЯЗАТЕЛЬНО учитывай её:
- Не повторяй вопросы, которые уже задавал.
- Помни, какую задачу решаете.
- Помни, что ученик уже ответил.
- Продолжай с того места, где остановились.

ПРИМЕР ПРАВИЛЬНОГО ПОВЕДЕНИЯ:
Ученик: «Нужно вообще-то разделить на 2»
Ты: «Верно! ✅ А теперь подставь числа в формулу сам. Что получится?» 🎯
(НЕ пиши «Получится 9 Дж»!)

ПРИМЕР НЕПРАВИЛЬНОГО ПОВЕДЕНИЯ:
❌ «Отлично! Ответ — 9 Дж. Джоули — это единицы кинетической энергии.»
❌ «(½)·2·3^2 = 9 Дж»
❌ «Какой единицей измеряется энергия? Кстати, это Джоули.»
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

# --- КЛАВИАТУРЫ ---
def grade_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="7️⃣ 7 класс", callback_data="grade_7"),
            InlineKeyboardButton(text="9️⃣ 9 класс", callback_data="grade_9"),
        ]
    ])

def control_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💡 Подсказка", callback_data="hint"),
            InlineKeyboardButton(text="❓ Я не понимаю", callback_data="rephrase"),
        ],
        [
            InlineKeyboardButton(text="🔄 Новая задача", callback_data="new_task"),
            InlineKeyboardButton(text="🎓 Сменить класс", callback_data="change_grade"),
        ],
    ])

# --- КОМАНДА /start ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    reset_history(message.from_user.id)

    await message.answer(
        "👋 Привет! Я — «Умный Физик», твой Сократический помощник.\n\n"
        "Я не даю готовых ответов — я помогаю тебе самому дойти до решения задач по механике. "
        "Будем думать вместе! 🤝\n\n"
        "📚 В каком ты классе?",
        reply_markup=grade_keyboard()
    )
    await state.set_state(PhysicsBotStates.choosing_grade)

# --- КОМАНДА /help ---
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "🤖 <b>Как пользоваться ботом «Умный Физик»</b>\n\n"
        "1. Нажми /start и выбери класс (7 или 9).\n"
        "2. Напиши задачу по механике.\n"
        "3. Я задам вопросы — отвечай, и мы вместе дойдём до решения.\n\n"
        "<b>Кнопки управления:</b>\n"
        "💡 <b>Подсказка</b> — небольшая подсказка (не ответ)\n"
        "❓ <b>Я не понимаю</b> — переформулирую вопрос проще\n"
        "🔄 <b>Новая задача</b> — начнём заново\n"
        "🎓 <b>Сменить класс</b> — выбрать 7 или 9 класс\n\n"
        "⚡ Важно: я не решаю задачи за тебя. Я помогаю тебе решить самому!",
        parse_mode="HTML"
    )

# --- ВЫБОР КЛАССА ---
@dp.callback_query(F.data.startswith("grade_"))
async def process_grade_button(callback: types.CallbackQuery, state: FSMContext):
    grade = callback.data.split("_")[1]

    await state.update_data(grade=grade)
    reset_history(callback.from_user.id)

    await callback.message.edit_reply_markup(reply_markup=None)

    await callback.message.answer(
        f"Отлично, ты в {grade} классе! 🎓\n\n"
        "Пришли мне задачу по механике — и давай начнём разбираться. 🚀"
    )
    await state.set_state(PhysicsBotStates.in_dialog)
    await callback.answer()

# --- КНОПКИ УПРАВЛЕНИЯ ---
@dp.callback_query(F.data == "new_task")
async def cb_new_task(callback: types.CallbackQuery, state: FSMContext):
    reset_history(callback.from_user.id)
    await callback.message.answer(
        "🔄 Начинаем новую задачу!\n\n"
        "Пришли мне условие — и разберёмся вместе. 🚀"
    )
    await state.set_state(PhysicsBotStates.in_dialog)
    await callback.answer()

@dp.callback_query(F.data == "change_grade")
async def cb_change_grade(callback: types.CallbackQuery, state: FSMContext):
    reset_history(callback.from_user.id)
    await callback.message.answer(
        "🎓 Хорошо, давай сменим класс. В каком ты классе?",
        reply_markup=grade_keyboard()
    )
    await state.set_state(PhysicsBotStates.choosing_grade)
    await callback.answer()

@dp.callback_query(F.data.in_({"hint", "rephrase"}))
async def cb_hint_or_rephrase(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    user_data = await state.get_data()
    grade = user_data.get("grade", "7")

    if callback.data == "hint":
        instruction = "Ученик просит ПОДСКАЗКУ. Не давай ответ! Дай небольшую подсказку — намекни на формулу или на следующий шаг, но так, чтобы ученик сам подумал. Используй эмодзи 💡."
    else:
        instruction = "Ученик не понимает. Переформулируй свой последний вопрос ПРОЩЕ, другими словами. Не давай ответ. Один вопрос за раз."

    history = get_history(user_id)
    messages_for_api = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages_for_api.extend(list(history))
    messages_for_api.append({"role": "user", "content": f"[{instruction}]"})

    try:
        await callback.message.answer("⏳ Секунду...")
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages_for_api,
            stream=False,
            temperature=0.7,
        )
        bot_reply = response.choices[0].message.content

        history.append({"role": "user", "content": instruction})
        history.append({"role": "assistant", "content": bot_reply})

        await callback.message.answer(bot_reply, reply_markup=control_keyboard())
    except Exception as e:
        logging.error(f"Ошибка при обращении к OpenRouter: {e}")
        await callback.message.answer(
            "Ой, что-то заклинило. 😅 Попробуй ещё раз через минуту."
        )
    await callback.answer()

# --- ОСНОВНОЙ ДИАЛОГ ---
@dp.message(PhysicsBotStates.in_dialog, F.text)
async def handle_dialog(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_message = message.text
    user_data = await state.get_data()
    grade = user_data.get("grade", "7")

    history = get_history(user_id)

    messages_for_api = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages_for_api.extend(list(history))
    messages_for_api.append({
        "role": "user",
        "content": f"Я в {grade} классе. {user_message}"
    })

    try:
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages_for_api,
            stream=False,
            temperature=0.7,
        )
        bot_reply = response.choices[0].message.content

        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": bot_reply})

        await message.answer(bot_reply, reply_markup=control_keyboard())
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
