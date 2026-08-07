from rest_framework import generics, permissions
from rest_framework.permissions import AllowAny
from .models import PACKAGE_DURATION, MemberProfile
from .serializers import MemberCreateSerializer, TrainerUpdateSerializer, MemberReadSerializer
from accounts.permissions import IsManagerOrReceptionist, IsTrainer, IsMember, CanViewMemberDetail, CanViewMembersList
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
from django.utils.dateparse import parse_date
from .models import CheckIn
from calendar import monthrange
from .serializers import RecentCheckInSerializer
from datetime import timedelta, datetime
from .filters import MemberProfileFilter
from rest_framework.pagination import PageNumberPagination
from .serializers import MemberImageAttachSerializer
from django.contrib.auth import get_user_model

User = get_user_model()


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
    http_method_names = ["get", "patch"]


class MemberCreateView(generics.CreateAPIView):
    queryset = MemberProfile.objects.all()
    serializer_class = MemberCreateSerializer
    permission_classes = [IsManagerOrReceptionist]
    http_method_names = ["post"]

    def get(self, request, *args, **kwargs):
        return Response(
            {"detail": "Use POST to create members"},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )


class ListMembersView(generics.ListAPIView):
    serializer_class = MemberReadSerializer
    permission_classes = [CanViewMembersList]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    http_method_names = ["get"]
    filterset_class = MemberProfileFilter
    filterset_fields = ["id", "phone_number", "first_name", "last_name", "barcode", "package_type", "start_date", "end_date"]
    ordering_fields = ["id", "start_date", "end_date", "left_days"]
    ordering = ["-id"]
    pagination_class = MemberListPagination

    def get_queryset(self):
        now = timezone.now()

        User.objects.filter(
            profile__end_date__lt=now,
            is_active=True
        ).update(is_active=False)

        queryset = MemberProfile.objects.all()

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
        if not term:
            return queryset

        term_date = parse_date(term)
        if not term_date:
            for fmt in ["%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%m-%d-%Y"]:
                try:
                    term_date = datetime.strptime(term, fmt).date()
                    break
                except ValueError:
                    continue

        if term_date:
            return queryset.filter(start_date__date=term_date).order_by("-id")

        term_id = int(term) if term.isdigit() else None

        # If pure numeric search, filter by ID only
        if term_id is not None:
            return queryset.filter(id=term_id).order_by("-id")

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
                    ).filter(similarity__gt=0.2)
                    return queryset.order_by("-id")
            except ProgrammingError:
                pass
            except Exception:
                pass

        queryset = queryset.filter(
            Q(user__first_name__icontains=term)
            | Q(user__last_name__icontains=term)
            | Q(user__phone_number__icontains=term)
            | Q(barcode__icontains=term)
        )

        return queryset.order_by("-id")


class MyProfileView(generics.RetrieveAPIView):
    serializer_class = MemberReadSerializer
    permission_classes = [CanViewMemberDetail]
    http_method_names = ["get"]

    def get_object(self):
        return self.request.user.profile


class BarcodeCheckInView(APIView):

    permission_classes = [IsManagerOrReceptionist]

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


class RecentRegistrationsView(generics.ListAPIView):
    serializer_class = MemberReadSerializer
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
                "is_active": member.user.is_active,
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
            days_status.append({
                "day": day,
                "date": day_date.strftime("%Y-%m-%d"),
                "attended": day in attended_days,
            })

        return Response({
            "member": f"{user.first_name} {user.last_name}",
            "month": start_date.strftime("%B"),
            "year": year,
            "total_attended_days": len(attended_days),
            "days": days_status,
        })


class ReceptionistUpcomingExpirationsView(generics.GenericAPIView):
    permission_classes = [IsManagerOrReceptionist]
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
                # FIX: member.user.phone_number is a PhoneNumber object which
                # is not JSON serializable — cast to str explicitly.
                "phone": str(member.user.phone_number),
            })

        return Response({
            "count": len(members_data),
            "members_expiring_soon": members_data,
        })


