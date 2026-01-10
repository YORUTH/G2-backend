from rest_framework import generics
from .models import MemberProfile
from .serializers import MemberCreateSerializer, TrainerUpdateSerializer, MemberUpdateSerializer
from accounts.permissions import IsReceptionist, IsTrainer, IsMember,CanViewMemberDetail ,CanViewMembersList
from rest_framework.response import Response
from rest_framework import status
from django.db.models import F, ExpressionWrapper, fields, Q
from django.db.models.functions import Greatest
from django.db import connection, ProgrammingError
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter
from rest_framework.views import APIView
from .serializers import CheckInSerializer
from django.utils import timezone
from .models import CheckIn
from calendar import monthrange
from .serializers import RecentCheckInSerializer
from datetime import timedelta
from .filters import MemberProfileFilter
from rest_framework.pagination import PageNumberPagination
from datetime import datetime
from calendar import monthrange
from .serializers import MemberImageAttachSerializer
from django.utils import timezone
from django.contrib.auth import get_user_model

User = get_user_model()


today = timezone.now().date()
class MemberListPagination(PageNumberPagination):
    page_size = 20             
    page_size_query_param = "page_size"  
    max_page_size = 30

class CheckInPagination(PageNumberPagination):
    page_size = 20          
    page_size_query_param = "page_size"
    max_page_size = 30

from staff.models import TrainerDietplan
from staff.serializers import TrainerDietplanSerializer

class DietplanListView(generics.ListAPIView):
    queryset = TrainerDietplan.objects.all()
    serializer_class = TrainerDietplanSerializer

class TrainerUpdateMemberView(generics.RetrieveUpdateAPIView):
    queryset = MemberProfile.objects.all()
    serializer_class = TrainerUpdateSerializer
    permission_classes = [IsTrainer]
    http_method_names = ["get","patch"]
    
class MemberCreateView(generics.CreateAPIView):
    queryset = MemberProfile.objects.all()
    serializer_class = MemberCreateSerializer
    permission_classes = [IsReceptionist]
    http_method_names = ["post"]
    def get(self, request, *args, **kwargs):
        return Response({"detail": "Use POST to create members"}, status=status.HTTP_405_METHOD_NOT_ALLOWED)
    

class ListMembersView(generics.ListAPIView):
    serializer_class = MemberUpdateSerializer
    permission_classes = [CanViewMembersList]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    http_method_names = ["get"]
    filterset_class = MemberProfileFilter  
    filterset_fields = ["phone_number", "first_name", "last_name", "barcode", "package_type", "start_date", "end_date", "left_days"]
    ordering_fields = ["start_date", "end_date", "left_days"]
    ordering = ["-start_date"]
    pagination_class = MemberListPagination
    
    def get_queryset(self):
        now = timezone.now()
        
        User.objects.filter(
            profile__end_date__lt=now,
            is_active=True
        ).update(is_active=False)
        
        queryset = MemberProfile.objects.all().order_by("id")

        search_term = self.request.query_params.get("search", "").strip()
        if search_term:
            queryset = self.apply_fuzzy_search(queryset, search_term)

        queryset = queryset.annotate(
            left_days=ExpressionWrapper(
                F("end_date") - now,
                output_field=fields.DurationField(),
            )
        )

        return queryset


    def apply_fuzzy_search(self, queryset, term):
        """Fuzzy search: use trigram similarity on Postgres; fallback to icontains elsewhere."""
        if not term:
            return queryset

        if connection.vendor == "postgresql":
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
                    has_trgm = cursor.fetchone() is not None

                if has_trgm:
                    from django.contrib.postgres.search import TrigramSimilarity

                    queryset = queryset.annotate(
                        similarity=Greatest(
                            TrigramSimilarity("user__first_name", term),
                            TrigramSimilarity("user__last_name", term),
                            TrigramSimilarity("user__phone_number", term),
                            TrigramSimilarity("barcode", term),
                        )
                    ).filter(similarity__gt=0.2).order_by("-similarity")
                    return queryset
            except ProgrammingError:
                # Extension not available or not installed; fall back
                pass
            except Exception:
                # Any other issue: fall back to icontains
                pass

        return queryset.filter(
            Q(user__first_name__icontains=term)
            | Q(user__last_name__icontains=term)
            | Q(user__phone_number__icontains=term)
            | Q(barcode__icontains=term)
        )
class MyProfileView(generics.RetrieveAPIView):
    serializer_class = MemberUpdateSerializer
    permission_classes = [CanViewMemberDetail]
    http_method_names = ["get"]
    def get_object(self):
        return self.request.user.profile

