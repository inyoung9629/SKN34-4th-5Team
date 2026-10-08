"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView


def health_check(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("api/v1/healthz/", health_check, name="api-v1-health-check"),
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="api-v1-schema"),
    path("admin/", admin.site.urls),
    path("api/v1/", include("config.urls_v1")),
    # 채팅 버전 분기(v1/v2)는 llm/urls.py 안의 re_path 가 맡는다. 여기는 다른 앱들과
    # 똑같이 plain include 만 한다.
    path("api/", include("llm.urls")),
]
