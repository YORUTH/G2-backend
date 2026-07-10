from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status
from django.utils import timezone
from django.db.models import Q
from datetime import datetime, timedelta
import pytz
from members.models import LockerAssignment
from members.models import MemberProfile, PendingMember, PACKAGE_DURATION
from accounts.permissions import IsManagerOrReceptionist
from .serializers import FinanceReportSerializer, TemporalMemberSerializer
from .constants import calculate_amount, LOCKER_FEE
from .models import TemporalMember, TEMPORAL_DAILY_FEE

ADDIS_TZ = pytz.timezone("Africa/Addis_Ababa")


def _parse_date_range(params):
    """
    Returns (start_date, end_date) as aware datetimes in Addis Ababa time.
    end is exclusive.

    Query param combinations:
      ?month=YYYY-MM                   → full month range
      ?from=YYYY-MM-DD&to=YYYY-MM-DD  → full custom range (to is inclusive)
      ?from=YYYY-MM-DD                → single day
      (none)                          → today in Addis Ababa time
    """
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
        "total_paid": LOCKER_FEE,
        "paid_at": la.assigned_at.astimezone(ADDIS_TZ),
    }

def _payment_date(member):
    return member.payment_recorded_at or member.start_date


class FinanceReportView(APIView):
    permission_classes = [IsManagerOrReceptionist]

    def get(self, request):
        start_date, end_date = _parse_date_range(request.query_params)

        rows = []
        active_total = 0
        pending_total = 0
        card_fees_total = 0
        locker_fees_total = 0
        temporal_total = 0
        counted_locker_ids = set()

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

            payment_local = paid_at.astimezone(ADDIS_TZ)
            day_start = payment_local.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            same_day_assignments = [
                la for la in LockerAssignment.objects.filter(member=m)
                if _in_addis_range(la.assigned_at, day_start, day_end)
            ]
            has_locker = bool(same_day_assignments)
            if has_locker:
                counted_locker_ids.update(la.id for la in same_day_assignments)

            base_amount, card_fee, locker_fee, total = calculate_amount(
                m.package_type, is_new=is_new, has_locker=has_locker
            )

            rows.append({
                "first_name": m.user.first_name,
                "last_name": m.user.last_name,
                "status": "new" if is_new else "renewal",
                "member_state": "active",
                "package_type": m.package_type,
                "months": PACKAGE_DURATION[m.package_type],
                "base_amount": base_amount,
                "card_fee": card_fee,
                "locker_fee": locker_fee,
                "total_paid": total,
                "paid_at": paid_at.astimezone(ADDIS_TZ),
            })

            active_total += total
            card_fees_total += card_fee
            locker_fees_total += locker_fee

        # ── Pending members recorded in the date range ────────────────────
        pending_members = PendingMember.objects.filter(
            recorded_at__gte=start_date,
            recorded_at__lt=end_date,
            status="pending",
        )

        for p in pending_members:
            is_new = p.pending_type == "new"
            base_amount, card_fee, locker_fee, total = calculate_amount(
                p.package_type, is_new=is_new, has_locker=False
            )

            rows.append({
                "first_name": p.first_name,
                "last_name": p.last_name,
                "status": "pending",
                "member_state": "pending",
                "pending_type": p.pending_type,
                "package_type": p.package_type,
                "months": PACKAGE_DURATION[p.package_type],
                "base_amount": base_amount,
                "card_fee": card_fee,
                "locker_fee": locker_fee,
                "total_paid": total,
                "paid_at": p.recorded_at.astimezone(ADDIS_TZ),
            })

            pending_total += total
            card_fees_total += card_fee

        # Locker-only payments: any assignment on this Addis day that was not
        # already bundled with a membership row above (works for expired members).
        lookback = start_date - timedelta(days=1)
        lookahead = end_date + timedelta(days=1)
        locker_candidates = (
            LockerAssignment.objects
            .select_related("member__user")
            .filter(
                assigned_at__gte=lookback,
                assigned_at__lt=lookahead,
            )
        )

        for la in locker_candidates:
            if la.id in counted_locker_ids:
                continue
            if not _in_addis_range(la.assigned_at, start_date, end_date):
                continue

            row = _locker_row(la)
            rows.append(row)

            active_total += LOCKER_FEE
            locker_fees_total += LOCKER_FEE

        # ── Temporal daily payments in the date range ─────────────────────
        temporal_payments = TemporalMember.objects.filter(
            paid_at__gte=start_date,
            paid_at__lt=end_date,
        )

        for t in temporal_payments:
            rows.append({
                "first_name": t.full_name,
                "last_name": "",
                "status": "temporal",
                "member_state": "temporal",
                "package_type": "daily",
                "months": 0,
                "base_amount": t.amount,
                "card_fee": 0,
                "locker_fee": 0,
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
                "locker_fees_total": locker_fees_total,
                "grand_total": grand_total,
            },
        }

        serializer = FinanceReportSerializer(data)
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
