from django.contrib.auth.backends import ModelBackend
from .models import User

class PhoneBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        phone_number = kwargs.get('phone_number') or username
        if phone_number is None or password is None:
            return None
        try:
            user = User.objects.get(phone_number=phone_number)
        except User.DoesNotExist:
            return None
        if user.check_password(password):
            return user
        return None
