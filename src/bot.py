import os
import sys
import asyncio
from fastapi import FastAPI
from uvicorn import Server, Config
from dotenv import load_dotenv
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.ext import CallbackQueryHandler
from telegram.constants import ParseMode
import random
from datetime import datetime, timedelta
from test_data import TEST

# Добавляем путь к src в PYTHONPATH
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

app = FastAPI()
bot_app = Application.builder().token(TOKEN).build()

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
RAILWAY_PUBLIC_URL = os.getenv('RAILWAY_STATIC_URL', os.getenv('RAILWAY_PUBLIC_URL'))
WEBHOOK_URL = os.getenv('RAILWAY_PUBLIC_URL') + '/webhook' if 'RAILWAY_PUBLIC_URL' in os.environ else None
# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Конфигурация теста
ADMIN_CHAT_ID = 705993924  # Замените на ваш chat_id (можно узнать у @userinfobot)

# Глобальные переменные
user_sessions = {}
message_ids_to_delete = {}


class UserSession:
    class UserSession:
        def reset(self):
            self.state = "awaiting_lastname"
            self.lastname = ""
            self.firstname = ""
            self.group = ""
            self.test_start_time = None
            self.current_question = 0
            self.score = 0
            self.last_messages = []
            # Перемешиваем вопросы заново
            self.questions = TEST["questions"].copy()
            if TEST["shuffle_questions"]:
                random.shuffle(self.questions)
            if TEST["shuffle_answers"]:
                for q in self.questions:
                    answers = q['answers']
                    correct = q['correct']
                    correct_answer = answers[correct]
                    random.shuffle(answers)
                    q['correct'] = answers.index(correct_answer)


    def __init__(self, user_id):
        self.user_id = user_id
        self.state = "awaiting_lastname"
        self.lastname = ""
        self.firstname = ""
        self.group = ""
        self.test_start_time = None
        self.current_question = 0
        self.score = 0
        self.questions = TEST["questions"].copy()
        self.last_messages = []  # Для удаления предыдущих сообщений

        # Перемешивание вопросов и ответов
        if TEST["shuffle_questions"]:
            random.shuffle(self.questions)
        if TEST["shuffle_answers"]:
            for q in self.questions:
                answers = q['answers']
                correct = q['correct']
                # Сохраняем текст правильного ответа
                q['correct_text'] = answers[correct]
                random.shuffle(answers)
                # Обновляем индекс правильного ответа
                q['correct'] = answers.index(q['correct_text'])


async def delete_previous_messages(update: Update, context: ContextTypes.DEFAULT_TYPE, session):
    """Удаляет предыдущие сообщения в чате"""
    for msg_id in session.last_messages:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg_id)
        except Exception as e:
            logging.error(f"Ошибка при удалении сообщения: {e}")
    session.last_messages = []


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("По просьбе Алексея Александровича направляю вам ссылку на опрос и прошу пройти до прохождения теста\nhttps://docs.google.com/forms/d/1lcuiOglXuiSXYhlklAs2dKgpUexJATvvlAMjP89fC-Y/viewform?edit_requested=true",parse_mode=ParseMode.HTML)

    user_id = update.message.from_user.id

    # Создаем или сбрасываем сессию
    if user_id in user_sessions:
        user_sessions[user_id].reset()  # Полный сброс
    else:
        user_sessions[user_id] = UserSession(user_id)

    msg = await update.message.reply_text(
        "Добро пожаловать в систему тестирования!\n"
        "Введите вашу фамилию:"
    )
    user_sessions[user_id].last_messages.append(msg.message_id)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text.strip()  # Вот где должна быть получена переменная text

    if user_id not in user_sessions:
        await update.message.reply_text("Пожалуйста, начните с команды /start")
        return

    session = user_sessions[user_id]
    session.last_messages.append(update.message.message_id)

    if session.state == "awaiting_lastname":
        session.lastname = text
        session.state = "awaiting_firstname"
        await delete_previous_messages(update, context, session)
        msg = await update.message.reply_text("Введите ваше имя:")
        session.last_messages.append(msg.message_id)

    elif session.state == "awaiting_firstname":
        session.firstname = text
        session.state = "awaiting_group"
        await delete_previous_messages(update, context, session)
        msg = await update.message.reply_text("Введите вашу группу:")
        session.last_messages.append(msg.message_id)

    elif session.state == "awaiting_group":
        session.group = text
        session.state = "testing"
        session.test_start_time = datetime.now()
        await delete_previous_messages(update, context, session)
        await send_question(update, context, session)

    elif session.state == "testing":
        current_question = session.questions[session.current_question]

        # Получаем текст правильного ответа
        correct_answer = current_question['answers'][current_question['correct']]

        # Сравниваем введенный ответ с правильным (без учета регистра)
        if text.lower() == correct_answer.lower():
            session.score += 1

        session.current_question += 1
        await delete_previous_messages(update, context, session)

        if session.current_question >= len(session.questions):
            await finish_test(update, context, session)
        else:
            await send_question(update, context, session)


