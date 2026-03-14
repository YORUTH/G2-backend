from rest_framework.response import Response
from rest_framework.views import APIView
from members.models import MemberProfile, PendingMember, PACKAGE_DURATION
from django.utils import timezone
from datetime import datetime, timedelta
import pytz

ADDIS_TZ = pytz.timezone("Africa/Addis_Ababa")  

class FinanceReportView(APIView):

    def get(self, request):
        report_type = request.query_params.get("type", "day")
        date_from = request.query_params.get("from")
        date_to = request.query_params.get("to")
        now = timezone.now()
        if date_from and date_to:
            start_naive = datetime.strptime(date_from, "%Y-%m-%d")
            end_naive = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            start_date = timezone.make_aware(start_naive, ADDIS_TZ)
            end_date = timezone.make_aware(end_naive, ADDIS_TZ)
        elif date_from:
            start_naive = datetime.strptime(date_from, "%Y-%m-%d")
            start_date = timezone.make_aware(start_naive, ADDIS_TZ)
            end_date = start_date + timedelta(days=1)
        else:
            start_date = now.astimezone(ADDIS_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = start_date + timedelta(days=1)

        # --- Active members within date range ---
        active_members = MemberProfile.objects.filter(
            start_date__gte=start_date, start_date__lt=end_date
        ).order_by("user_id", "start_date")

        pending_members = PendingMember.objects.filter(
            recorded_at__gte=start_date, recorded_at__lt=end_date
        )

        rows = []
        pending_total = 0
        active_total = 0
        card_fees_total = 0

        # --- Process active members ---
        for m in active_members:
            # Check if this is a renewal (previous profile exists)
            previous_profiles = MemberProfile.objects.filter(
                user=m.user, end_date__lt=m.start_date
            ).order_by("-end_date")

            if previous_profiles.exists():
                last_profile = previous_profiles.first()
                # Grace window: 3 days before expiration counts as renewal
                if last_profile.end_date >= m.start_date - timedelta(days=3):
                    status_str = "renewal"
                    card_fee = 0
                else:
                    status_str = "new"
                    card_fee = 400
            else:
                status_str = "new"
                card_fee = 400

            months = PACKAGE_DURATION[m.package_type]
            base_amount = months * 1000
            total_paid = base_amount + card_fee

            rows.append({
                "first_name": m.user.first_name,
                "last_name": m.user.last_name,
                "status": status_str,
                "member_state": "active",
                "package_type": m.package_type,
                "months": months,
                "base_amount": base_amount,
                "card_fee": card_fee,
                "total_paid": total_paid,
                "paid_at": m.start_date.astimezone(ADDIS_TZ),
            })

            active_total += total_paid
            card_fees_total += card_fee

        # --- Process pending members ---
        for p in pending_members:
            months = PACKAGE_DURATION[p.package_type]
            base_amount = months * 3000
            card_fee = 400 if p.pending_type == "new" else 0
            total_paid = base_amount + card_fee

            rows.append({
                "first_name": p.first_name,
                "last_name": p.last_name,
                "status": p.pending_type,
                "member_state": "pending",
                "package_type": p.package_type,
                "months": months,
                "base_amount": base_amount,
                "card_fee": card_fee,
                "total_paid": total_paid,
                "paid_at": p.recorded_at.astimezone(ADDIS_TZ),
            })

            pending_total += total_paid
            card_fees_total += card_fee

        grand_total = active_total + pending_total

        return Response({
            "rows": rows,
            "summary": {
                "pending_total": pending_total,
                "active_total": active_total,
                "card_fees_total": card_fees_total,
                "grand_total": grand_total
            }
        })
