from django.urls import path
from .views import (
    MemberCreateView,
    TrainerUpdateMemberView,
    ListMembersView,
    MyProfileView,
    BarcodeCheckInView,
    RecentCheckInsView,
    ReceptionistUpdateMemberView,
    RecentRegistrationsView,
    MemberAttendanceCalendarView,
    ReceptionistUpcomingExpirationsView,
    MemberAttachImageView,
    DietplanListView,
    AssignLockerAPIView,
    LockerListAPIView,
    NewPendingMemberCreateView,
    RenewalPendingMemberCreateView,
    PendingMemberListView,
    ActivatePendingMemberView,
    InstantRenewalView,
)

urlpatterns = [

    # MEMBER MANAGEMENT
    path("create/", MemberCreateView.as_view(), name="member-create"),
    path("list/", ListMembersView.as_view(), name="members-list"),
    path("update/<int:pk>/", ReceptionistUpdateMemberView.as_view(), name="member-receptionist-update"),
    path("<int:pk>/trainer-update/", TrainerUpdateMemberView.as_view(), name="member-trainer-update"),

    # MEMBER PROFILE
    path("me/", MyProfileView.as_view(), name="my-profile"),
    path("me/calendar/", MemberAttendanceCalendarView.as_view(), name="member-calendar"),

    # MEMBER IMAGE
    path("<int:pk>/attach-image/", MemberAttachImageView.as_view(), name="member-attach-image"),

    # CHECK-IN SYSTEM
    path("checkin/<str:barcode_number>/", BarcodeCheckInView.as_view(), name="barcode-checkin"),
    path("recent-checkins/", RecentCheckInsView.as_view(), name="recent-checkins"),

    # MEMBER ACTIVITY
    path("recent-registrations/", RecentRegistrationsView.as_view(), name="recent-registrations"),
    path("upcoming-expirations/", ReceptionistUpcomingExpirationsView.as_view(), name="upcoming-expirations"),

    # DIET PLANS
    path("dietplan/", DietplanListView.as_view(), name="dietplan-list"),

    # LOCKER SYSTEM
    path("lockers/", LockerListAPIView.as_view(), name="locker-list"),
    path("lockers/assign/", AssignLockerAPIView.as_view(), name="locker-assign"),

    # PENDING MEMBERS
    path("pending/", PendingMemberListView.as_view(), name="pending-list"),
    path("pending/new/", NewPendingMemberCreateView.as_view(), name="pending-new-create"),
    path("pending/renew/<int:pk>/", InstantRenewalView.as_view(), name="instant-renewal"),
    path("pending/renewal/", RenewalPendingMemberCreateView.as_view(), name="pending-renewal-create"),
    path("pending/<int:pk>/activate/", ActivatePendingMemberView.as_view(), name="pending-activate"),

]
