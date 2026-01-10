from rest_framework import serializers
from accounts.models import User
from rest_framework import serializers
from .models import TrainerDietplan
from cloudinary.uploader import upload

class TrainerDietplanSerializer(serializers.ModelSerializer):
    items = serializers.ListField(
        child=serializers.CharField(),
        allow_empty=False
    )
    image = serializers.ImageField(write_only=True, required=False)  # allow uploads

    class Meta:
        model = TrainerDietplan
        fields = ["id", "title", "description", "items", "image_url", "image"]
        read_only_fields = ["image_url"]

    def create(self, validated_data):
        image_file = validated_data.pop("image", None)
        instance = TrainerDietplan.objects.create(**validated_data)

        if image_file:
            result = upload(
                image_file,
                folder="diet_images",
                public_id=f"dietplan_{instance.id}",
                overwrite=True
            )
            instance.image_url = result.get("secure_url")
            instance.save(update_fields=["image_url"])

        return instance
class StaffSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id","phone_number", "first_name", "last_name", "role", "password"]
        extra_kwargs = {"password": {"write_only": True}}

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user
