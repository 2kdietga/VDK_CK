from django.contrib import admin

from .models import AutomationRule


@admin.register(AutomationRule)
class AutomationRuleAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_enabled', 'updated_at')
    list_filter = ('is_enabled',)
    search_fields = ('name', 'description')
    readonly_fields = ('created_at', 'updated_at')
