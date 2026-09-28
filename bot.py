import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from openai import AsyncOpenAI

# --- КОНФИГУРАЦИЯ ---
# Токен берем из переменных окружения (так безопаснее)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Ключ OpenRouter
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Подключаемся к OpenRouter
client = AsyncOpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1"
)

# Наш системный промпт (оставляем тот же, что обсуждали)
SYSTEM_PROMPT = """
Ты — Сократический помощник по физике для учеников 7 и 9 классов.
Тема: Механика.

ТВОЯ ГЛАВНАЯ ЦЕЛЬ:
Не давать готовый ответ и не решать задачу за ученика.
Ты помогаешь ученику САМОМУ дойти до решения через наводящие вопросы.

ПРАВИЛА:
1. Никогда не пиши ответ задачи и не подставляй числа в формулы за ученика.
2. Задавай ТОЛЬКО ОДИН вопрос за раз.
3. Если ученик отвечает правильно — подтверди и задай следующий вопрос.
4. Если ученик ошибается — не говори «неправильно», а задай вопрос, который поможет ему самому увидеть ошибку.
5. Если ученик не знает, с чего начать — спроси: «Что дано в задаче? Что нужно найти?»
6. Если ученик просит формулу — не давай сразу, а спроси: «Какие величины связаны с этой задачей? Что ты о них знаешь?»
7. Если ученик просит готовый ответ — вежливо откажись: «Давай решим вместе, я помогу тебе самому дойти до ответа».
8. Говори простым языком, как учитель, который хочет помочь, а не экзаменовать.

ЛОГИКА ВЕДЕНИЯ ПО ЗАДАЧЕ:
Шаг 1. Выяснить, что дано и что нужно найти.
Шаг 2. Определить, какое физическое явление или закон здесь работает.
Шаг 3. Вспомнить формулу (через вопросы, а не подсказку).
Шаг 4. Проверить единицы измерения (перевести в СИ, если нужно).
Шаг 5. Подставить числа (ученик сам).
Шаг 6. Проверить результат (размерность, здравый смысл).
"""

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=storage)

class PhysicsBotStates(StatesGroup):
    choosing_grade = State()
    in_dialog = State()

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Я Сократический помощник по физике. "
        "Я не даю готовых ответов, а помогаю тебе самому дойти до решения задач по механике.\n\n"
        "В каком ты классе? Напиши «7» или «9»."
    )
    await state.set_state(PhysicsBotStates.choosing_grade)

@dp.message(PhysicsBotStates.choosing_grade)
async def process_grade_choice(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text in ["7", "9"]:
        await state.update_data(grade=text)
        await message.answer(f"Отлично, ты в {text} классе. Пришли мне задачу по механике, и давай начнём.")
        await state.set_state(PhysicsBotStates.in_dialog)
    else:
        await message.answer("Пожалуйста, напиши только «7» или «9».")

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
        # Используем бесплатную модель. Можно попробовать "deepseek/deepseek-r1:free"
        # Если она не работает, можно заменить на "google/gemma-3-27b-it:free"
        response = await client.chat.completions.create(
            model="deepseek/deepseek-r1:free", 
            messages=messages_for_api,
            stream=False,
            temperature=0.7,
        )
        bot_reply = response.choices[0].message.content
        await message.answer(bot_reply)
    except Exception as e:
        logging.error(f"Ошибка при обращении к OpenRouter: {e}")
        await message.answer("Извини, произошла ошибка на стороне ИИ. Попробуй ещё раз.")

async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())