from django.db import models


class AutomationRule(models.Model):
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    conditions = models.JSONField(default=list, blank=True)
    action = models.JSONField(default=dict, blank=True)
    is_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['is_enabled']),
        ]

    def __str__(self) -> str:
        return self.name
