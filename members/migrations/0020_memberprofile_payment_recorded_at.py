from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("members", "0019_lockerassignment_expired_at_cooldown_end"),
    ]

    operations = [
        migrations.AddField(
            model_name="memberprofile",
            name="payment_recorded_at",
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
    ]
