from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status
from django.utils import timezone
from django.db.models import Q
from datetime import datetime, timedelta
import pytz
from members.models import LockerAssignment
from members.models import MemberProfile, PendingMember, PACKAGE_DURATION
from accounts.permissions import IsManagerOrReceptionist, IsManagerForCustomFinance
from .serializers import FinanceReportSerializer, TemporalMemberSerializer, LockerFinanceReportSerializer
from .constants import calculate_amount, LOCKER_FEE
from .models import TemporalMember, TEMPORAL_DAILY_FEE
from .generate_pdf import generate_finance_pdf

ADDIS_TZ = pytz.timezone("Africa/Addis_Ababa")


def _is_custom_range(params):
    """Return True when the request is explicitly asking for a custom
    date range (via `month`, `from` or `to` parameters).
    """
    if not params:
        return False
    # QueryDict / dict like
    return bool(params.get("month") or params.get("from") or params.get("to"))


def _parse_date_range(params):

    now = timezone.now()
    month = params.get("month")
    date_from = params.get("from")
    date_to = params.get("to")

    if month:
        month_start = timezone.make_aware(
            datetime.strptime(month, "%Y-%m"), ADDIS_TZ
        )
        if month_start.month == 12:
            month_end_naive = datetime(month_start.year + 1, 1, 1)
        else:
            month_end_naive = datetime(month_start.year, month_start.month + 1, 1)
        start = month_start
        end = timezone.make_aware(month_end_naive, ADDIS_TZ)
    elif date_from and date_to:
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


def _in_addis_range(dt, start, end):
    """True when dt falls in [start, end) interpreted in Addis Ababa time."""
    return start <= dt.astimezone(ADDIS_TZ) < end


def _locker_row(la):
    user = la.member.user
    return {
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "status": "locker",
        "member_state": "active",
        "package_type": la.member.package_type or "",
        "months": 0,
        "base_amount": 0,
        "card_fee": 0,
        "locker_fee": LOCKER_FEE,
        "boxing_fee": 0,
        "total_paid": LOCKER_FEE,
        "paid_at": la.assigned_at.astimezone(ADDIS_TZ),
    }

def _payment_date(member):
    return member.payment_recorded_at or member.start_date


def get_finance_report_data(params):
    start_date, end_date = _parse_date_range(params)

    rows = []
    active_total = 0
    pending_total = 0
    card_fees_total = 0
    boxing_fees_total = 0
    temporal_total = 0

    # ── Active members whose payment falls in the date range ───────────
    active_members = MemberProfile.objects.select_related(
        "user"
    ).prefetch_related("locker_assignments").filter(
        Q(
            payment_recorded_at__gte=start_date,
            payment_recorded_at__lt=end_date,
        )
        | Q(
            payment_recorded_at__isnull=True,
            start_date__gte=start_date,
            start_date__lt=end_date,
        )
    ).exclude(
        pendingmember__status="pending",
    )

    for m in active_members:
        is_new = not m.is_renewal
        paid_at = _payment_date(m)

        # Locker payments are handled entirely in the LockerFinanceReportView
        base_amount, card_fee, locker_fee, boxing_fee, total = calculate_amount(
            m.package_type, is_new=is_new, has_locker=False, has_boxing=m.has_boxing
        )

        rows.append({
            "first_name": m.user.first_name,
            "last_name": m.user.last_name,
            "status": "new" if is_new else "renewal",
            "member_state": "active",
            "package_type": m.package_type or "",
            "months": PACKAGE_DURATION.get(m.package_type, 0),
            "base_amount": base_amount,
            "card_fee": card_fee,
            "locker_fee": locker_fee,
            "boxing_fee": boxing_fee,
            "total_paid": total,
            "paid_at": paid_at.astimezone(ADDIS_TZ),
        })

        active_total += total
        card_fees_total += card_fee
        boxing_fees_total += boxing_fee

    # ── Pending members recorded in the date range ────────────────────
    pending_members = PendingMember.objects.filter(
        recorded_at__gte=start_date,
        recorded_at__lt=end_date,
        status="pending",
    )

    for p in pending_members:
        is_new = p.pending_type == "new"
        base_amount, card_fee, locker_fee, boxing_fee, total = calculate_amount(
            p.package_type, is_new=is_new, has_locker=False, has_boxing=p.wants_boxing
        )

        rows.append({
            "first_name": p.first_name,
            "last_name": p.last_name,
            "status": "pending",
            "member_state": "pending",
            "pending_type": p.pending_type,
            "package_type": p.package_type or "",
            "months": PACKAGE_DURATION.get(p.package_type, 0),
            "base_amount": base_amount,
            "card_fee": card_fee,
            "locker_fee": locker_fee,
            "boxing_fee": boxing_fee,
            "total_paid": total,
            "paid_at": p.recorded_at.astimezone(ADDIS_TZ),
        })

        pending_total += total
        card_fees_total += card_fee
        boxing_fees_total += boxing_fee



    # ── Temporal daily payments in the date range ─────────────────────
    temporal_payments = TemporalMember.objects.filter(
        paid_at__gte=start_date,
        paid_at__lt=end_date,
    )

    for t in temporal_payments:
        rows.append({
            "first_name": t.full_name,
            "last_name": f"(Age: {t.age})" if t.age else "",
            "status": "temporal",
            "member_state": "temporal",
            "package_type": "daily",
            "months": 0,
            "base_amount": t.amount,
            "card_fee": 0,
            "locker_fee": 0,
            "boxing_fee": 0,
            "total_paid": t.amount,
            "paid_at": t.paid_at.astimezone(ADDIS_TZ),
        })
        temporal_total += t.amount

    grand_total = active_total + pending_total + temporal_total

    data = {
        "rows": rows,
        "summary": {
            "active_total": active_total,
            "pending_total": pending_total,
            "temporal_total": temporal_total,
            "card_fees_total": card_fees_total,
            "locker_fees_total": 0,  # Now strictly 0 in the main report
            "boxing_fees_total": boxing_fees_total,
            "grand_total": grand_total,
        },
    }
    return data, start_date, end_date

