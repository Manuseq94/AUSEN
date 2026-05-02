"""
Configuración de URLs para la aplicación 'core'.

Este archivo define las rutas (endpoints) del sistema administrativo, enrutando 
las peticiones HTTP hacia sus respectivas vistas (views.py) o clases basadas en vistas (auth_views).
"""

from django.contrib.auth import views as auth_views
from django.urls import path

from . import views


# ==========================================
# 1. INICIO Y DASHBOARD
# ==========================================
rutas_inicio = [
    path('', views.home_redirect, name='home_redirect'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('configuracion/', views.configuracion, name='configuracion'),
    path('api/calendario/', views.calendario_api, name='calendario_api'),
]

# ==========================================
# 2. GESTIÓN DE EMPLEADOS
# ==========================================
rutas_empleados = [
    path('empleados/', views.lista_empleados, name='lista_empleados'),
    path('empleados/nuevo/', views.crear_empleado, name='crear_empleado'),
    path('empleado/<int:empleado_id>/', views.detalle_empleado, name='detalle_empleado'),
    path('empleado/editar/<int:empleado_id>/', views.editar_empleado, name='editar_empleado'),
    path('empleado/eliminar/<int:empleado_id>/', views.eliminar_empleado, name='eliminar_empleado'),
]

# ==========================================
# 3. VACACIONES (SOLICITUDES Y BOLSAS)
# ==========================================
rutas_vacaciones = [
    # Solicitudes
    path('empleado/<int:empleado_id>/nueva-solicitud/', views.registrar_vacaciones, name='registrar_vacaciones'),
    path('solicitud/editar/<int:solicitud_id>/', views.editar_solicitud, name='editar_solicitud'),
    path('solicitud/procesar/<int:solicitud_id>/<str:accion>/', views.procesar_solicitud, name='procesar_solicitud'),
    path('solicitud/eliminar/<int:solicitud_id>/', views.eliminar_solicitud, name='eliminar_solicitud'),
    path('solicitud/<int:solicitud_id>/pdf/', views.generar_pdf_solicitud, name='generar_pdf_solicitud'),
    path('historial-general/', views.historial_general, name='historial_general'),

    # Bolsas (Saldos)
    path('empleado/<int:empleado_id>/cargar-saldo/', views.cargar_saldo_historico, name='cargar_saldo_historico'),
    path('bolsa/editar/<int:bolsa_id>/', views.editar_bolsa, name='editar_bolsa'),
    path('bolsa/eliminar/<int:bolsa_id>/', views.eliminar_bolsa, name='eliminar_bolsa'),

    # Procesos Globales cron/manuales
    path('renovar-vacaciones-global/', views.ejecutar_renovacion_anual, name='ejecutar_renovacion'),
]

# ==========================================
# 4. AUSENCIAS (LICENCIAS Y PERMISOS)
# ==========================================
rutas_ausencias = [
    # Licencias Médicas / Especiales
    path('empleado/<int:empleado_id>/nueva-licencia/', views.registrar_licencia, name='registrar_licencia'),
    path('licencia/procesar/<int:licencia_id>/<str:accion>/', views.procesar_licencia, name='procesar_licencia'),
    path('licencia/pdf/<int:licencia_id>/', views.generar_pdf_licencia, name='generar_pdf_licencia'),
    path('licencia/eliminar/<int:licencia_id>/', views.eliminar_licencia, name='eliminar_licencia'),

    # Permisos (Home Office / Trámites)
    path('permiso/registrar/<int:empleado_id>/', views.registrar_permiso, name='registrar_permiso'),
    path('permiso/procesar/<int:permiso_id>/<str:accion>/', views.procesar_permiso, name='procesar_permiso'),
    path('permiso/pdf/<int:permiso_id>/', views.generar_pdf_permiso, name='generar_pdf_permiso'),
    path('permiso/eliminar/<int:permiso_id>/', views.eliminar_permiso, name='eliminar_permiso'),
]

# ==========================================
# 5. LEGAJO DIGITAL Y REPORTES
# ==========================================
rutas_reportes_y_legajos = [
    # Legajo Digital
    path('documento/subir/<int:empleado_id>/', views.subir_documento, name='subir_documento'),
    path('documento/eliminar/<int:documento_id>/', views.eliminar_documento, name='eliminar_documento'),
    
    # Exportaciones CSV
    path('exportar/saldos/', views.exportar_saldos_csv, name='exportar_saldos'),
    path('exportar/historial/<int:empleado_id>/', views.exportar_historial_csv, name='exportar_historial'),
]

# ==========================================
# 6. CONFIGURACIÓN DEL SISTEMA (USUARIOS Y FERIADOS)
# ==========================================
rutas_sistema = [
    # Usuarios
    path('usuarios/', views.gestion_usuarios, name='gestion_usuarios'),
    path('usuario/editar/<int:user_id>/', views.editar_usuario, name='editar_usuario'),
    path('usuarios/eliminar/<int:empleado_id>/', views.eliminar_usuario, name='eliminar_usuario'),
    path('usuario/eliminar-sistema/<int:user_id>/', views.eliminar_usuario_sistema, name='eliminar_usuario_sistema'),

    # Feriados
    path('feriados/crear/', views.crear_feriado, name='crear_feriado'),
    path('feriados/eliminar/<int:feriado_id>/', views.eliminar_feriado, name='eliminar_feriado'),
    path('feriados/importar/', views.importar_feriados_nacionales, name='importar_feriados_nacionales'),
]

# ==========================================
# 7. AUTENTICACIÓN (CLAVES Y RECUPERACIÓN)
# ==========================================
"""
Nota Arquitectónica sobre Auth Views:
Se importan las CBV (Class Based Views) por defecto de django.contrib.auth.views y 
se les inyecta el 'template_name' en el enrutador. 
Esto evita crear lógica repetitiva en views.py para manejar algo estándar como un Password Reset,
manteniendo el core del framework intacto y delegando solo el diseño front-end.
"""
rutas_auth = [
    # A. Cambio de clave (estando logueado)
    path('cambiar-clave/', auth_views.PasswordChangeView.as_view(
        template_name='core/form_cambiar_clave.html',
        success_url='/cambiar-clave/exito/'
    ), name='cambiar_clave'),

    path('cambiar-clave/exito/', auth_views.PasswordChangeDoneView.as_view(
        template_name='core/cambiar_clave_exito.html'
    ), name='password_change_done'),

    # B. Recuperación de clave (Olvidé mi contraseña)
    path('reset_password/', auth_views.PasswordResetView.as_view(
        template_name="core/password_reset_1_request.html",
        email_template_name="core/password_reset_email.html"
    ), name="password_reset"),

    path('reset_password_sent/', auth_views.PasswordResetDoneView.as_view(
        template_name="core/password_reset_2_sent.html"
    ), name="password_reset_done"),

    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name="core/password_reset_3_form.html"
    ), name="password_reset_confirm"),

    path('reset_password_complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name="core/password_reset_4_complete.html"
    ), name="password_reset_complete"),
]

# ==========================================
# COMPILACIÓN FINAL DE URLPATTERNS
# ==========================================
urlpatterns = (
    rutas_inicio + 
    rutas_empleados + 
    rutas_vacaciones + 
    rutas_ausencias + 
    rutas_reportes_y_legajos + 
    rutas_sistema + 
    rutas_auth
)