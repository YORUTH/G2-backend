from datetime import timedelta

from django.db import migrations
from django.db.models import F
from django.utils import timezone


def cleanup_stale_locker_assignments(apps, schema_editor):
    LockerAssignment = apps.get_model("members", "LockerAssignment")
    now = timezone.now()
    cooldown_cutoff = now - timedelta(days=3)

    LockerAssignment.objects.filter(
        status="active",
        expires_at__lte=now,
    ).update(status="expired", expired_at=F("expires_at"))

    LockerAssignment.objects.filter(
        status="expired",
        expired_at__lte=cooldown_cutoff,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("members", "0017_delete_activated_pending_members"),
    ]

    operations = [
        migrations.RunPython(cleanup_stale_locker_assignments, migrations.RunPython.noop),
    ]
