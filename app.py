# app.py

import os
import logging
import tempfile
import re
import requests
import json
import time

from flask import Flask, request
import telebot
from telebot.types import Update
from PyPDF2 import PdfReader

# --- إعدادات أساسية ---
logging.basicConfig(level=logging.INFO)
logger = telebot.logger
telebot.logger.setLevel(logging.INFO)

# --- المتغيرات السرية (مضمنة في الكود) ---
TOKEN = "7892395794:AAEUNB1UygFFcCbl7vxoEvH_DFGhjkfOlg8"
GEMINI_API_KEY = "AIzaSyCtGuhftV0VQCWZpYS3KTMWHoLg__qpO3g"
OWNER_ID = 1749717270
URL = os.getenv('RENDER_EXTERNAL_URL')

# --- التحقق من وجود رابط الخادم ---
if not URL:
    logger.critical("FATAL ERROR: RENDER_EXTERNAL_URL not found.")
    exit()

# --- إنشاء البوت وخادم الويب ---
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# --- كل دوال البوت والمنطق الخاص بك (تمت إعادة برمجتها بالمكتبة الجديدة) ---
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

def send_single_mcq_as_poll(mcq_text: str, message):
    match = mcq_parsing_pattern.search(mcq_text.strip())
    if not match: return
    try:
        question, opt_a, opt_b, opt_c, opt_d, correct_letter = [g.strip() for g in match.groups()]
        options = [opt_a, opt_b, opt_c, opt_d]
        correct_option_id = {'A': 0, 'B': 1, 'C': 2, 'D': 3}.get(correct_letter.upper())
        if correct_option_id is not None:
            bot.send_poll(message.chat.id, question, options, type='quiz', correct_option_id=correct_option_id)
    except Exception as e:
        logger.error(f"Error creating poll: {e}")

# --- معالجات الأوامر والرسائل ---

# فلتر للتحقق من أن الرسالة من المالك فقط
def is_owner(message):
    return message.from_user.id == OWNER_ID

@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    json_string = request.get_data().decode('utf-8')
    update = Update.de_json(json_string)
    bot.process_new_updates([update])
    return "!", 200

@app.route('/')
def index():
    bot.remove_webhook()
    time.sleep(1)
    bot.set_webhook(url=f'{URL}/{TOKEN}')
    return "Webhook set successfully!", 200

@bot.message_handler(commands=['start'])
def start(message):
    if not is_owner(message):
        bot.reply_to(message, "عذراً، هذا البوت يعمل بشكل حصري لمبرمجه.")
        return
    bot.reply_to(message, f"مرحباً {message.from_user.first_name}! أرسل ملف PDF.")

@bot.message_handler(content_types=['document'])
def handle_pdf(message):
    if not is_owner(message):
        bot.reply_to(message, "عذراً، لا يمكنك استخدام هذه الميزة.")
        return
    
    if message.document.mime_type != 'application/pdf':
        bot.reply_to(message, "من فضلك أرسل ملف PDF صالح.")
        return

    bot.reply_to(message, "تم استلام ملف PDF. جاري معالجة النص...")
    file_info = bot.get_file(message.document.file_id)
    downloaded_file = bot.download_file(file_info.file_path)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
        temp_pdf.write(downloaded_file)
        pdf_path = temp_pdf.name
    
    text_content = extract_text_from_pdf(pdf_path)
    os.remove(pdf_path)

    if not text_content.strip():
        bot.reply_to(message, "لم أتمكن من استخراج أي نص من الملف.")
        return

    # حفظ النص في متغير مؤقت لاستخدامه في الخطوة التالية
    user_data = {'pdf_text': text_content}
    
    msg = bot.reply_to(message, "النص استخرج. كم سؤال تريد؟")
    bot.register_next_step_handler(msg, num_questions_received, user_data)


def num_questions_received(message, user_data):
    try:
        num_questions = int(message.text)
        if num_questions < 1: raise ValueError
    except (ValueError, TypeError):
        bot.reply_to(message, "الرجاء إرسال رقم صحيح موجب.")
        # نعيد تسجيل الخطوة مرة أخرى
        new_msg = bot.reply_to(message, "كم سؤال تريد؟")
        bot.register_next_step_handler(new_msg, num_questions_received, user_data)
        return

    pdf_text = user_data.get('pdf_text')
    if not pdf_text:
        bot.reply_to(message, "خطأ: نص PDF غير موجود. أعد إرسال الملف.")
        return

    bot.reply_to(message, f"جاري استخراج {num_questions} سؤالاً...")
    mcq_blob = generate_mcqs_text_blob_with_gemini(pdf_text, num_questions)
    if not mcq_blob:
        bot.reply_to(message, "فشل استخراج الأسئلة من Gemini API.")
        return

    mcqs = [mcq.strip() for mcq in re.split(r'\s*---\s*', mcq_blob) if mcq.strip()]
    bot.reply_to(message, f"تم إنشاء {len(mcqs)} سؤال. جاري إرسال الاختبارات...")
    
    for mcq in mcqs:
        send_single_mcq_as_poll(mcq, message)
        time.sleep(0.5) # تأخير بسيط بين كل سؤال لتجنب حظر تليجرام
    
    bot.reply_to(message, "انتهت العملية.")

# هذا الكود يضمن أن يتم إعداد الـ Webhook عند بدء تشغيل الخادم
# Gunicorn سيقوم بتشغيل متغير 'app'
if __name__ != "__main__":
    # إزالة أي webhook قديم وتعيين الجديد
    bot.remove_webhook()
    time.sleep(1)
    bot.set_webhook(url=f'{URL}/{TOKEN}')
    logger.info(f"Webhook set to {URL}/{TOKEN}")


