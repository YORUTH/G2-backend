from django.urls import path
from .views import LoginView, CustomTokenRefreshView

urlpatterns = [
        path('login/', LoginView.as_view(), name='token_obtain_pair'),
        path('refresh/', CustomTokenRefreshView.as_view(), name='token_refresh'),
]