class MemberAttachImageView(generics.UpdateAPIView):
    queryset = MemberProfile.objects.all()
    serializer_class = MemberImageAttachSerializer
    permission_classes = [IsManagerOrReceptionist]
    http_method_names = ["put"]
    lookup_field = "pk"


from .serializers import LockerAssignSerializer
from .models import Locker, LockerAssignment, cleanup_locker_assignments
from .serializers import LockerListSerializer
from django.db.models import Prefetch


class AssignLockerAPIView(APIView):
    permission_classes = [IsManagerOrReceptionist]

    def post(self, request):
        serializer = LockerAssignSerializer(data=request.data)
        if serializer.is_valid():
            assignment = serializer.save()
            return Response(
                {
                    "message": "Locker assigned successfully.",
                    "locker_number": assignment.locker.locker_number,
                    "member_id": assignment.member.id,
                    "expires_at": assignment.expires_at,
                },
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LockerListAPIView(APIView):
    permission_classes = [IsManagerOrReceptionist]

    def get(self, request):
        now = timezone.now()
        gender = request.query_params.get("gender")

        if gender not in ("male", "female", "all"):
            return Response(
                {"detail": "Query param 'gender' is required (male, female, or all)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cleanup_locker_assignments(now)

        # Include both active AND expired assignments so the frontend
        # can show expired lockers with a "Re-assign" button.
        if gender == "all":
            assignment_qs = LockerAssignment.objects.filter(
                status__in=("active", "expired"),
            ).exclude(
                Q(member__gender__isnull=True) | Q(member__gender__exact="")
            ).select_related("member__user").order_by("-assigned_at")
        else:
            assignment_qs = LockerAssignment.objects.filter(
                status__in=("active", "expired"),
                member__gender=gender,
            ).select_related("member__user").order_by("-assigned_at")

        lockers = Locker.objects.prefetch_related(
            Prefetch(
                "assignments",
                queryset=assignment_qs,
                to_attr="gender_assignments",
            )
        ).order_by("locker_number")

        if gender == "all":
            expanded_data = []
            for locker in lockers:
                assignments = getattr(locker, "gender_assignments", [])
                if not assignments:
                    expanded_data.append({
                        "locker_number": locker.locker_number,
                        "gender": "all",
                        "status": "available",
                        "member_id": None,
                        "member_name": None,
                        "expires_at": None,
                        "days_left": None,
                    })
                else:
                    for a in assignments:
                        u = a.member.user
                        parts = [p.strip() for p in (u.first_name or "", u.last_name or "") if p and p.strip()]
                        name = " ".join(parts) if parts else None
                        
                        days_left = None
                        if a.status == "active":
                            delta = a.expires_at.date() - now.date()
                            days_left = max(delta.days, 0)
                            
                        expanded_data.append({
                            "locker_number": locker.locker_number,
                            "gender": a.member.gender,
                            "status": a.status,
                            "member_id": a.member.id,
                            "member_name": name,
                            "expires_at": a.expires_at,
                            "days_left": days_left,
                        })
            return Response(expanded_data, status=status.HTTP_200_OK)
        else:
            serializer = LockerListSerializer(
                lockers, many=True, context={"gender": gender, "now": now}
            )
            return Response(serializer.data, status=status.HTTP_200_OK)


class ReleaseLockerAPIView(APIView):
    """Manually release an expired locker assignment."""
    permission_classes = [IsManagerOrReceptionist]

    def post(self, request):
        locker_number = request.data.get("locker_number")
        if not locker_number:
            return Response(
                {"detail": "locker_number is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        deleted, _ = LockerAssignment.objects.filter(
            locker__locker_number=locker_number,
            status="expired",
        ).delete()

        if deleted == 0:
            return Response(
                {"detail": "No expired assignment found for this locker."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {"message": f"Locker {locker_number} released successfully."},
            status=status.HTTP_200_OK,
        )


from .models import PendingMember, PendingMemberHistory
from .serializers import NewPendingMemberSerializer, RenewalPendingSerializer, PendingMemberSerializer
from django.db import transaction
from django.shortcuts import get_object_or_404


class NewPendingMemberCreateView(generics.CreateAPIView):
    queryset = PendingMember.objects.all()
    serializer_class = NewPendingMemberSerializer
    permission_classes = [IsManagerOrReceptionist]

    def perform_create(self, serializer):
        serializer.save(recorded_by=self.request.user)


class RenewalPendingMemberCreateView(APIView):
    permission_classes = [IsManagerOrReceptionist]

    def post(self, request):
        serializer = RenewalPendingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        member = MemberProfile.objects.get(
            pk=serializer.validated_data["member_id"]
        )

        pending = PendingMember.objects.create(
            first_name=member.user.first_name,
            last_name=member.user.last_name,
            phone_number=str(member.user.phone_number),
            weight=member.weight,
            height=member.height,
            gender=member.gender,
            age=member.age,
            package_type=serializer.validated_data["package_type"],
            wants_boxing=serializer.validated_data.get("wants_boxing", False),
            password=member.user.password,
            pending_type="renewal",
            recorded_by=request.user,
            activated_profile=member,
        )

        return Response(
            {"message": "Renewal pending recorded", "pending_id": pending.id},
            status=status.HTTP_201_CREATED,
        )


class InstantRenewalView(APIView):
    permission_classes = [IsManagerOrReceptionist]

    def put(self, request, pk):
        return self._renew(request, pk)

    def post(self, request, pk):
        return self._renew(request, pk)

    def _renew(self, request, pk):
        member = get_object_or_404(MemberProfile, pk=pk)
        now = timezone.now()

        if member.user.is_active and member.end_date and member.end_date > now:
            return Response(
                {"detail": "Member still has an active membership."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        package_type = request.data.get("package_type")
        if not package_type or package_type not in PACKAGE_DURATION:
            return Response(
                {"detail": "Invalid or missing package_type."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if package_type.endswith("_women") and member.gender != "female":
            return Response(
                {"detail": "Women package types are only allowed for female members."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        months = PACKAGE_DURATION[package_type]
        wants_boxing = request.data.get("wants_boxing", False)
        
        member.start_date = now
        member.end_date = now + timedelta(days=30 * months)
        member.package_type = package_type
        member.is_renewal = True
        member.has_boxing = wants_boxing
        if wants_boxing:
            member.boxing_expires_at = now + timedelta(days=30)
        else:
            member.boxing_expires_at = None
        member.payment_recorded_at = now
        member.user.is_active = True
        member.user.save(update_fields=["is_active"])
        member.save(update_fields=["start_date", "end_date", "package_type", "is_renewal", "payment_recorded_at", "has_boxing", "boxing_expires_at"])

        return Response({"message": "Membership renewed instantly"})


class ActivatePendingMemberView(APIView):
    permission_classes = [IsManagerOrReceptionist]

    @transaction.atomic
    def post(self, request, pk):
        pending = get_object_or_404(PendingMember, pk=pk)

        if pending.status == "activated":
            return Response(
                {"detail": "This pending member has already been activated."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()
        activated_profile = None

        if pending.pending_type == "new":
            user = User.objects.create(
                phone_number=pending.phone_number,
                first_name=pending.first_name,
                last_name=pending.last_name,
                role="member",
            )
            user.password = pending.password
            user.save(update_fields=["password"])

            activated_profile = MemberProfile.objects.create(
                user=user,
                package_type=pending.package_type,
                start_date=now,
                end_date=now + timedelta(days=30 * PACKAGE_DURATION[pending.package_type]),
                is_renewal=False,
                payment_recorded_at=pending.recorded_at,
                weight=pending.weight or 0.0,
                height=pending.height or 0.0,
                age=pending.age or 0,
                gender=pending.gender,
                has_boxing=pending.wants_boxing,
                boxing_expires_at=now + timedelta(days=30) if pending.wants_boxing else None,
            )

        elif pending.pending_type == "renewal":
            member = get_object_or_404(
                MemberProfile,
                user__phone_number=pending.phone_number,
            )

            if member.user.is_active:
                return Response(
                    {
                        "detail": (
                            "Member's current membership has not yet expired. "
                            "Activation is not allowed until it does."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            member.start_date = now
            member.end_date = now + timedelta(days=30 * PACKAGE_DURATION[pending.package_type])
            member.package_type = pending.package_type
            member.is_renewal = True
            member.payment_recorded_at = pending.recorded_at
            if pending.weight:
                member.weight = pending.weight
            if pending.height:
                member.height = pending.height
            if pending.age:
                member.age = pending.age
            if pending.gender:
                member.gender = pending.gender
            member.user.is_active = True
            member.has_boxing = pending.wants_boxing
            if pending.wants_boxing:
                member.boxing_expires_at = now + timedelta(days=30)
            else:
                member.boxing_expires_at = None
            member.user.save(update_fields=["is_active"])
            member.save(update_fields=[
                "start_date", "end_date", "package_type", "is_renewal", "payment_recorded_at",
                "weight", "height", "age", "gender", "has_boxing", "boxing_expires_at",
            ])
            activated_profile = member

        PendingMemberHistory.objects.create(
            first_name=pending.first_name,
            last_name=pending.last_name,
            phone_number=pending.phone_number,
            weight=pending.weight,
            height=pending.height,
            gender=pending.gender,
            age=pending.age,
            package_type=pending.package_type,
            wants_boxing=pending.wants_boxing,
            pending_type=pending.pending_type,
            activation_mode=pending.activation_mode,
            recorded_by=pending.recorded_by,
            activated_profile=activated_profile,
            recorded_at=pending.recorded_at,
            activated_at=now,
        )

        pending.delete()

        return Response({"message": "Member activated"})


class PendingMemberListView(generics.ListAPIView):
    serializer_class = PendingMemberSerializer
    permission_classes = [IsManagerOrReceptionist]
    pagination_class = None

    def get_queryset(self):
        return PendingMember.objects.filter(status="pending").order_by("-recorded_at")


class PendingMemberHistoryListView(APIView):
    permission_classes = [IsManagerOrReceptionist]

    def get(self, request):
        phone = request.query_params.get("phone_number")
        pending_type = request.query_params.get("pending_type")

        queryset = PendingMemberHistory.objects.select_related(
            "recorded_by", "activated_profile__user"
        ).order_by("-activated_at")

        if phone:
            queryset = queryset.filter(phone_number=phone)
        if pending_type in ("new", "renewal"):
            queryset = queryset.filter(pending_type=pending_type)

        data = [
            {
                "id": row.id,
                "first_name": row.first_name,
                "last_name": row.last_name,
                "phone_number": row.phone_number,
                "pending_type": row.pending_type,
                "wants_boxing": row.wants_boxing,
                "activation_mode": row.activation_mode,
                "package_type": row.package_type,
                "recorded_at": row.recorded_at,
                "activated_at": row.activated_at,
                "recorded_by": (
                    f"{row.recorded_by.first_name} {row.recorded_by.last_name}".strip()
                    if row.recorded_by
                    else None
                ),
                "activated_member_id": row.activated_profile_id,
            }
            for row in queryset
        ]
        return Response(data, status=status.HTTP_200_OK)


class PackageTypesView(APIView):

    permission_classes = [IsManagerOrReceptionist]

    def get(self, request):

        from .constants import PACKAGE_PRICES
        from .models import PACKAGE_CHOICES, PACKAGE_DURATION

        packages = []
        for key, label in PACKAGE_CHOICES:
            packages.append({
                "key": key,
                "label": label,
                "duration_months": PACKAGE_DURATION.get(key),
                "price": PACKAGE_PRICES.get(key),
            })

        return Response(packages)