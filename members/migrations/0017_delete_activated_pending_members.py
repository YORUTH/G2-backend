from django.db import migrations


def delete_activated_pending(apps, schema_editor):
    PendingMember = apps.get_model("members", "PendingMember")
    PendingMember.objects.filter(status="activated").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("members", "0016_alter_memberprofile_package_type_and_more"),
    ]

    operations = [
        migrations.RunPython(delete_activated_pending, migrations.RunPython.noop),
    ]
