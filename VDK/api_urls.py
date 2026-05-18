from django.urls import include, path


urlpatterns = [
    path('', include('gateway.urls')),
    path('', include('control.urls')),
    path('', include('assistant_ai.urls')),
]
