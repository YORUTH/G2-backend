from django.urls import path
from .views import StaffCreateView, StaffDeleteView,StaffListView,TrainerDietplanView,TrainerDietplanDeleteView


urlpatterns = [
    path("create/", StaffCreateView.as_view(), name="staff-create"),
    path("list/", StaffListView.as_view(), name="staff-delete"),
    path("<int:pk>/delete/", StaffDeleteView.as_view(), name="staff-delete"),
    path("trainer/dietplan/",TrainerDietplanView.as_view(), name = "trainer-dietplan-addview"),
    path("delete/<int:pk>/",TrainerDietplanDeleteView.as_view())
]
