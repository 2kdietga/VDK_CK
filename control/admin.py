from django.contrib import admin

from .models import CommandLog, OutputTarget


@admin.register(OutputTarget)
class OutputTargetAdmin(admin.ModelAdmin):
    list_display = ('key', 'name', 'kind', 'is_enabled', 'updated_at')
    list_filter = ('kind', 'is_enabled')
    search_fields = ('key', 'name', 'kind')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(CommandLog)
class CommandLogAdmin(admin.ModelAdmin):
    list_display = ('command_id', 'name', 'target', 'source', 'status', 'created_at', 'sent_at')
    list_filter = ('source', 'status', 'created_at')
    search_fields = ('command_id', 'name', 'target')
    readonly_fields = ('created_at', 'sent_at', 'completed_at')
