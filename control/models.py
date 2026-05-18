from django.db import models


class OutputTarget(models.Model):
    key = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=50)
    current_state = models.JSONField(default=dict, blank=True)
    is_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['key']
        indexes = [
            models.Index(fields=['key']),
            models.Index(fields=['kind']),
        ]

    def __str__(self) -> str:
        return f'{self.name} ({self.key})'


class CommandLog(models.Model):
    class Source(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        VOICE = 'voice', 'Voice'
        RULE = 'rule', 'Rule'
        SYSTEM = 'system', 'System'

    class Status(models.TextChoices):
        QUEUED = 'queued', 'Queued'
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'
        COMPLETED = 'completed', 'Completed'

    command_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    target = models.CharField(max_length=100, blank=True)
    params = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=50, choices=Source.choices, default=Source.MANUAL)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.QUEUED)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['command_id']),
            models.Index(fields=['target']),
            models.Index(fields=['status', '-created_at']),
        ]

    def __str__(self) -> str:
        target = f' -> {self.target}' if self.target else ''
        return f'{self.name}{target} [{self.status}]'
