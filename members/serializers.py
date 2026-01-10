from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from .models import MemberProfile, CheckIn,PACKAGE_DURATION
from django.utils import timezone
from django.contrib.auth import get_user_model
from datetime import timedelta
User = get_user_model()
PACKAGE_CHOICES = [(key, key.capitalize()) for key in PACKAGE_DURATION.keys()]


class MemberCreateSerializer(serializers.ModelSerializer):
    first_name = serializers.CharField(write_only=True)
    last_name = serializers.CharField(write_only=True)
    phone_number = serializers.CharField(write_only=True)
    password = serializers.CharField(write_only=True)

    class Meta:
        model = MemberProfile
        fields = [
            "first_name",
            "last_name",
            "phone_number",
            "password",
            "weight",
            "height",
            "gender",
            "age",
            "package_type",
            "start_date",
        ]

    def create(self, validated_data):
        user_data = {
            "first_name": validated_data.pop("first_name"),
            "last_name": validated_data.pop("last_name"),
            "phone_number": validated_data.pop("phone_number"),
            "role": "member",
        }
        password = validated_data.pop("password")
        if User.objects.filter(phone_number=user_data["phone_number"]).exists():
            raise serializers.ValidationError({"phone_number": "This phone number is already registered."})
        user = User.objects.create_user(**user_data, password=password)

        package_type = validated_data.get("package_type")
        start_date = validated_data.get("start_date") or timezone.now()
        validated_data["start_date"] = start_date  
        if package_type in PACKAGE_DURATION:
            months = PACKAGE_DURATION[package_type]
            validated_data["end_date"] = start_date + timedelta(days=30*months)

        profile = MemberProfile.objects.create(user=user, **validated_data)
        return profile

class TrainerUpdateSerializer(serializers.ModelSerializer):
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    phone_number = serializers.CharField(source="user.phone_number", read_only=True)

    class Meta:
        model = MemberProfile
        fields = [
            "first_name",
            "last_name",
            "phone_number",
            "image_url",
            "weight",
            "height",
        ]

class MemberUpdateSerializer(serializers.ModelSerializer):
    package_type = serializers.ChoiceField(choices=[(k, k.capitalize()) for k in PACKAGE_DURATION.keys()])
    phone_number = serializers.CharField(source="user.phone_number", read_only=True)
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    is_active = serializers.BooleanField(source="user.is_active")  # receptionist can update
    weight = serializers.FloatField(read_only=True)
    height = serializers.FloatField(read_only=True)
    gender = serializers.CharField(read_only=True)
    age = serializers.IntegerField(read_only=True)
    barcode = serializers.CharField(read_only=True)
    barcode_image = serializers.URLField(read_only=True)
    start_date = serializers.DateTimeField(read_only=True)
    end_date = serializers.DateTimeField(read_only=True)
    left_days = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = MemberProfile
        fields = [
            "id",
            "phone_number",
            "first_name",
            "last_name",
            "weight",
            "height",
            "gender",
            "age",
            "barcode",
            "barcode_image",
            "image_url",
            "package_type",
            "start_date",
            "end_date",
            "is_active",
            "left_days",
        ]

    def get_left_days(self, obj):
        from django.utils import timezone
        if obj.end_date:
            delta = obj.end_date.date() - timezone.now().date()
            return max(delta.days, 0)
        return 0

    def get_is_active(self, obj):
        # Just return the current user status
        return obj.user.is_active


    def update(self, instance, validated_data):
        user_data = validated_data.get("user", {})
        new_active = user_data.get("is_active", instance.user.is_active)
        new_package = validated_data.get("package_type", instance.package_type)
        left_days = self.get_left_days(instance)
        if instance.user.is_active and left_days > 0 and new_active:
            raise ValidationError({
                "detail": "Cannot update active membership before it expires."
            })
        if not instance.user.is_active or left_days <= 0:
            if new_active:
                now = timezone.now()
                instance.start_date = now
                months = PACKAGE_DURATION.get(new_package, 1)
                instance.end_date = now + timedelta(days=30*months)
                instance.package_type = new_package
                instance.user.is_active = True
                instance.user.save(update_fields=["is_active"])
        instance.save(update_fields=["start_date", "end_date", "package_type"])
        return instance

class CheckInSerializer(serializers.ModelSerializer):
    member = MemberUpdateSerializer(read_only=True)

    class Meta:
        model = CheckIn
        fields = ["id", "member", "timestamp"]

class RecentCheckInSerializer(serializers.ModelSerializer):
    member_name = serializers.CharField(source="member.user.first_name", read_only=True)
    image_url = serializers.SerializerMethodField()
    class Meta:
        model = CheckIn
        fields = ["id", "member_name","image_url","timestamp"]
    def get_image_url(self, obj):
        if hasattr(obj.member, "image_url") and obj.member.image_url:
            return obj.member.image_url
        return None

class MemberImageAttachSerializer(serializers.ModelSerializer):
    first_name = serializers.CharField(required=False)
    last_name = serializers.CharField(required=False)
    phone_number = serializers.CharField(required=False)
    age = serializers.IntegerField(required=False)
    weight = serializers.FloatField(required=False)
    height = serializers.FloatField(required=False)

    class Meta:
        model = MemberProfile
        fields = [
            "image",
            "first_name",
            "last_name",
            "phone_number",
            "age",
            "weight",
            "height",
        ]
        extra_kwargs = {
            "image": {"write_only": True, "required": False},
        }

    def update(self, instance, validated_data):
        from cloudinary.uploader import upload

        # Handle image upload
        image = validated_data.pop("image", None)
        if image:
            upload_result = upload(
                image,
                folder="member_images",
                public_id=f"{instance.user.first_name}_{instance.user.id}_profile",
                overwrite=True,
            )
            instance.image_url = upload_result["secure_url"]

        # Update MemberProfile fields
        for field in ["age", "weight", "height"]:
            if field in validated_data:
                setattr(instance, field, validated_data[field])

        # Update User fields
        user = instance.user
        if "first_name" in validated_data:
            user.first_name = validated_data["first_name"]
        if "last_name" in validated_data:
            user.last_name = validated_data["last_name"]
        if "phone_number" in validated_data:
            user.phone_number = validated_data["phone_number"]

        user.save()
        instance.save()
        return instance