class FinanceReportView(APIView):
    permission_classes = [IsManagerForCustomFinance]

    def get(self, request):
        data, _, _ = get_finance_report_data(request.query_params)
        serializer = FinanceReportSerializer(data)
        return Response(serializer.data)

import requests
from django.conf import settings

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

def _get_chat_ids():
    """Parse configured Telegram chat IDs from settings."""
    raw_chat_ids = getattr(settings, 'TELEGRAM_CHAT_IDS', getattr(settings, 'TELEGRAM_CHAT_ID', ''))
    if isinstance(raw_chat_ids, str):
        return [cid.strip() for cid in raw_chat_ids.split(',') if cid.strip()]
    elif isinstance(raw_chat_ids, (list, tuple)):
        return [str(cid).strip() for cid in raw_chat_ids if str(cid).strip()]
    return []


def send_telegram_message(text):
    bot_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    chat_ids = _get_chat_ids()
    if not bot_token or not chat_ids:
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    success_count = 0
    for cid in chat_ids:
        payload = {
            "chat_id": cid,
            "text": text,
            "parse_mode": "Markdown"
        }
        try:
            response = requests.post(url, json=payload)
            if response.status_code == 200:
                success_count += 1
        except Exception:
            pass

    return success_count > 0


def send_telegram_document(file_buffer, filename, caption=None):
    """Send a PDF document to all configured Telegram chat IDs."""
    bot_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    chat_ids = _get_chat_ids()
    if not bot_token or not chat_ids:
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    success_count = 0
    for cid in chat_ids:
        file_buffer.seek(0)
        data = {"chat_id": cid}
        if caption:
            data["caption"] = caption
            data["parse_mode"] = "Markdown"
        files = {"document": (filename, file_buffer, "application/pdf")}
        try:
            response = requests.post(url, data=data, files=files)
            if response.status_code == 200:
                success_count += 1
        except Exception:
            pass

    return success_count > 0


class SendTelegramReportView(APIView):
    permission_classes = [IsManagerForCustomFinance]

    def post(self, request):
        data, start_date, end_date = get_finance_report_data(request.data)
        text, _ = get_telegram_message_payload(data, start_date, end_date)
        
        # We append a note indicating who triggered it
        username = request.user.first_name or request.user.username
        text += f"\n\n_Sent by {username} from Dashboard_"
        
        msg_ok = send_telegram_message(text)

        # Generate and send the PDF with the same date range
        from_str = start_date.strftime("%Y-%m-%d")
        to_str = (end_date - timedelta(days=1)).strftime("%Y-%m-%d")
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
        doc_ok = send_telegram_document(pdf_buf, filename, caption="📎 Full PDF Report")

        if msg_ok or doc_ok:
            return Response({"success": True, "message": "Message sent successfully!"})
            
        return Response({"success": False, "error": "Failed to send telegram message."}, status=500)


class LockerFinanceReportView(APIView):
    permission_classes = [IsManagerForCustomFinance]

    def get(self, request):
        start_date, end_date = _parse_date_range(request.query_params)

        rows = []
        locker_total = 0

        # All locker assignments in date range
        locker_assignments = LockerAssignment.objects.select_related("member__user").filter(
            assigned_at__gte=start_date,
            assigned_at__lt=end_date,
        )

        for la in locker_assignments:
            row = _locker_row(la)
            rows.append(row)
            locker_total += LOCKER_FEE

        data = {
            "rows": rows,
            "summary": {
                "locker_total": locker_total,
                "grand_total": locker_total,
            },
        }

        serializer = LockerFinanceReportSerializer(data)
        return Response(serializer.data)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Temporal Members — simple list / create / delete
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TemporalMemberListCreateView(APIView):
    """
    GET  → list all temporal daily payments.
    POST → record a new daily payment.  Body: { "full_name": "Abebe Kebede" }
           Amount is always 500 (auto-set).
    """
    permission_classes = [IsManagerOrReceptionist]

    def get(self, request):
        payments = TemporalMember.objects.all()
        serializer = TemporalMemberSerializer(payments, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = TemporalMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)
