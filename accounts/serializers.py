from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.contrib.auth import authenticate
from rest_framework import serializers
from phonenumber_field.serializerfields import PhoneNumberField

class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    username_field = "phone_number"
    phone_number = PhoneNumberField(region="ET")
    password = serializers.CharField(write_only=True)
    def validate(self, attrs):
        phone_number = attrs.get("phone_number")
        password = attrs.get("password")
        user = authenticate(
            request=self.context.get("request"),
            phone_number=phone_number,
            password=password
        )
        if not user:
            raise serializers.ValidationError("Invalid phone number or password")
        if not user.is_active:
            raise serializers.ValidationError("This account is inactive")
        refresh = self.get_token(user)
        access = refresh.access_token
        phone_number_str = str(user.phone_number) if user.phone_number else None
        access["phone_number"] = phone_number_str
        access["first_name"] = user.first_name
        access["last_name"] = user.last_name
        access["role"] = user.role
        return {
            "refresh": str(refresh),
            "access": str(access),
            "user": {
                "phone_number": phone_number_str,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "role": user.role,
            },
        }
