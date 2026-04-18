from django.contrib import admin
from .models import TemporalMember


@admin.register(TemporalMember)
class TemporalMemberAdmin(admin.ModelAdmin):
    list_display = ("full_name", "amount", "paid_at")
    search_fields = ("full_name",)
