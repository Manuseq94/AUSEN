"""
Configuración de URLs Principal (Proyecto: gestion_vacaciones)

Define las rutas de nivel superior del sistema, incluyendo el panel de administración,
las rutas de autenticación nativas de Django y las rutas de la aplicación principal ('core').
También maneja la provisión de archivos estáticos y multimedia en modo DEBUG.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    # 1. Panel de Administración de Django
    path('admin/', admin.site.urls),

    # 2. Autenticación Nativa (habilitando /accounts/login/ y /accounts/logout/)
    path('accounts/', include('django.contrib.auth.urls')),

    # 3. Enrutamiento a la App Principal ('core')
    path('', include('core.urls')),
]

# 4. Exposición de Archivos Multimedia durante Desarrollo
# Esto permite acceder visualmente a los archivos subidos al Legajo (como PDFs) desde el navegador.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
