from django.urls import path

from . import views


urlpatterns = [
    path('llm/intent/', views.intent_api, name='llm-intent-api'),
]
