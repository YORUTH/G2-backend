from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone
from datetime import datetime, timedelta
import pytz

from members.models import MemberProfile, PendingMember, LockerAssignment
from members.constants import PACKAGE_DURATION
from accounts.permissions import IsReceptionist
from .serializers import FinanceReportSerializer
from .constants import calculate_amount

ADDIS_TZ = pytz.timezone("Africa/Addis_Ababa")


def _parse_date_range(params):
    """
    Returns (start_date, end_date) as aware datetimes in UTC.

    Query param combinations:
      ?from=YYYY-MM-DD&to=YYYY-MM-DD  → full custom range (to is inclusive)
      ?from=YYYY-MM-DD                → single day
      (none)                          → today in Addis Ababa time
    """
    now = timezone.now()
    date_from = params.get("from")
    date_to = params.get("to")

    if date_from and date_to:
        start = timezone.make_aware(
            datetime.strptime(date_from, "%Y-%m-%d"), ADDIS_TZ
        )
        end = timezone.make_aware(
            datetime.strptime(date_to, "%Y-%m-%d"), ADDIS_TZ
        ) + timedelta(days=1)
    elif date_from:
        start = timezone.make_aware(
            datetime.strptime(date_from, "%Y-%m-%d"), ADDIS_TZ
        )
        end = start + timedelta(days=1)
    else:
        today = now.astimezone(ADDIS_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start = today
        end = today + timedelta(days=1)

    return start, end


def get_finance_report_data(params):
    start_date, end_date = _parse_date_range(params)

    rows = []
    active_total = 0
    pending_total = 0
    card_fees_total = 0
    locker_fees_total = 0

    # ── Active members whose membership started in the date range ──────
    active_members = MemberProfile.objects.select_related(
        "user", "locker_assignment"
    ).filter(
        start_date__gte=start_date,
        start_date__lt=end_date,
    )

    for m in active_members:
        is_new = not m.is_renewal

        # Locker fee: only if a locker was assigned on the same calendar
        # day as the membership start (Addis Ababa local date).
        start_local_date = m.start_date.astimezone(ADDIS_TZ).date()
        has_locker = LockerAssignment.objects.filter(
            member=m,
            assigned_at__date=start_local_date,
        ).exists()

        base_amount, card_fee, locker_fee, total = calculate_amount(
            m.package_type, is_new=is_new, has_locker=has_locker
        )

        rows.append({
            "first_name": m.user.first_name,
            "last_name": m.user.last_name,
            "status": "new" if is_new else "renewal",
            "member_state": "active",
            "package_type": m.package_type,
            "months": PACKAGE_DURATION.get(m.package_type, 1),
            "base_amount": base_amount,
            "card_fee": card_fee,
            "locker_fee": locker_fee,
            "total_paid": total,
            "paid_at": m.start_date.astimezone(ADDIS_TZ),
        })

        active_total += total
        card_fees_total += card_fee
        locker_fees_total += locker_fee

    # ── Pending members recorded in the date range ────────────────────
    # Pending members haven't been activated yet so they have no locker.
    pending_members = PendingMember.objects.filter(
        recorded_at__gte=start_date,
        recorded_at__lt=end_date,
    )

    for p in pending_members:
        is_new = p.pending_type == "new"
        base_amount, card_fee, locker_fee, total = calculate_amount(
            p.package_type, is_new=is_new, has_locker=False
        )

        rows.append({
            "first_name": p.first_name,
            "last_name": p.last_name,
            "status": p.pending_type,
            "member_state": "pending",
            "package_type": p.package_type,
            "months": PACKAGE_DURATION.get(p.package_type, 1),
            "base_amount": base_amount,
            "card_fee": card_fee,
            "locker_fee": locker_fee,
            "total_paid": total,
            "paid_at": p.recorded_at.astimezone(ADDIS_TZ),
        })

        pending_total += total
        card_fees_total += card_fee
        # pending members have no locker fee

    grand_total = active_total + pending_total

    data = {
        "rows": rows,
        "summary": {
            "active_total": active_total,
            "pending_total": pending_total,
            "card_fees_total": card_fees_total,
            "locker_fees_total": locker_fees_total,
            "grand_total": grand_total,
        },
    }
    return data, start_date, end_date

class FinanceReportView(APIView):
    permission_classes = [IsReceptionist]

    def get(self, request):
        data, _, _ = get_finance_report_data(request.query_params)
        serializer = FinanceReportSerializer(data)
        return Response(serializer.data)

import requests
from django.conf import settings
import json

def get_telegram_message_payload(data, start_date, end_date):
    summary = data.get("summary", {})
    start_dt = start_date.strftime('%Y-%m-%d')
    end_dt = (end_date - timedelta(days=1)).strftime('%Y-%m-%d')
    
    text = f"📊 *Finance Report*\n"
    if start_dt == end_dt:
        text += f"📅 Date: {start_dt}\n\n"
    else:
        text += f"📅 Period: {start_dt} to {end_dt}\n\n"
        
    text += f"💰 *Grand Total: {summary.get('grand_total', 0):,} Birr*"
    
    inline_keyboard = [
        [
            {"text": "🏋️ Active Members", "callback_data": f"val|Active Members|{summary.get('active_total', 0)}"},
            {"text": "⏳ Pending Members", "callback_data": f"val|Pending Members|{summary.get('pending_total', 0)}"}
        ],
        [
            {"text": "💳 Card Fees", "callback_data": f"val|Card Fees|{summary.get('card_fees_total', 0)}"},
            {"text": "🔐 Locker Fees", "callback_data": f"val|Locker Fees|{summary.get('locker_fees_total', 0)}"}
        ],
        [
            {"text": "🥊 Boxing Fees", "callback_data": f"val|Boxing Fees|{summary.get('boxing_fees_total', 0)}"},
            {"text": "⏱️ Temporal", "callback_data": f"val|Temporal|{summary.get('temporal_total', 0)}"}
        ]
    ]
    return text, inline_keyboard

def send_telegram_message(text):
    bot_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    chat_id = getattr(settings, 'TELEGRAM_CHAT_ID', '')
    if not bot_token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload)
        return response.status_code == 200
    except Exception:
        return False

class SendTelegramReportView(APIView):
    permission_classes = [IsReceptionist]

    def post(self, request):
        data, start_date, end_date = get_finance_report_data(request.data)
        
        text, inline_keyboard = get_telegram_message_payload(data, start_date, end_date)
        
        bot_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
        chat_id = getattr(settings, 'TELEGRAM_CHAT_ID', '')
        if not bot_token or not chat_id:
            return Response({"success": False, "error": "Telegram not configured"}, status=500)
            
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "reply_markup": json.dumps({"inline_keyboard": inline_keyboard})
        }
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        try:
            response = requests.post(url, json=payload)
            if response.status_code == 200:
                return Response({"success": True})
        except Exception:
            pass
            
        return Response({"success": False, "error": "Failed to send telegram message."}, status=500)
