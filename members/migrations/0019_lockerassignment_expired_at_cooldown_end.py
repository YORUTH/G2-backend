from datetime import timedelta

from django.db import migrations
from django.utils import timezone


def normalize_expired_at(apps, schema_editor):
    LockerAssignment = apps.get_model("members", "LockerAssignment")
    now = timezone.now()

    LockerAssignment.objects.filter(status="active").update(expired_at=None)

    for assignment in LockerAssignment.objects.filter(status="expired"):
        cooldown_end = assignment.expires_at + timedelta(days=3)
        if assignment.expired_at != cooldown_end:
            assignment.expired_at = cooldown_end
            assignment.save(update_fields=["expired_at"])

    LockerAssignment.objects.filter(status="expired", expired_at__lte=now).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("members", "0018_cleanup_stale_locker_assignments"),
    ]

    operations = [
        migrations.RunPython(normalize_expired_at, migrations.RunPython.noop),
    ]
