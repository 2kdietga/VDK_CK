from django.contrib import admin

from .models import SensorReading


@admin.register(SensorReading)
class SensorReadingAdmin(admin.ModelAdmin):
    list_display = ('id', 'temperature', 'humidity', 'light', 'created_at')
    list_filter = ('created_at',)
    readonly_fields = ('created_at',)
