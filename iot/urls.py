from django.urls import path

from . import views


urlpatterns = [
    path('esp32/', views.esp32_state, name='esp32-state'),
    path('esp32/commands/', views.send_command, name='send-command'),
]
