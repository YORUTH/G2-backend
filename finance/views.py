from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone
from datetime import datetime, timedelta
import pytz

from members.models import MemberProfile, PendingMember, PACKAGE_DURATION
from accounts.permissions import IsReceptionist
from .serializers import FinanceReportSerializer
from .constants import calculate_amount, LOCKER_FEE

ADDIS_TZ = pytz.timezone("Africa/Addis_Ababa")


def _parse_date_range(params):
    """
    Returns (start_date, end_date) as aware datetimes in UTC.

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


class FinanceReportView(APIView):
    permission_classes = [IsReceptionist]

    def get(self, request):
        start_date, end_date = _parse_date_range(request.query_params)

        rows = []
        active_total = 0
        pending_total = 0
        card_fees_total = 0
        locker_fees_total = 0
        counted_locker_ids = set()

        # ── Active members whose membership started in the date range ──────
        active_members = MemberProfile.objects.select_related(
            "user"
        ).prefetch_related("locker_assignments").filter(
            start_date__gte=start_date,
            start_date__lt=end_date,
        )

        for m in active_members:
            is_new = not m.is_renewal

            start_local = m.start_date.astimezone(ADDIS_TZ)
            day_start = start_local.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            same_day_assignments = LockerAssignment.objects.filter(
                member=m,
                assigned_at__gte=day_start,
                assigned_at__lt=day_end,
            )
            has_locker = same_day_assignments.exists()
            if has_locker:
                counted_locker_ids.update(
                    same_day_assignments.values_list("id", flat=True)
                )

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
                "status": p.pending_type,
                "member_state": "pending",
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
            # pending members have no locker fee

        # ── Standalone locker assignments in the date range ───────────────
        # Picks up locker assignments that were NOT already bundled into an
        # active-member registration row above (e.g. locker assigned to an
        # existing member on a day different from their start_date).
        standalone_lockers = (
            LockerAssignment.objects
            .select_related("member__user")
            .filter(
                assigned_at__gte=start_date,
                assigned_at__lt=end_date,
            )
            .exclude(id__in=counted_locker_ids)
        )

        for la in standalone_lockers:
            rows.append({
                "first_name": la.member.user.first_name,
                "last_name": la.member.user.last_name,
                "status": "locker",
                "member_state": "active",
                "package_type": la.member.package_type,
                "months": 0,
                "base_amount": 0,
                "card_fee": 0,
                "locker_fee": LOCKER_FEE,
                "total_paid": LOCKER_FEE,
                "paid_at": la.assigned_at.astimezone(ADDIS_TZ),
            })

            active_total += LOCKER_FEE
            locker_fees_total += LOCKER_FEE

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

        serializer = FinanceReportSerializer(data)
        return Response(serializer.data)