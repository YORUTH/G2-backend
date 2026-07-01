from django.db import migrations


def clear_payment_recorded_at(apps, schema_editor):
    """Leave payment_recorded_at null unless set by pending activation."""
    MemberProfile = apps.get_model("members", "MemberProfile")
    MemberProfile.objects.all().update(payment_recorded_at=None)


class Migration(migrations.Migration):

    dependencies = [
        ("members", "0021_pendingmemberhistory"),
    ]

    operations = [
        migrations.RunPython(clear_payment_recorded_at, migrations.RunPython.noop),
    ]
