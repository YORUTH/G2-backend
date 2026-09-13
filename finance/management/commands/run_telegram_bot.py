import time
import requests
from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from datetime import datetime, timedelta
import pytz
import json
from rest_framework.test import APIRequestFactory
from finance.views import get_finance_report_data, get_telegram_message_payload
from finance.generate_pdf import generate_finance_pdf
import environ 
ADDIS_TZ = pytz.timezone("Africa/Addis_Ababa")
env = environ.Env(
    DEBUG=(bool, False)
)

# Allowed user IDs for interactive bot commands
def _parse_allowed_user_ids():
    raw = env("TELEGRAM_CHAT_IDS", default=env("TELEGRAM_CHAT_ID", default=""))
    if isinstance(raw, str):
        return [int(x.strip()) for x in raw.split(",") if x.strip().replace("-", "").isdigit()]
    elif isinstance(raw, (list, tuple)):
        return [int(x) for x in raw if str(x).strip().replace("-", "").isdigit()]
    return []

ALLOWED_USER_IDS = _parse_allowed_user_ids()


def get_report_with_pdf(from_date=None, to_date=None):
    """
    Fetch report data, build the Telegram text message and inline keyboard,
    and generate a PDF buffer — all from the same date range.

    Returns (text, inline_keyboard, pdf_buffer, pdf_filename).
    """
    params = {}
    if from_date:
        params['from'] = from_date
    if to_date:
        params['to'] = to_date
        
    data, start_dt_obj, end_dt_obj = get_finance_report_data(params)
    text, inline_keyboard = get_telegram_message_payload(data, start_dt_obj, end_dt_obj)

    # Derive the same date strings used in the text header for the PDF
    from_str = start_dt_obj.strftime("%Y-%m-%d")
    to_str = (end_dt_obj - timedelta(days=1)).strftime("%Y-%m-%d")

    pdf_buf = generate_finance_pdf(
        rows=data["rows"],
        summary=data["summary"],
        from_date_str=from_str,
        to_date_str=to_str,
    )

    is_today = from_str == to_str
    filename = (
        f"Jitu_Finance_Report_{from_str}.pdf"
        if is_today
        else f"Jitu_Finance_Report_{from_str}_to_{to_str}.pdf"
    )

    return text, inline_keyboard, pdf_buf, filename


class Command(BaseCommand):
    help = "Run the Telegram Bot for Finance Reports"

    def handle(self, *args, **options):
        bot_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
        if not bot_token:
            self.stdout.write(self.style.ERROR("TELEGRAM_BOT_TOKEN is not set in settings/env."))
            return

        url = f"https://api.telegram.org/bot{bot_token}/"
        offset = None

        self.stdout.write(self.style.SUCCESS("Telegram bot started. Waiting for messages..."))
        
        while True:
            try:
                # Get updates
                get_url = url + "getUpdates"
                params = {"timeout": 30}
                if offset:
                    params["offset"] = offset
                
                response = requests.get(get_url, params=params, timeout=40)
                if response.status_code == 200:
                    data = response.json()
                    for result in data.get("result", []):
                        offset = result["update_id"] + 1
                        
                        # Handle callback queries from inline buttons
                        if "callback_query" in result:
                            callback = result["callback_query"]
                            chat_id = callback["message"]["chat"]["id"]
                            callback_data = callback["data"]
                            
                            self.stdout.write(f"Received callback: {callback_data}")
                            
                            now_addis = timezone.now().astimezone(ADDIS_TZ)
                            
                            if chat_id not in ALLOWED_USER_IDS:
                                requests.get(url + "answerCallbackQuery", params={"callback_query_id": callback["id"], "text": "⛔ Unauthorized."})
                                continue
                            
                            if callback_data.startswith("val|"):
                                parts = callback_data.split('|')
                                if len(parts) == 3:
                                    _, label, val = parts
                                    # Format value
                                    try:
                                        val_formatted = f"{int(float(val)):,} Birr"
                                    except ValueError:
                                        val_formatted = f"{val} Birr"
                                        
                                    text_msg = f"💵 {label}:\n{val_formatted}"
                                    
                                    # Answer callback quickly to stop loading
                                    requests.get(url + "answerCallbackQuery", params={"callback_query_id": callback["id"]})
                                    
                                    # Send as a new text message
                                    self.send_message(url, chat_id, text_msg)
                                continue
                            
                            if callback_data == "report_today":
                                text, ik, pdf_buf, pdf_name = get_report_with_pdf()
                                self.send_message(url, chat_id, text, reply_markup={"inline_keyboard": ik})
                                self.send_document(url, chat_id, pdf_buf, pdf_name)
                            elif callback_data == "report_week":
                                from_date = (now_addis - timedelta(days=7)).strftime("%Y-%m-%d")
                                to_date = now_addis.strftime("%Y-%m-%d")
                                text, ik, pdf_buf, pdf_name = get_report_with_pdf(from_date, to_date)
                                self.send_message(url, chat_id, text, reply_markup={"inline_keyboard": ik})
                                self.send_document(url, chat_id, pdf_buf, pdf_name)
                            elif callback_data == "report_month":
                                from_date = (now_addis - timedelta(days=30)).strftime("%Y-%m-%d")
                                to_date = now_addis.strftime("%Y-%m-%d")
                                text, ik, pdf_buf, pdf_name = get_report_with_pdf(from_date, to_date)
                                self.send_message(url, chat_id, text, reply_markup={"inline_keyboard": ik})
                                self.send_document(url, chat_id, pdf_buf, pdf_name)
                            
                            # Answer callback query to remove loading state
                            requests.get(url + "answerCallbackQuery", params={"callback_query_id": callback["id"]})
                            continue

                        # Handle text messages
                        if "message" in result and "text" in result["message"]:
                            message = result["message"]
                            chat_id = message["chat"]["id"]
                            text = message["text"].lower().strip()
                            if chat_id not in ALLOWED_USER_IDS:
                                unauthorized_text = f"⛔ *Unauthorized*\n\nYour Telegram ID is: `{chat_id}`\n\nPlease copy this ID and send it to the gym owner so they can grant you access."
                                self.send_message(url, chat_id, unauthorized_text)
                                continue
                                
                            if "report" in text or "finance" in text or "/start" in text:
                                # Send general report with inline buttons for specific date ranges
                                report_text, ik, pdf_buf, pdf_name = get_report_with_pdf()
                                
                                # We add the generic date filter buttons at the bottom of the keyboard
                                ik.append([
                                    {"text": "📅 Last 7 Days", "callback_data": "report_week"},
                                    {"text": "📅 Last 30 Days", "callback_data": "report_month"}
                                ])
                                
                                reply_markup = {
                                    "inline_keyboard": ik
                                }
                                
                                self.send_message(url, chat_id, report_text, reply_markup=reply_markup)
                                self.send_document(url, chat_id, pdf_buf, pdf_name)
                                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error fetching updates: {e}"))
                time.sleep(5)
                
            time.sleep(1)

    def send_message(self, base_url, chat_id, text, reply_markup=None):
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
            
        requests.post(base_url + "sendMessage", data=payload)

    def send_document(self, base_url, chat_id, file_buffer, filename, caption=None):
        """Send a PDF document to a specific chat via Telegram Bot API."""
        file_buffer.seek(0)
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
            data["parse_mode"] = "Markdown"
        files = {"document": (filename, file_buffer, "application/pdf")}
        try:
            requests.post(base_url + "sendDocument", data=data, files=files)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error sending document: {e}"))
