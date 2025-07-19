# app.py

import os
import logging
import tempfile
import re
import requests
import json
import asyncio
from datetime import datetime

# استيراد مكتبات تيليجرام و فلاسك
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
from flask import Flask, request

# --- إعدادات أساسية ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- قراءة المتغيرات السرية من إعدادات Render ---
# هذا هو الجزء الأكثر أماناً
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
URL = os.getenv('RENDER_EXTERNAL_URL')
OWNER_ID = int(os.getenv('OWNER_ID', 0)) # تحويل الأيدي إلى رقم

# التحقق من وجود المتغيرات الأساسية
if not TOKEN or not GEMINI_API_KEY or not OWNER_ID:
    logger.critical("FATAL ERROR: Environment variables (TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, OWNER_ID) are not set!")
    # في حالة عدم وجود المتغيرات، سيتوقف التطبيق
    exit()

# --- تعريفات وثوابت البوت (من الكود الأصلي) ---
OWNER_USERNAME = "ll7ddd"
BOT_PROGRAMMER_NAME = "عبدالرحمن حسن"
ASK_NUM_QUESTIONS_FOR_EXTRACTION = range(1)
MCQS_FILENAME = "latest_mcqs.json"

# --- كل دوال البوت والمنطق الخاص بك (تبقى كما هي تماماً) ---
# (لقد نسختها بالكامل من ملفك)

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
    max_chars = 20000
    text_content = text_content[:max_chars] if len(text_content) > max_chars else text_content

    prompt = f"""
    Generate exactly {num_questions} MCQs in {language} from the text below.
    STRICT FORMAT (EACH PART ON A NEW LINE):
    Question: [Question text]
    A) [Option A text]
    B) [Option B text]
    C) [Option C text]
    D) [Option D text]
    Correct Answer: [Correct option letter]
    ---
    Text:
    \"\"\"
    {text_content}
    \"\"\"
    """
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.4, "maxOutputTokens": 8192}}
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=300)
        response.raise_for_status()
        generated_text_candidate = response.json().get("candidates")
        if generated_text_candidate:
            return generated_text_candidate[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
        return ""
    except Exception as e:
        logger.error(f"Gemini API error: {e}", exc_info=True)
        return ""

mcq_parsing_pattern = re.compile(
    r"Question:\s*(.*?)\s*\n"
    r"A\)\s*(.*?)\s*\n"
    r"B\)\s*(.*?)\s*\n"
    r"C\)\s*(.*?)\s*\n"
    r"D\)\s*(.*?)\s*\n"
    r"Correct Answer:\s*([A-D])",
    re.IGNORECASE | re.DOTALL
)

async def send_single_mcq_as_poll(mcq_text: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    match = mcq_parsing_pattern.fullmatch(mcq_text.strip())
    if not match: return False
    try:
        question_text, opt_a, opt_b, opt_c, opt_d, correct_letter = [g.strip() for g in match.groups()]
        options = [opt_a, opt_b, opt_c, opt_d]
        correct_option_id = {'A': 0, 'B': 1, 'C': 2, 'D': 3}.get(correct_letter.upper())
        if correct_option_id is None: return False

        await context.bot.send_poll(
            chat_id=update.effective_chat.id,
            question=question_text,
            options=options,
            type='quiz',
            correct_option_id=correct_option_id,
            is_anonymous=True,
        )
        return True
    except Exception as e:
        logger.error(f"Error creating poll: {e}", exc_info=True)
        return False

async def handle_restricted_access(update: Update, context: ContextTypes.DEFAULT_TYPE, feature: str):
    user = update.effective_user
    if not user: return
    logger.warning(f"Restricted access attempt by {user.id} (@{user.username}) for feature: {feature}")
    await update.message.reply_text(f"عذراً، هذا البوت يعمل بشكل حصري لمبرمجه.")

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
        await update.message.reply_text("خطأ: نص PDF غير موجود. أعد إرسال الملف.")
        return ConversationHandler.END

    await update.message.reply_text(f"جاري استخراج {num_questions} سؤالاً...")
    mcq_blob = generate_mcqs_text_blob_with_gemini(pdf_text, num_questions)
    if not mcq_blob:
        await update.message.reply_text("فشل استخراج الأسئلة من Gemini API.")
        return ConversationHandler.END

    mcqs = [mcq.strip() for mcq in re.split(r'\s*---\s*', mcq_blob) if mcq.strip()]
    await update.message.reply_text(f"تم إنشاء {len(mcqs)} سؤال. جاري إرسال الاختبارات...")

    for mcq in mcqs:
        await send_single_mcq_as_poll(mcq, update, context)
        await asyncio.sleep(0.2)
    
    await update.message.reply_text("انتهت العملية.")
    return ConversationHandler.END

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id != OWNER_ID:
        return ConversationHandler.END
    await update.message.reply_text("تم إلغاء العملية.")
    context.user_data.clear()
    return ConversationHandler.END

# --- إعداد الخادم والبوت ---
# هذه هي الدالة الرئيسية الجديدة التي تجمع كل شيء
async def main():
    # إنشاء تطبيق البوت
    application = Application.builder().token(TOKEN).build()

    # تعريف المحادثة الخاصة باستخراج الأسئلة
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Document.PDF, handle_pdf_for_extraction)],
        states={
            ASK_NUM_QUESTIONS_FOR_EXTRACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, num_questions_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
    )

    # إضافة الأوامر والمحادثة إلى البوت
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(conv_handler)

    # إعداد الـ Webhook
    await application.bot.set_webhook(url=f"{URL}/webhook")

    # إنشاء خادم Flask
    flask_app = Flask(__name__)

    @flask_app.route("/")
    def index():
        return "Bot is alive!"

    @flask_app.route("/webhook", methods=['POST'])
    async def webhook():
        await application.process_update(
            Update.de_json(request.get_json(force=True), application.bot)
        )
        return "ok"
    
    # إعادة تطبيق Flask ليتم تشغيله بواسطة Gunicorn
    return flask_app

# هذا السطر مهم جداً لـ Render
# يقوم بتشغيل الدالة الرئيسية ويجعل متغير 'app' متاحاً لـ Gunicorn
if __name__ == "__main__":
    app = asyncio.run(main())

