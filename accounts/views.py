from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework import status
from django.contrib.auth import get_user_model

from .serializers import MyTokenObtainPairSerializer

User = get_user_model()


class LoginView(TokenObtainPairView):
    serializer_class = MyTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)

        if response.status_code == 200:
            role = response.data.get("user", {}).get("role")

            if role == "member":
                user_phone = response.data["user"]["phone_number"]
                try:
                    user = User.objects.get(phone_number=user_phone)
                    if not user.is_active:
                        return Response(
                            {"detail": "Membership expired. Please renew."},
                            status=status.HTTP_403_FORBIDDEN,
                        )
                except User.DoesNotExist:
                    return Response({"detail": "User not found."}, status=400)


            refresh_token = response.data.get("refresh")
            if refresh_token:
                response.set_cookie(
                    key="refresh_token",
                    value=refresh_token,
                    httponly=True,
                    secure=True,   
                    samesite="Lax",
                    expires=RefreshToken(refresh_token).lifetime,
                )
                del response.data["refresh"]

        return response


class CustomTokenRefreshView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        refresh_token = request.COOKIES.get("refresh_token")
        if not refresh_token:
            return Response({"detail": "No refresh token cookie found."}, status=400)

        try:
            refresh = RefreshToken(refresh_token)
            user_id = refresh["user_id"]
            user = User.objects.get(id=user_id)
            if user.role == "member" and not user.is_active:
                return Response(
                    {"detail": "Membership expired. Please renew."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            access_token = str(refresh.access_token)

            response = Response({"access": access_token})
            new_refresh = str(refresh)

            response.set_cookie(
                key="refresh_token",
                value=new_refresh,
                httponly=True,
                secure=True,   #
                samesite="Lax",
                expires=refresh.lifetime,
                path="/api/token/refresh/",
            )

            return response

        except Exception:
            return Response({"detail": "Invalid refresh token."}, status=400)