class BarcodeCheckInView(APIView):
    def post(self, request, barcode_number):
        try:
            member = MemberProfile.objects.get(barcode=barcode_number)
        except MemberProfile.DoesNotExist:
            return Response({"valid": False, "message": "Member not found."}, status=404)

        now = timezone.now()
        if member.end_date < now and member.user.is_active:
            member.user.is_active = False
            member.user.save(update_fields=["is_active"])

        if not member.user.is_active:
            return Response({
                "valid": False,
                "member": member.user.first_name,
                "message": f"Membership expired on {member.end_date.strftime('%Y-%m-%d %H:%M:%S')}."
            }, status=403)

        checkin = CheckIn.objects.create(member=member)
        member.refresh_from_db()

        return Response({
            "valid": True,
            "member": member.user.first_name,
            "package_type": member.package_type,
            "end_date": member.end_date,
            "message": "Check-in successful!",
            "checkin": CheckInSerializer(checkin).data
        }, status=200)



class RecentCheckInsView(generics.ListAPIView):
    serializer_class = RecentCheckInSerializer
    pagination_class = CheckInPagination
    http_method_names = ["get"]

    def get_queryset(self):
        now = timezone.now()
        range_param = self.request.query_params.get("range", "day") 
        order_param = self.request.query_params.get("order", "desc")  
        if range_param == "month":
            time_threshold = now - timedelta(days=30)
        elif range_param == "day":
            time_threshold = now - timedelta(hours=24)
        else:
            time_threshold = now - timedelta(hours=24)
        order_by = "timestamp" if order_param == "asc" else "-timestamp"

        return CheckIn.objects.filter(timestamp__gte=time_threshold).order_by(order_by)

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)
        active_count = MemberProfile.objects.filter(user__is_active=True).count()
        inactive_count = MemberProfile.objects.filter(user__is_active=False).count()

        return self.get_paginated_response({
            "checkins": serializer.data,
            "active_members": active_count,
            "inactive_members": inactive_count,
        })

class ReceptionistUpdateMemberView(generics.RetrieveUpdateAPIView):
    queryset = MemberProfile.objects.all()
    serializer_class = MemberUpdateSerializer
    permission_classes = [IsReceptionist]  

class RecentRegistrationsView(generics.ListAPIView):
    serializer_class = MemberUpdateSerializer
    pagination_class = CheckInPagination
    http_method_names = ["get"]

    def get_queryset(self):
        now = timezone.now()
        range_param = self.request.query_params.get("range", "day") 
        order_param = self.request.query_params.get("order", "desc")
        if range_param == "month":
            time_threshold = now - timedelta(days=30)
        elif range_param == "week":
            time_threshold = now - timedelta(7)
        else:
            time_threshold = now - timedelta(hours=24)
        order_by = "start_date" if order_param == "asc" else "-start_date"

        return MemberProfile.objects.filter(start_date__gte=time_threshold).order_by(order_by)
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        data = [
            {
                "firstname": member.user.first_name,
                "barcode": member.barcode,
                "package_type": member.package_type,
                "is_active": member.user.is_active,  # already correct
            }
            for member in queryset
        ]

        return Response(data)

class MemberAttendanceCalendarView(generics.GenericAPIView):
    permission_classes = [IsMember]
    http_method_names = ["get"]

    def get(self, request):
        user = request.user
        member_profile = user.profile
        month = int(request.query_params.get("month", timezone.now().month))
        year = int(request.query_params.get("year", timezone.now().year))
        start_date = datetime(year, month, 1)
        last_day = monthrange(year, month)[1]
        end_date = datetime(year, month, last_day, 23, 59, 59)
        checkins = CheckIn.objects.filter(
            member=member_profile,
            timestamp__range=(start_date, end_date)
        )
        attended_days = {c.timestamp.day for c in checkins}
        days_status = []
        for day in range(1, last_day + 1):
            day_date = datetime(year, month, day)
            attended = day in attended_days
            days_status.append({
                "day": day,
                "date": day_date.strftime("%Y-%m-%d"),
                "attended": attended
            })
        data = {
            "member": f"{user.first_name} {user.last_name}",
            "month": start_date.strftime("%B"),
            "year": year,
            "total_attended_days": len(attended_days),
            "days": days_status
        }

        return Response(data)
class ReceptionistUpcomingExpirationsView(generics.GenericAPIView):
    permission_classes = [IsReceptionist]
    http_method_names = ["get"]

    def get(self, request):
        today = timezone.now().date()
        upcoming_date = today + timedelta(days=5)
        upcoming_expirations = MemberProfile.objects.filter(
            end_date__gte=today, end_date__lte=upcoming_date
        ).order_by("end_date")

        members_data = []
        for member in upcoming_expirations:
            days_left = (member.end_date.date() - today).days if member.end_date else None
            members_data.append({
                "id": member.id,
                "name": f"{member.user.first_name} {member.user.last_name}",
                "end_date": member.end_date,
                "days_left": days_left,
                "phone": getattr(member, "phone", None), 
                "email": getattr(member.user, "email", None), 
            })

        return Response({
            "count": len(members_data),
            "members_expiring_soon": members_data
        })
class MemberAttachImageView(generics.UpdateAPIView):
    queryset = MemberProfile.objects.all()
    serializer_class = MemberImageAttachSerializer
    permission_classes = [IsReceptionist]
    http_method_names = ["put"]
    lookup_field = "pk"
