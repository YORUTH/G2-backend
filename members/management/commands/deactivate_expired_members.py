from django.core.management.base import BaseCommand
from django.utils import timezone
from accounts.models import User
from django.db import transaction

class Command(BaseCommand):
    help = 'Deactivates members whose end_date has passed.'

    def handle(self, *args, **kwargs):
        now = timezone.now()
        
        # We find users who are still marked active but their profile end_date has passed
        expired_users = User.objects.filter(
            profile__end_date__lt=now,
            is_active=True
        )

        count = expired_users.count()
        
        if count > 0:
            with transaction.atomic():
                expired_users.update(is_active=False)
            self.stdout.write(self.style.SUCCESS(f'Successfully deactivated {count} expired members.'))
        else:
            self.stdout.write(self.style.SUCCESS('No expired active members found. Everything is up to date.'))
