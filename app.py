# app.py

import os
import logging
import tempfile
import re
import requests
import json
import asyncio
from datetime import datetime

from flask import Flask, request
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from PyPDF2 import PdfReader

# --- إعدادات أساسية ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- قراءة المتغيرات السرية من إعدادات Render ---
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
URL = os.getenv('RENDER_EXTERNAL_URL')
OWNER_ID = int(os.getenv('OWNER_ID', 0))

# --- التحقق من وجود المتغيرات ---
if not all([TOKEN, GEMINI_API_KEY, OWNER_ID, URL]):
    logger.critical("FATAL ERROR: One or more environment variables are not set!")
    # في حالة عدم وجود المتغيرات، سيتوقف التطبيق
    # هذا يمنع الأخطاء غير المتوقعة
    exit()

# --- تعريفات وثوابت البوت ---
OWNER_USERNAME = "ll7ddd"
BOT_PROGRAMMER_NAME = "عبدالرحمن حسن"
ASK_NUM_QUESTIONS_FOR_EXTRACTION = range(1)

# --- كل دوال البوت والمنطق الخاص بك (تبقى كما هي) ---
def extract_text_from_pdf(pdf_path: str) -> str:
    try:
        reader = PdfReader(pdf_path)
        text = "".join(page.extract_text() + "\n" for page in reader.pages if page.extract_text())
        return text
    except Exception as e:
        logger.error(f"Error extracting PDF text: {e}")
        return ""

def generate_mcqs_text_blob_with_gemini(text_content: str, num_questions: int, language: str = "English") -> str:
    api_model = "gemini-1.5-flash-latest"
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{api_model}:generateContent?key={GEMINI_API_KEY}"
    text_content = text_content[:20000]

    prompt = f"Generate exactly {num_questions} MCQs in {language} from the text below. STRICT FORMAT: Question: [text]\nA) [text]\nB) [text]\nC) [text]\nD) [text]\nCorrect Answer: [A,B,C, or D]\n---\nText: \"\"\"{text_content}\"\"\""
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.4, "maxOutputTokens": 8192}}
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=300)
        response.raise_for_status()
        candidates = response.json().get("candidates")
        if candidates:
            return candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
        return ""
    except Exception as e:
        logger.error(f"Gemini API error: {e}", exc_info=True)
        return ""

mcq_parsing_pattern = re.compile(r"Question:\s*(.*?)\s*A\)\s*(.*?)\s*B\)\s*(.*?)\s*C\)\s*(.*?)\s*D\)\s*(.*?)\s*Correct Answer:\s*([A-D])", re.IGNORECASE | re.DOTALL)

async def send_single_mcq_as_poll(mcq_text: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    match = mcq_parsing_pattern.search(mcq_text.strip())
    if not match: return False
    try:
        question, opt_a, opt_b, opt_c, opt_d, correct_letter = [g.strip() for g in match.groups()]
        options = [opt_a, opt_b, opt_c, opt_d]
        correct_option_id = {'A': 0, 'B': 1, 'C': 2, 'D': 3}.get(correct_letter.upper())
        if correct_option_id is None: return False
        await context.bot.send_poll(chat_id=update.effective_chat.id, question=question, options=options, type='quiz', correct_option_id=correct_option_id)
        return True
    except Exception as e:
        logger.error(f"Error creating poll: {e}", exc_info=True)
        return False

async def handle_restricted_access(update: Update, context: ContextTypes.DEFAULT_TYPE, feature: str):
    await update.message.reply_text("عذراً، هذا البوت يعمل بشكل حصري لمبرمجه.")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await handle_restricted_access(update, context, "start")
    await update.message.reply_html(rf"مرحباً {update.effective_user.mention_html()}! أرسل ملف PDF.")

async def handle_pdf_for_extraction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id != OWNER_ID:
        await handle_restricted_access(update, context, "PDF Upload")
        return ConversationHandler.END
    
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
        await send_single_mcq_as_poll(mcq, update, context)
        await asyncio.sleep(0.2)
    await update.message.reply_text("انتهت العملية.")
    return ConversationHandler.END

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id != OWNER_ID: return ConversationHandler.END
    await update.message.reply_text("تم إلغاء العملية.")
    context.user_data.clear()
    return ConversationHandler.END

# --- إعداد الخادم والبوت (الهيكلة الجديدة) ---

# 1. إعداد تطبيق البوت وإضافة الأوامر
ptb_application = Application.builder().token(TOKEN).build()
conv_handler = ConversationHandler(
    entry_points=[MessageHandler(filters.Document.PDF, handle_pdf_for_extraction)],
    states={ASK_NUM_QUESTIONS_FOR_EXTRACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, num_questions_received)]},
    fallbacks=[CommandHandler("cancel", cancel_command)],
)
ptb_application.add_handler(CommandHandler("start", start_command))
ptb_application.add_handler(conv_handler)

# 2. إعداد خادم الويب (Flask). هذا المتغير هو ما سيجده Gunicorn.
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is alive!"

@app.route("/webhook", methods=['POST'])
async def webhook():
    await ptb_application.process_update(Update.de_json(request.get_json(force=True), ptb_application.bot))
    return "ok"

# 3. دالة لتشغيل إعداد الـ Webhook مرة واحدة عند بدء التشغيل
async def setup():
    await ptb_application.bot.set_webhook(url=f"{URL}/webhook", allowed_updates=Update.ALL_TYPES)

# 4. تشغيل الإعداد عند بدء الخادم
# هذا الكود يعمل فقط عندما يتم تشغيل التطبيق بواسطة Gunicorn على Render
if __name__ != "__main__":
    asyncio.run(setup())