from telegram import InlineKeyboardButton, InlineKeyboardMarkup


async def send_question(update: Update, context: ContextTypes.DEFAULT_TYPE, session):
    """Отправляет вопрос с кнопками вариантов ответов"""
    try:
        question = session.questions[session.current_question]
        time_left = (session.test_start_time + timedelta(minutes=TEST["time_limit"]) - datetime.now())

        if time_left.total_seconds() <= 0:
            await finish_test(update, context, session)
            return

        time_str = f"[{time_left.seconds // 60:02d}:{time_left.seconds % 60:02d}]"
        question_text = f"{time_str} Вопрос {session.current_question + 1}/{len(session.questions)}:\n{question['question']}"

        # Создаем кнопки для вариантов ответов
        keyboard = []
        for idx, answer in enumerate(question['answers']):
            callback_data = f"answer_{session.current_question}_{idx}"
            keyboard.append([InlineKeyboardButton(answer, callback_data=callback_data)])

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Удаляем предыдущее сообщение с вопросом (если есть)
        if hasattr(session, 'last_message_id'):
            try:
                await context.bot.delete_message(chat_id=session.user_id, message_id=session.last_message_id)
            except:
                pass

        # Отправляем новый вопрос
        msg = await context.bot.send_message(
            chat_id=session.user_id,
            text=f"```\n{question_text}\n```",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=reply_markup
        )
        session.last_message_id = msg.message_id

    except Exception as e:
        logging.error(f"Ошибка в send_question: {e}")
        await context.bot.send_message(
            chat_id=session.user_id,
            text="Произошла ошибка. Начните тест заново /start"
        )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in user_sessions:
        await query.edit_message_text("Сессия истекла. Начните с /start")
        return

    session = user_sessions[user_id]

    try:
        _, question_idx, answer_idx = query.data.split('_')
        question_idx = int(question_idx)
        answer_idx = int(answer_idx)

        if question_idx != session.current_question:
            return

        # Проверяем ответ
        current_question = session.questions[session.current_question]
        if answer_idx == current_question['correct']:
            session.score += 1

        session.current_question += 1

        # Удаляем сообщение с кнопками
        await query.delete_message()

        if session.current_question >= len(session.questions):
            await finish_test(update, context, session)
        else:
            await send_question(update, context, session)

    except Exception as e:
        logging.error(f"Ошибка в button_handler: {e}")
        await query.edit_message_text("Произошла ошибка. Начните тест заново /start")

async def finish_test(update: Update, context: ContextTypes.DEFAULT_TYPE, session):
    """Завершает тест и отправляет результаты"""
    time_taken = datetime.now() - session.test_start_time
    score_percent = int((session.score / len(session.questions)) * 100)

    # Формируем результат для студента
    student_result = (
        f"Тест завершен!\n"
        f"Результаты для {session.lastname} {session.firstname} ({session.group}):\n"
        f"Правильных ответов: {session.score}/{len(session.questions)}\n"
        f"Процент: {score_percent}%\n"
        f"Затраченное время: {time_taken.seconds // 60:02d}:{time_taken.seconds % 60:02d}"
    )

    # Формируем компактный результат для администратора
    admin_result = f"Группа {session.group}\n {session.lastname} {session.firstname}\n Результат : {session.score}/{len(session.questions)} "

    try:
        # Отправляем результаты студенту
        await context.bot.send_message(
            chat_id=session.user_id,
            text=f"```\n{student_result}\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )

        # Отправляем результаты администратору
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=admin_result
        )
    except Exception as e:
        logging.error(f"Ошибка при отправке результатов: {e}")
    finally:
        # Очищаем сессию
        if session.user_id in user_sessions:
            del user_sessions[session.user_id]

async def setup_webhook(application):
    await application.bot.set_webhook(
        url=WEBHOOK_URL,
        drop_pending_updates=True
    )    

@app.post("/webhook")
async def webhook(update: Update):
    await bot_app.update_queue.put(update)
    return {"status": "ok"}

async def setup():
    await bot_app.initialize()
    await bot_app.bot.set_webhook(
        url=WEBHOOK_URL,
        drop_pending_updates=True
    )
    print(f"Webhook установлен на {WEBHOOK_URL}")

    server = Server(Config(
        app=app,
        host="0.0.0.0",
        port=int(os.getenv('PORT', 8000))
    )
    await server.serve()
    
def main():
    application = Application.builder().token("7724050180:AAFI_yWUzKQDz_Kzygkle-MuAy5Z8jQ3rrE").build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_handler))  # Добавляем обработчик кнопок
    if WEBHOOK_URL:
        # Настройка для Railway
        PORT = int(os.getenv('PORT', 8000))
               
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=WEBHOOK_URL,
            secret_token=TOKEN,
        )
        asyncio.get_event_loop().run_until_complete(setup_webhook(application))
    else:
        # Локальный режим
        application.run_polling()
    
if __name__ == '__main__':
    import asyncio
    bot_app.add_handler(CommandHandler("start", start))
    asyncio.run(setup())

