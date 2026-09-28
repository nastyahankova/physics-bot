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
MODEL_NAME = "qwen/qwen3.8-27b:free"

# --- ПАМЯТЬ ДИАЛОГОВ ---
MAX_HISTORY = 8
user_histories = {}

def get_history(user_id: int):
    if user_id not in user_histories:
        user_histories[user_id] = deque(maxlen=MAX_HISTORY * 2)
    return user_histories[user_id]

def reset_history(user_id: int):
    if user_id in user_histories:
        user_histories[user_id].clear()

# --- ОЧИСТКА ОТВЕТА ОТ СТРАННЫХ СИМВОЛОВ ---
def clean_reply(text: str) -> str:
    """
    Убирает из ответа модели LaTeX и markdown-символы.
    Эмодзи не трогает — они разрешены и полезны.
    """
    if not text:
        return ""

    replacements = {
        "\\times": " умножить на ",
        "\\cdot": " умножить на ",
        "\\frac": " делить ",
        "\\sqrt": " корень из ",
        "\\alpha": " альфа ",
        "\\beta": " бета ",
        "\\pi": " пи ",
        "\\(": "",
        "\\)": "",
        "\\[": "",
        "\\]": "",
        "$$": "",
        "$": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    # Убираем markdown-символы (эмодзи не входят в этот список)
    chars_to_remove = ["*", "_", "`", "[", "]", "#"]
    for ch in chars_to_remove:
        text = text.replace(ch, "")

    # Математические символы → слова
    text = text.replace("×", " умножить на ")
    text = text.replace("÷", " разделить на ")
    text = text.replace("²", " в квадрате")
    text = text.replace("√", " корень из ")
    text = text.replace("½", " одна вторая ")
    text = text.replace("¼", " одна четвёртая ")
    text = text.replace("≈", " примерно ")
    text = text.replace("≠", " не равно ")
    text = text.replace("≤", " меньше или равно ")
    text = text.replace("≥", " больше или равно ")

    while "  " in text:
        text = text.replace("  ", " ")

    return text.strip()

# --- СИСТЕМНЫЙ ПРОМПТ ---
SYSTEM_PROMPT = """
Ты — Сократический помощник по физике для учеников 7 и 9 классов.
Тема: Механика.

ТВОЯ ГЛАВНАЯ ЦЕЛЬ:
Не давать готовый ответ и не решать задачу за ученика.
Ты ведёшь ученика по УНИВЕРСАЛЬНОМУ АЛГОРИТМУ решения задач,
задавая ОДИН вопрос за раз.

УНИВЕРСАЛЬНЫЙ АЛГОРИТМ:

Этап 1. Что дано и что найти?
Этап 2. Какое явление или закон работает?
Этап 3. Какая формула связывает величины?
Этап 4. Проверь единицы измерения (СИ).
Этап 5. Ученик сам подставляет числа.
Этап 6. Проверка результата (единицы, смысл).

КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО:
1. Писать числовой ответ.
2. Подставлять числа в формулу за ученика.
3. Писать финальный результат.
4. Самому отвечать на свои вопросы.
5. Уходить в длинные лекции.
6. Повторять вопросы.
7. Перескакивать через этапы алгоритма.
8. Подтверждать ответ без объяснения («Правильно!» и сразу дальше).

ЗАПРЕЩЕНО ИСПОЛЬЗОВАТЬ:
- Звёздочки, подчёркивания, обратные кавычки, квадратные скобки, решётки.
- LaTeX-команды: \times, \frac, \cdot, \sqrt и любые с обратным слэшем.
- Математические символы: ½, ², ×, ÷, √, ≈.
- Пиши ТОЛЬКО словами: «умножить», «разделить», «в квадрате», «корень».

ВМЕСТО СИМВОЛОВ ВЫДЕЛЕНИЯ ИСПОЛЬЗУЙ ЭМОДЗИ:
✅ — правильный ответ
❌ — ошибка (но не пиши слово «неправильно», задай вопрос)
📝 — что дано / запись
🤔 — подумай
💡 — подсказка
🎯 — цель / следующий шаг
📖 — теория
🚀 — успех
💪 — поддержка
✨ — похвала
⚠️ — внимание
🔢 — числа
📏 — единицы измерения

ВАЖНО: не используй более 2 эмодзи в одном сообщении.

ЗАПРЕЩЕНО ПИСАТЬ АНГЛИЙСКИЕ СЛОВА:
- answer, speed, force, work, time, mass, height, energy.
- Пиши ТОЛЬКО на русском языке.

ЕДИНИЦЫ ИЗМЕРЕНИЯ — ЭТО ОБЯЗАТЕЛЬНО:

1. Когда ученик называет, что дано — ВСЕГДА уточняй единицы:
   «Ты назвал массу 2. В каких единицах? Килограммы?»
   Записывай: «m = 2 кг».

2. Когда ученик называет, что найти — уточняй, в каких единицах
   должен получиться ответ:
   «Найти работу. В каких единицах измеряется работа?»

3. Перед подстановкой чисел — напомни про СИ:
   «Все ли величины в СИ? Может, нужно что-то перевести?»

4. После того как ученик подставил числа — спроси про единицы:
   «Число получилось. А в каких единицах? Джоули, ньютоны, ватты?»

5. Если ученик написал число без единицы измерения —
   НЕ принимай ответ, а мягко спроси:
   «Хорошо, но в каких единицах? Это важно в физике».

6. Если ученик ошибся с единицами — задай вопрос:
   «А в чём измеряется сила? Ньютоны или килограммы?»

ГЛАВНЫЕ ЕДИНИЦЫ:
- масса — килограммы (кг)
- расстояние, путь, высота — метры (м)
- время — секунды (с)
- скорость — метры в секунду (м/с)
- сила — ньютоны (Н)
- работа, энергия — джоули (Дж)
- мощность — ватты (Вт)
- давление — паскали (Па)
- ускорение — метры в секунду в квадрате (м/с²)

ФОРМАТ ЗАПИСИ ВЕЛИЧИН:
Всегда пиши с единицей:
✅ «m = 2 кг», «v = 3 м/с», «h = 2 м»
❌ «m = 2», «v = 3», «h = 2»

ФОРМАТ ОТВЕТА:
- Пиши простым текстом, коротко (3–4 предложения).
- Задавай ТОЛЬКО ОДИН вопрос за раз.

КАК ВЕСТИ ДИАЛОГ:
1. Один вопрос за раз.
2. Не перескакивай на следующий этап, пока ученик не разобрался с текущим.
3. Если ученик ответил правильно — спроси: «А почему именно так?»
4. Если ошибся — задай вопрос, который поможет ему самому увидеть ошибку.
5. Если почти у цели — скажи: «А теперь подставь числа сам».

ЧЕГО НЕ ДЕЛАТЬ:
- Не путай понятия: вес и сила тяжести, работа и мощность.
- Не скачи между формулами.
- Не объясняй всю теорию — только то, что нужно для шага.

ВАЖНО ПРО КОНТЕКСТ:
Ты видишь предыдущую переписку. Учитывай её:
- Не повторяй вопросы.
- Помни, какую задачу решаете.
- Продолжай с того места, где остановились.

ПРИМЕР ПРАВИЛЬНОГО ОТВЕТА:
«📝 Отличная задача! Что нам дано?»

ПРИМЕР НЕПРАВИЛЬНОГО ОТВЕТА:
«Ответ: 200 Дж.»
«v умножить на t = 200»
«Speed is 90 km/h»
«**Что дано?**»
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
        "Я не даю готовых ответов — я помогаю тебе самому дойти до решения задач по механике.\n\n"
        "📚 В каком ты классе?",
        reply_markup=grade_keyboard()
    )
    await state.set_state(PhysicsBotStates.choosing_grade)

# --- КОМАНДА /help ---
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "🤖 Как пользоваться ботом\n\n"
        "1. Нажми /start и выбери класс (7 или 9).\n"
        "2. Напиши задачу по механике.\n"
        "3. Я задам вопросы — отвечай, и мы вместе дойдём до решения.\n\n"
        "Кнопки внизу экрана:\n"
        "💡 Подсказка — небольшая подсказка\n"
        "❓ Я не понимаю — переформулирую вопрос проще\n"
        "📖 Теория — объясню тему простыми словами\n"
        "✅ Задача решена — завершить работу\n"
        "🔄 Новая задача — сбросить диалог\n"
        "🎓 Сменить класс — выбрать 7 или 9 класс\n\n"
        "⚡ Важно: я не решаю задачи за тебя. Я помогаю тебе решить самому."
    )

# --- ВЫБОР КЛАССА ---
@dp.message(F.text == "7️⃣ 7 класс")
async def btn_grade_7(message: types.Message, state: FSMContext):
    await state.update_data(grade="7")
    reset_history(message.from_user.id)
    await message.answer(
        "✅ Отлично, ты в 7 классе!\n\n"
        "📝 Пришли мне задачу по механике — и давай начнём разбираться.",
        reply_markup=control_keyboard()
    )
    await state.set_state(PhysicsBotStates.in_dialog)

@dp.message(F.text == "9️⃣ 9 класс")
async def btn_grade_9(message: types.Message, state: FSMContext):
    await state.update_data(grade="9")
    reset_history(message.from_user.id)
    await message.answer(
        "✅ Отлично, ты в 9 классе!\n\n"
        "📝 Пришли мне задачу по механике — и давай начнём разбираться.",
        reply_markup=control_keyboard()
    )
    await state.set_state(PhysicsBotStates.in_dialog)

# --- КНОПКИ УПРАВЛЕНИЯ ---
@dp.message(F.text == "🔄 Новая задача")
async def btn_new_task(message: types.Message, state: FSMContext):
    reset_history(message.from_user.id)
    await message.answer(
        "🔄 Начинаем новую задачу!\n\n"
        "📝 Пришли мне условие — и разберёмся вместе.",
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
            "Ученик просит подсказку. Не давай ответ! "
            "Дай небольшую подсказку — намекни на формулу или на следующий шаг. "
            "Используй эмодзи 💡. Напомни про единицы измерения."
        )
    elif message.text == "❓ Я не понимаю":
        instruction = (
            "Ученик не понимает. Переформулируй свой последний вопрос проще, "
            "другими словами. Не давай ответ. Один вопрос за раз."
        )
    else:
        instruction = (
            "Ученик просит теорию по теме задачи. Расскажи простыми словами, "
            "что это за явление, от чего зависит, где встречается в жизни. "
            "Не решай задачу, не подставляй числа. 3–4 предложения. "
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
            temperature=0.5,
            max_tokens=300,
        )
        bot_reply = clean_reply(response.choices[0].message.content)

        history.append({"role": "user", "content": instruction})
        history.append({"role": "assistant", "content": bot_reply})

        await message.answer(bot_reply)
    except Exception as e:
        logging.error(f"Ошибка при обращении к OpenRouter: {e}")
        await message.answer(
            "😅 Ой, что-то заклинило. Попробуй ещё раз через минуту."
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
            temperature=0.5,
            max_tokens=300,
        )
        bot_reply = clean_reply(response.choices[0].message.content)

        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": bot_reply})

        await message.answer(bot_reply)
    except Exception as e:
        logging.error(f"Ошибка при обращении к OpenRouter: {e}")
        await message.answer(
            "😅 Ой, кажется, у меня что-то заклинило. Попробуй написать ещё раз через минуту."
        )

# --- ЗАПУСК ---
async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())
