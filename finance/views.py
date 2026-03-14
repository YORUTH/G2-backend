from rest_framework.response import Response
from rest_framework.views import APIView
from members.models import MemberProfile, PendingMember, PACKAGE_DURATION
from django.utils import timezone
from datetime import timedelta

class FinanceReportView(APIView):

    def get(self, request):
        report_type = request.query_params.get("type", "day")
        date_str = request.query_params.get("date")
        now = timezone.now()

        # --- Filter dates ---
        if report_type == "day" and date_str:
            start_date = timezone.datetime.strptime(date_str, "%Y-%m-%d")
            start_date = timezone.make_aware(start_date)
            end_date = start_date + timedelta(days=1)
        elif report_type == "day":
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = start_date + timedelta(days=1)
        # Week / Month filtering can be added here

        # --- Active members ---
        active_members = MemberProfile.objects.filter(
            start_date__gte=start_date, start_date__lt=end_date
        )

        # --- Pending members ---
        pending_members = PendingMember.objects.filter(
            recorded_at__gte=start_date, recorded_at__lt=end_date
        )

        rows = []
        pending_total = 0
        active_total = 0
        card_fees_total = 0

        # --- Active members rows ---
        for m in active_members:
            months = PACKAGE_DURATION[m.package_type]
            base_amount = months * 3000  # k=1000 per month for example
            card_fee = 400  # one-time fee
            total_paid = base_amount + card_fee

            rows.append({
                "first_name": m.user.first_name,
                "last_name": m.user.last_name,
                "status": "new" if m.start_date.date() == now.date() else "renewal",
                "member_state": "active",
                "package_type": m.package_type,
                "months": months,
                "base_amount": base_amount,
                "card_fee": card_fee,
                "total_paid": total_paid,
                "paid_at": m.start_date,
            })

            active_total += total_paid
            card_fees_total += card_fee

        # --- Pending members rows ---
        for p in pending_members:
            months = PACKAGE_DURATION[p.package_type]
            base_amount = months * 3000  # k per month
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
                "paid_at": p.recorded_at,
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
