from django.core.management.base import BaseCommand
import sys

class Command(BaseCommand):
    help = "Dummy command to create the file structure."

    def handle(self, *args, **options):
        pass
