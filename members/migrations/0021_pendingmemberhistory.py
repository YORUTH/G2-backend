from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("members", "0020_memberprofile_payment_recorded_at"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PendingMemberHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("first_name", models.CharField(max_length=50)),
                ("last_name", models.CharField(max_length=50)),
                ("phone_number", models.CharField(max_length=20)),
                ("weight", models.FloatField(blank=True, null=True)),
                ("height", models.FloatField(blank=True, null=True)),
                ("gender", models.CharField(choices=[("male", "Male"), ("female", "Female")], max_length=10)),
                ("age", models.IntegerField(blank=True, null=True)),
                ("package_type", models.CharField(choices=[("bronze", "Bronze - 1 Month"), ("silver", "Silver - 3 Months"), ("gold", "Gold - 6 Months"), ("diamond", "Diamond - 12 Months"), ("bronze_women", "Bronze (Women, 4-6 PM) - 1 Month"), ("silver_women", "Silver (Women) - 3 Months"), ("gold_women", "Gold (Women) - 6 Months"), ("diamond_women", "Diamond (Women) - 12 Months")], max_length=20)),
                ("pending_type", models.CharField(choices=[("new", "New"), ("renewal", "Renewal")], max_length=10)),
                ("activation_mode", models.CharField(choices=[("instant", "Instant"), ("delayed", "Delayed")], max_length=10)),
                ("recorded_at", models.DateTimeField()),
                ("activated_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("activated_profile", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="pending_history", to="members.memberprofile")),
                ("recorded_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="pending_history_recorded", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-activated_at", "-recorded_at"],
            },
        ),
    ]
