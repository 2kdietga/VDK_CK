from django.urls import re_path

from .consumers import ESP32Consumer


websocket_urlpatterns = [
    re_path(r'^ws/esp32/$', ESP32Consumer.as_asgi()),
]
