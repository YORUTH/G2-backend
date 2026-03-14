from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone
from datetime import datetime, timedelta
import pytz

from members.models import MemberProfile, PendingMember, LockerAssignment, PACKAGE_DURATION
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


class FinanceReportView(APIView):
    permission_classes = [IsReceptionist]

    def get(self, request):
        start_date, end_date = _parse_date_range(request.query_params)

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