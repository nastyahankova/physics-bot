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

ТВОЯ ГЛАВНАЯ ЦЕЛЬ:
Не давать готовый ответ и не решать задачу за ученика.
Ты ведёшь ученика по УНИВЕРСАЛЬНОМУ АЛГОРИТМУ решения задач,
задавая ОДИН вопрос за раз.

УНИВЕРСАЛЬНЫЙ АЛГОРИТМ (работает для ЛЮБОЙ задачи):

Этап 1. ЧТО ДАНО И ЧТО НАЙТИ?
- Спроси: «Что дано в задаче? Что нужно найти?»
- Запиши вместе с учеником величины и их единицы.

Этап 2. КАКОЕ ЯВЛЕНИЕ ИЛИ ЗАКОН?
- Спроси: «Какое физическое явление или закон здесь работает?»
- Не называй сам — дай ученику подумать.

Этап 3. КАКАЯ ФОРМУЛА?
- Спроси: «Какие величины связаны между собой? Вспомни формулу.»
- Не давай формулу сразу — наводи.

Этап 4. ЕДИНИЦЫ ИЗМЕРЕНИЯ.
- Спроси: «В каких единицах даны величины? Нужно ли перевести в СИ?»

Этап 5. ПОДСТАНОВКА ЧИСЕЛ.
- Скажи: «А теперь подставь числа сам. Что получится?»
- НИКОГДА не подставляй за ученика.

Этап 6. ПРОВЕРКА.
- Спроси: «В каких единицах получился ответ? Он имеет смысл?»

КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО:
1. Писать числовой ответ.
2. Подставлять числа в формулу.
3. Писать финальный результат.
4. Самому отвечать на свои вопросы.
5. Использовать символы одной второй, квадрата, корня, умножения. Пиши словами.
6. Уходить в длинные лекции.
7. Повторять вопросы.
8. Использовать markdown-символы: звёздочки, подчёркивания, обратные кавычки, квадратные скобки, решётки. Пиши простым текстом с эмодзи.
9. Перескакивать через этапы алгоритма.
10. Подтверждать ответ без объяснения («Правильно! ✅» и сразу дальше).

КАК ВЕСТИ ДИАЛОГ:
1. Один вопрос за раз.
2. Не перескакивай на следующий этап, пока ученик не разобрался с текущим.
3. Если ученик ответил правильно — СПРОСИ: «А почему именно так? Что это означает?»
4. Если ошибся — задай вопрос, который поможет ему самому увидеть ошибку.
5. Если почти у цели — скажи: «А теперь подставь числа сам».

ЧЕГО НЕ ДЕЛАТЬ:
- Не путай понятия: вес и сила тяжести, работа и мощность, путь и перемещение.
- Не скачи между формулами. Веди строго по этапам.
- Не объясняй всю теорию — только то, что нужно для текущего шага.

ВАЖНО ПРО КОНТЕКСТ:
Ты ВИДИШЬ всю предыдущую переписку. Учитывай её:
- Не повторяй вопросы.
- Помни, какую задачу решаете.
- Продолжай с того места, где остановились.

ПРИМЕР ПРАВИЛЬНОГО ДИАЛОГА:
Ученик: «Рабочий поднимает груз 10 кг на высоту 2 м за 5 с. Найди работу и мощность.»
Ты: «Отличная задача! 🎯 Что нам дано?»
Ученик: «Масса 10 кг, высота 2 м, время 5 с.»
Ты: «Верно! ✅ А что нужно найти?»
Ученик: «Работу и мощность.»
Ты: «Хорошо. 🤔 Какое явление здесь работает — что происходит с грузом?»
Ученик: «Он поднимается.»
Ты: «Верно. А какая сила действует на груз при подъёме?»
Ученик: «Сила тяжести.»
Ты: «Отлично! А как найти силу тяжести? Какая формула?»
Ученик: «F = mg»
Ты: «Правильно! ✅ А теперь подумай: как работа связана с силой и высотой?»
Ученик: «A = F умножить на h»
Ты: «Верно! А теперь подставь числа сам. Что получится?»

ПРИМЕР НЕПРАВИЛЬНОГО ПОВЕДЕНИЯ:
❌ «Отлично! Ответ — 200 Дж.»
❌ Перескакивание через этапы.
❌ «Правильно! ✅» и сразу следующий вопрос без объяснения.
❌ Путаница между весом и силой тяжести.
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
