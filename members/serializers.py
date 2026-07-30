from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from .models import MemberProfile, CheckIn, PACKAGE_DURATION, PACKAGE_CHOICES as MODEL_PACKAGE_CHOICES
from django.utils import timezone
from django.contrib.auth import get_user_model
from datetime import timedelta
from .models import PendingMember

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from .models import MemberProfile, Locker, assign_locker

User = get_user_model()
PACKAGE_CHOICES = MODEL_PACKAGE_CHOICES


def _validate_women_package(package_type, gender):
    if package_type and package_type.endswith("_women") and gender != "female":
        raise serializers.ValidationError(
            {"package_type": "Women package types are only allowed for female members."}
        )


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

    def validate(self, attrs):
        _validate_women_package(attrs.get("package_type"), attrs.get("gender"))
        return attrs

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
            validated_data["end_date"] = start_date + timedelta(days=30 * months)
        
        validated_data["payment_recorded_at"] = start_date

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


class MemberReadSerializer(serializers.ModelSerializer):
    phone_number = serializers.CharField(source="user.phone_number", read_only=True)
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    is_active = serializers.BooleanField(source="user.is_active", read_only=True)
    left_days = serializers.SerializerMethodField(read_only=True)
    package_price = serializers.IntegerField(read_only=True)
    locker_number = serializers.SerializerMethodField()

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
            "package_price",
            "start_date",
            "end_date",
            "payment_recorded_at",
            "is_active",
            "left_days",
            "locker_number",
        ]
        read_only_fields = fields

    def get_left_days(self, obj):
        if obj.end_date:
            delta = obj.end_date.date() - timezone.now().date()
            return max(delta.days, 0)
        return 0

    def get_locker_number(self, obj):
        active = obj.locker_assignments.filter(
            status="active", expires_at__gt=timezone.now()
        ).first()
        if active:
            return active.locker.locker_number
        return None


class CheckInSerializer(serializers.ModelSerializer):
    member = MemberReadSerializer(read_only=True)
    locker_number = serializers.SerializerMethodField()

    class Meta:
        model = CheckIn
        fields = ["id", "member", "locker_number", "timestamp"]

    def get_locker_number(self, obj):
        active = obj.member.locker_assignments.filter(
            status="active", expires_at__gt=timezone.now()
        ).first()
        if active:
            return active.locker.locker_number
        return None


class RecentCheckInSerializer(serializers.ModelSerializer):
    member_name = serializers.CharField(source="member.user.first_name", read_only=True)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = CheckIn
        fields = ["id", "member_name", "image_url", "timestamp"]

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

        image = validated_data.pop("image", None)
        if image:
            upload_result = upload(
                image,
                folder="member_images",
                public_id=f"{instance.user.first_name}_{instance.user.id}_profile",
                overwrite=True,
            )
            instance.image_url = upload_result["secure_url"]

        for field in ["age", "weight", "height"]:
            if field in validated_data:
                setattr(instance, field, validated_data[field])

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




class LockerAssignSerializer(serializers.Serializer):
    member_id = serializers.IntegerField()
    locker_number = serializers.IntegerField(min_value=1)

    def validate(self, data):
        try:
            member = MemberProfile.objects.get(id=data["member_id"])
        except MemberProfile.DoesNotExist:
            raise serializers.ValidationError("Invalid member ID.")
        data["member"] = member
        return data

    def create(self, validated_data):
        member = validated_data["member"]
        locker_number = validated_data["locker_number"]

        try:
            with transaction.atomic():
                Locker.objects.get_or_create(
                    locker_number=locker_number,
                    defaults={"status": "available"},
                )
                assignment = assign_locker(
                    member=member,
                    locker_number=locker_number,
                )
                return assignment
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.message)


class LockerListSerializer(serializers.ModelSerializer):
    gender = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    member_id = serializers.SerializerMethodField()
    member_name = serializers.SerializerMethodField()
    expires_at = serializers.SerializerMethodField()
    days_left = serializers.SerializerMethodField()

    class Meta:
        model = Locker
        fields = [
            "locker_number",
            "gender",
            "status",
            "member_id",
            "member_name",
            "expires_at",
            "days_left",
        ]

    def _get_assignments(self, obj):
        return getattr(obj, "gender_assignments", [])

    def _active_assignment(self, obj):
        now = self.context.get("now", timezone.now())
        for a in self._get_assignments(obj):
            if a.status == "active" and a.expires_at > now:
                return a
        return None

    def _expired_assignment(self, obj):
        """Return the most recent expired assignment (for display)."""
        for a in self._get_assignments(obj):
            if a.status == "expired":
                return a
        return None

    def _relevant_assignment(self, obj):
        """Return active if exists, otherwise expired."""
        return self._active_assignment(obj) or self._expired_assignment(obj)

    def get_gender(self, obj):
        return self.context.get("gender")

    def get_status(self, obj):
        if self._active_assignment(obj):
            return "active"
        if self._expired_assignment(obj):
            return "expired"
        return "available"

    def get_member_id(self, obj):
        a = self._relevant_assignment(obj)
        return a.member.id if a else None

    def get_member_name(self, obj):
        a = self._relevant_assignment(obj)
        if not a:
            return None
        u = a.member.user
        parts = [p.strip() for p in (u.first_name or "", u.last_name or "") if p and p.strip()]
        return " ".join(parts) if parts else None

    def get_expires_at(self, obj):
        a = self._relevant_assignment(obj)
        return a.expires_at if a else None

    def get_days_left(self, obj):
        a = self._active_assignment(obj)
        if a:
            delta = a.expires_at.date() - timezone.now().date()
            return max(delta.days, 0)
        return None


from django.contrib.auth.hashers import make_password


class NewPendingMemberSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = PendingMember
        fields = [
            "first_name",
            "last_name",
            "phone_number",
            "weight",
            "height",
            "gender",
            "age",
            "package_type",
            "password",
        ]

    def validate(self, attrs):
        _validate_women_package(attrs.get("package_type"), attrs.get("gender"))
        return attrs

    def create(self, validated_data):
        validated_data["password"] = make_password(validated_data["password"])
        validated_data["pending_type"] = "new"
        validated_data["activation_mode"] = "delayed"
        return PendingMember.objects.create(**validated_data)


class RenewalPendingSerializer(serializers.Serializer):
    member_id = serializers.IntegerField()
    package_type = serializers.ChoiceField(choices=PACKAGE_CHOICES)

    def validate_member_id(self, value):
        try:
            member = MemberProfile.objects.get(pk=value)
        except MemberProfile.DoesNotExist:
            raise serializers.ValidationError("Member not found.")

        if PendingMember.objects.filter(
            phone_number=member.user.phone_number,
            pending_type="renewal",
            status="pending"
        ).exists():
            raise serializers.ValidationError(
                "A pending renewal already exists for this member."
            )

        return value

    def validate(self, attrs):
        member = MemberProfile.objects.get(pk=attrs["member_id"])
        _validate_women_package(attrs.get("package_type"), member.gender)
        return attrs


class PendingMemberSerializer(serializers.ModelSerializer):
    recorded_by = serializers.SerializerMethodField()
    package_price = serializers.IntegerField(read_only=True)

    def get_recorded_by(self, obj):
        if obj.recorded_by:
            return f"{obj.recorded_by.first_name} {obj.recorded_by.last_name}"
        return None

    class Meta:
        model = PendingMember
        fields = [
            "id",
            "first_name",
            "last_name",
            "phone_number",
            "pending_type",
            "package_type",
            "package_price",
            "activation_mode",
            "status",
            "recorded_by",
            "recorded_at",
        ]
        read_only_fields = fields