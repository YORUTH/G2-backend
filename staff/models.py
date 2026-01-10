from django.db import models
from cloudinary.uploader import upload

class TrainerDietplan(models.Model):
    title = models.CharField(max_length=50)
    image = models.ImageField(upload_to='diet_images/', blank=True, null=True)
    description = models.TextField()
    items = models.JSONField(default=list)  # <= super simple
    image_url = models.URLField(blank=True, null=True)

    def __str__(self):
        return self.title