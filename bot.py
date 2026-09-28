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
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
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

КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО:
1. Писать числовой ответ (например, «9 Дж», «15 Н», «3 м/с», «Ответ: ...»).
2. Подставлять числа в формулу за ученика.
3. Писать финальный результат вычислений.
4. Самому отвечать на свои же вопросы.
5. Использовать символы одной второй, квадрата, корня, умножения. Пиши словами: «одна вторая», «в квадрате», «корень», «умножить».
6. Уходить в длинные лекции и списки тем.
7. Повторять вопросы, которые уже задавал.
8. Использовать markdown-символы: звёздочки, подчёркивания, обратные кавычки, квадратные скобки, решётки. Пиши простым текстом. Для выделения используй ЭМОДЗИ, а не символы.

ЧТО ТЫ ДЕЛАЕШЬ:
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

ПРИМЕРЫ ХОРОШИХ НАВОДЯЩИХ ВОПРОСОВ:

Задача на работу и энергию:
✅ «Какая сила действует на тело при подъёме?»
✅ «От чего зависит работа?»
✅ «Какая формула связывает работу, силу и путь?»

НЕ СПРАШИВАЙ:
❌ «Какую силу он преодолевает?» — это очевидно
❌ «Что такое работа?» — слишком обще
❌ «А что нам дано?» — если ученик уже сказал

Задача на кинетическую энергию:
✅ «От чего зависит энергия движущегося тела?»
✅ «Если тело остановится, куда денется энергия?»

Задача на скорость:
✅ «Что показывает скорость?»
✅ «Как связаны путь, время и скорость?»

ОБЩЕЕ ПРАВИЛО:
Наводящий вопрос должен быть конкретным, неочевидным и не повторять то, что уже сказал ученик.

ПРИМЕР ПРАВИЛЬНОГО ПОВЕДЕНИЯ:
Ученик: «Нужно разделить на 2»
Ты: «Верно! ✅ А теперь подставь числа в формулу сам. Что получится?» 🎯

ПРИМЕР НЕПРАВИЛЬНОГО ПОВЕДЕНИЯ:
❌ «Отлично! Ответ — 9 Дж. Джоули — это единицы кинетической энергии.»
❌ «Получится 9 Дж»
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
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="7️⃣ 7 класс"), KeyboardButton(text="9️⃣ 9 класс")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

def control_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💡 Подсказка"), KeyboardButton(text="❓ Я не понимаю")],
            [KeyboardButton(text="📖 Теория"), KeyboardButton(text="✅ Задача решена")],
            [KeyboardButton(text="🔄 Новая задача"), KeyboardButton(text="🎓 Сменить класс")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

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
        "🤖 Как пользоваться ботом «Умный Физик»\n\n"
        "1. Нажми /start и выбери класс (7 или 9).\n"
        "2. Напиши задачу по механике.\n"
        "3. Я задам вопросы — отвечай, и мы вместе дойдём до решения.\n\n"
        "Кнопки управления:\n"
        "💡 Подсказка — небольшая подсказка (не ответ)\n"
        "❓ Я не понимаю — переформулирую вопрос проще\n"
        "📖 Теория — объясню тему простыми словами\n"
        "✅ Задача решена — завершить работу\n"
        "🔄 Новая задача — сбросить диалог\n"
        "🎓 Сменить класс — выбрать 7 или 9 класс\n\n"
        "⚡ Важно: я не решаю задачи за тебя. Я помогаю тебе решить самому!"
    )

# --- ВЫБОР КЛАССА ---
@dp.message(F.text == "7️⃣ 7 класс")
async def btn_grade_7(message: types.Message, state: FSMContext):
    await state.update_data(grade="7")
    reset_history(message.from_user.id)
    await message.answer(
        "Отлично, ты в 7 классе! 🎓\n\n"
        "Пришли мне задачу по механике — и давай начнём разбираться. 🚀",
        reply_markup=control_keyboard()
    )
    await state.set_state(PhysicsBotStates.in_dialog)

@dp.message(F.text == "9️⃣ 9 класс")
async def btn_grade_9(message: types.Message, state: FSMContext):
    await state.update_data(grade="9")
    reset_history(message.from_user.id)
    await message.answer(
        "Отлично, ты в 9 классе! 🎓\n\n"
        "Пришли мне задачу по механике — и давай начнём разбираться. 🚀",
        reply_markup=control_keyboard()
    )
    await state.set_state(PhysicsBotStates.in_dialog)

# --- КНОПКИ УПРАВЛЕНИЯ ---
@dp.message(F.text == "🔄 Новая задача")
async def btn_new_task(message: types.Message, state: FSMContext):
    reset_history(message.from_user.id)
    await message.answer(
        "🔄 Начинаем новую задачу!\n\n"
        "Пришли мне условие — и разберёмся вместе. 🚀",
        reply_markup=control_keyboard()
    )
    await state.set_state(PhysicsBotStates.in_dialog)

@dp.message(F.text == "🎓 Сменить класс")
async def btn_change_grade(message: types.Message, state: FSMContext):
    reset_history(message.from_user.id)
    await message.answer(
        "🎓 Хорошо, давай сменим класс. В каком ты классе?",
        reply_markup=grade_keyboard()
    )
    await state.set_state(PhysicsBotStates.choosing_grade)

@dp.message(F.text == "✅ Задача решена")
async def btn_done(message: types.Message, state: FSMContext):
    reset_history(message.from_user.id)
    await message.answer(
        "🎉 Отлично! Рад был помочь.\n\n"
        "Когда понадобится моя помощь — просто нажми /start! 👋",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.clear()

@dp.message(F.text.in_({"💡 Подсказка", "❓ Я не понимаю", "📖 Теория"}))
async def btn_control(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_data = await state.get_data()
    grade = user_data.get("grade", "7")

    if message.text == "💡 Подсказка":
        instruction = (
            "Ученик просит ПОДСКАЗКУ. Не давай ответ! "
            "Дай небольшую подсказку — намекни на формулу или на следующий шаг, "
            "но так, чтобы ученик сам подумал. Используй эмодзи 💡."
        )
    elif message.text == "❓ Я не понимаю":
        instruction = (
            "Ученик не понимает. Переформулируй свой последний вопрос ПРОЩЕ, "
            "другими словами. Не давай ответ. Один вопрос за раз."
        )
    else:  # Теория
        instruction = (
            "Ученик просит ТЕОРИЮ по теме задачи. Расскажи простыми словами, "
            "что это за явление или величина, от чего зависит, где встречается в жизни. "
            "НЕ решай задачу и НЕ подставляй числа. Не уходи в длинную лекцию — 4–5 предложений. "
            "В конце спроси: «Теперь понятнее? Продолжим задачу?» 📖"
        )

    history = get_history(user_id)
    messages_for_api = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages_for_api.extend(list(history))
    messages_for_api.append({"role": "user", "content": f"[{instruction}]"})

    try:
        await message.answer("⏳ Секунду...")
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages_for_api,
            stream=False,
            temperature=0.7,
        )
        bot_reply = response.choices[0].message.content

        history.append({"role": "user", "content": instruction})
        history.append({"role": "assistant", "content": bot_reply})

        await message.answer(bot_reply)
    except Exception as e:
        logging.error(f"Ошибка при обращении к OpenRouter: {e}")
        await message.answer(
            "Ой, что-то заклинило. 😅 Попробуй ещё раз через минуту."
        )

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
