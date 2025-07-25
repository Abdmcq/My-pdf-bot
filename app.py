# app.py

import os
import logging
import tempfile
import re
import requests
import json
import asyncio

from flask import Flask, request
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    PicklePersistence,
)
from PyPDF2 import PdfReader

# --- إعدادات أساسية ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- المتغيرات السرية (مضمنة في الكود للاستخدام الشخصي) ---
TOKEN = "7892395794:AAEUNB1UygFFcCbl7vxoEvH_DFGhjkfOlg8"
GEMINI_API_KEY = "AIzaSyCtGuhftV0VQCWZpYS3KTMWHoLg__qpO3g"
OWNER_ID = 1749717270

# --- هذا المتغير يجب قراءته من Render دائماً ---
URL = os.getenv('RENDER_EXTERNAL_URL')

# --- التحقق من وجود رابط الخادم ---
if not URL:
    logger.critical("FATAL ERROR: RENDER_EXTERNAL_URL not found. The bot cannot set a webhook.")
    exit()

# --- تعريفات وثوابت البوت ---
ASK_NUM_QUESTIONS_FOR_EXTRACTION = range(1)
PERSISTENCE_FILE = "bot_persistence.pkl"

# --- دوال البوت (تبقى كما هي) ---
def extract_text_from_pdf(pdf_path: str) -> str:
    try:
        reader = PdfReader(pdf_path)
        return "".join(page.extract_text() + "\n" for page in reader.pages if page.extract_text())
    except Exception as e:
        logger.error(f"Error extracting PDF text: {e}")
        return ""

def generate_mcqs_text_blob_with_gemini(text_content: str, num_questions: int) -> str:
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"
    text_content = text_content[:20000]
    prompt = f"Generate exactly {num_questions} MCQs in English from the text below. STRICT FORMAT: Question: [text]\nA) [text]\nB) [text]\nC) [text]\nD) [text]\nCorrect Answer: [A,B,C, or D]\n---\nText: \"\"\"{text_content}\"\"\""
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.4, "maxOutputTokens": 8192}}
    try:
        response = requests.post(api_url, headers={'Content-Type': 'application/json'}, json=payload, timeout=300)
        response.raise_for_status()
        candidates = response.json().get("candidates")
        return candidates[0]['content']['parts'][0]['text'].strip() if candidates else ""
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        return ""

mcq_parsing_pattern = re.compile(r"Question:\s*(.*?)\s*A\)\s*(.*?)\s*B\)\s*(.*?)\s*C\)\s*(.*?)\s*D\)\s*(.*?)\s*Correct Answer:\s*([A-D])", re.IGNORECASE | re.DOTALL)

async def send_single_mcq_as_poll(mcq_text: str, chat_id: int, bot):
    match = mcq_parsing_pattern.search(mcq_text.strip())
    if not match: return
    try:
        question, opt_a, opt_b, opt_c, opt_d, correct_letter = [g.strip() for g in match.groups()]
        options = [opt_a, opt_b, opt_c, opt_d]
        correct_option_id = {'A': 0, 'B': 1, 'C': 2, 'D': 3}.get(correct_letter.upper())
        if correct_option_id is not None:
            await bot.send_poll(chat_id, question, options, type='quiz', correct_option_id=correct_option_id)
    except Exception as e:
        logger.error(f"Error creating poll: {e}")

async def restricted_access_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("عذراً، هذا البوت يعمل بشكل حصري لمبرمجه.")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(rf"مرحباً {update.effective_user.mention_html()}! أرسل ملف PDF.")

async def handle_pdf_for_extraction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    document = update.message.document
    await update.message.reply_text("تم استلام ملف PDF. جاري معالجة النص...")
    pdf_file = await document.get_file()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
        await pdf_file.download_to_drive(custom_path=temp_pdf.name)
        text_content = extract_text_from_pdf(temp_pdf.name)
    os.remove(temp_pdf.name)

    if not text_content.strip():
        await update.message.reply_text("لم أتمكن من استخراج أي نص.")
        return ConversationHandler.END

    context.user_data['pdf_text'] = text_content
    await update.message.reply_text("النص استخرج. كم سؤال تريد؟")
    return ASK_NUM_QUESTIONS_FOR_EXTRACTION

async def num_questions_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        num_questions = int(update.message.text)
        if num_questions < 1: raise ValueError
    except (ValueError, TypeError):
        await update.message.reply_text("الرجاء إرسال رقم صحيح موجب.")
        return ASK_NUM_QUESTIONS_FOR_EXTRACTION

    pdf_text = context.user_data.pop('pdf_text', None)
    if not pdf_text:
        await update.message.reply_text("خطأ: نص PDF غير موجود.")
        return ConversationHandler.END

    await update.message.reply_text(f"جاري استخراج {num_questions} سؤالاً...")
    mcq_blob = generate_mcqs_text_blob_with_gemini(pdf_text, num_questions)
    mcqs = [mcq.strip() for mcq in re.split(r'\s*---\s*', mcq_blob) if mcq.strip()]
    await update.message.reply_text(f"تم إنشاء {len(mcqs)} سؤال. جاري إرسال الاختبارات...")
    
    for mcq in mcqs:
        await send_single_mcq_as_poll(mcq, update.effective_chat.id, context.bot)
        await asyncio.sleep(0.5)
    
    await update.message.reply_text("انتهت العملية.")
    return ConversationHandler.END

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("تم إلغاء العملية.")
    context.user_data.clear()
    return ConversationHandler.END

# --- الهيكلة الجديدة والنهائية ---

# 1. إعداد تطبيق البوت
persistence = PicklePersistence(filepath=PERSISTENCE_FILE)
application = (
    Application.builder()
    .token(TOKEN)
    .persistence(persistence)
    .build()
)

# 2. إضافة الأوامر والمحادثات
owner_filter = filters.User(user_id=OWNER_ID)
conv_handler = ConversationHandler(
    entry_points=[MessageHandler(filters.Document.PDF & owner_filter, handle_pdf_for_extraction)],
    states={
        ASK_NUM_QUESTIONS_FOR_EXTRACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND & owner_filter, num_questions_received)],
    },
    fallbacks=[CommandHandler("cancel", cancel_command, filters=owner_filter)],
)
application.add_handler(CommandHandler("start", start_command, filters=owner_filter))
application.add_handler(conv_handler)
application.add_handler(MessageHandler(filters.ALL & ~owner_filter, restricted_access_handler))

# 3. إعداد خادم الويب (Flask)
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is alive and running!"

@app.route("/webhook", methods=['POST'])
async def webhook():
    await application.update_queue.put(
        Update.de_json(request.get_json(force=True), application.bot)
    )
    return "ok"

# 4. دالة لتشغيل البوت وإعداد الـ Webhook
async def main():
    async with application:
        await application.bot.set_webhook(url=f"{URL}/webhook", allowed_updates=Update.ALL_TYPES)
        await application.start()
        logger.info("Application started, waiting for updates...")
        await asyncio.Event().wait()

# 5. تشغيل الإعداد عند بدء الخادم
if __name__ == "__main__":
    application.run_polling()
else:
    asyncio.run(main())


