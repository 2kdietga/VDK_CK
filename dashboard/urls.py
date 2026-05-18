from django.urls import path

from . import views


app_name = 'dashboard'

urlpatterns = [
    path('', views.overview, name='overview'),
    path('sensors/', views.sensors, name='sensors'),
    path('controls/', views.controls, name='controls'),
    path('commands/', views.commands, name='commands'),
    path('rules/', views.rules, name='rules'),
]
