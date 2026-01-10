# members/admin.py
from django.contrib import admin
from .models import MemberProfile, CheckIn

@admin.register(MemberProfile)
class MemberProfileAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "gender",
        "age",
        "package_type",
        "start_date",
        "end_date",
        "barcode",
        "barcode_image"
    )
    search_fields = ("user__first_name", "user__last_name", "barcode","barcode_image")
    list_filter = ("package_type", "gender")

    # Only fields you want to edit manually
    fields = (
        "user",
        "weight",
        "height",
        "gender",
        "age",
        "image_url",  # manually enter only this
        "package_type",
        "start_date",  # can be past or future
    )
