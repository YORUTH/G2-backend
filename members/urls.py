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
)



urlpatterns = [
    path("create/", MemberCreateView.as_view(), name="member-create"),
    path("<int:pk>/update/", TrainerUpdateMemberView.as_view(), name="member-trainer-update"),
    path("list/", ListMembersView.as_view(), name="members-list"),
    path("me/", MyProfileView.as_view(), name="my-profile"),
    path("checkin/<str:barcode_number>/", BarcodeCheckInView.as_view(), name="barcode-checkin"),
    path("recent-checkins/", RecentCheckInsView.as_view(), name="recent-checkins"),
    path("update/<int:pk>/", ReceptionistUpdateMemberView.as_view(), name="recent-check"),
    path("recent-registration/",RecentRegistrationsView.as_view(), name="recent-registration"),
    path("me/callander/",MemberAttendanceCalendarView.as_view(), name="member-callander" ),
    path("<int:pk>/attach-image/", MemberAttachImageView.as_view(), name="member-attach-image"),
    path("upcoming-expirations/", ReceptionistUpcomingExpirationsView.as_view(), name="upcoming-expirations"),
    path("dietplan/", DietplanListView.as_view(), name = "DietplanListView"),
    path("lockers/assign/", AssignLockerAPIView.as_view()),
    path("lockers/list/", LockerListAPIView.as_view(), name="locker-list"),
    path("pending/new/",NewPendingMemberCreateView.as_view(),name="pending-new-create"),
    path("pending/renewal/",RenewalPendingMemberCreateView.as_view(),name="pending-renewal-create",),
    path("pending/",PendingMemberListView.as_view(),name="pending-list"),
    path("pending/<int:pk>/activate/",ActivatePendingMemberView.as_view(),name="pending-activate"),

]

