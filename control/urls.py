from django.urls import path

from . import views


urlpatterns = [
    path('esp32/commands/', views.send_command, name='send-command'),
    path('esp32/commands/<str:command_id>/', views.command_status, name='command-status'),
]
