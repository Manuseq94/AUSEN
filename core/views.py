"""
Módulo de Vistas (Controllers) para 'core'.

Gestiona toda la lógica de presentación y procesamiento HTTP:
- Dashboards y Reportes.
- Gestión de Empleados, Licencias, Vacaciones y Permisos.
- Aprobación/Rechazo de solicitudes.
- Generación de PDFs y exportaciones CSV.
"""

import csv
from datetime import timedelta
from io import BytesIO, StringIO

import holidays
from xhtml2pdf import pisa

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import ExtractMonth
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import get_template
from django.urls import reverse
from django.utils import timezone

# 1. MODELOS
from .models import (AuditoriaSaldo, BolsaVacaciones, ConsumoDetalle, Documento, Empleado, Feriado, Licencia, Permiso,
                     SolicitudVacaciones)
# 2. FORMULARIOS
from .forms import (BolsaManualForm, CrearUsuarioForm, DocumentoForm, EmpleadoEditarForm, EmpleadoForm, FeriadoForm,
                    LicenciaForm, PermisoForm, SolicitudForm)
# 3. UTILS
from .utils import enviar_notificacion_email


# --- HELPERS LOCALES (DRY) ---
def _obtener_correos_rrhh():
    """Obtiene una lista plana de todos los correos electrónicos de los administradores (RRHH)."""
    return list(User.objects.filter(is_staff=True).exclude(email='').values_list('email', flat=True))


def _tiene_permiso_sobre_empleado(user, empleado_objetivo):
    """
    Verifica si el usuario actual tiene permisos sobre un empleado específico.
    Previene errores 500 (AttributeError) si un user no tiene perfil de Empleado vinculado.
    
    Args:
        user (User): Request User.
        empleado_objetivo (Empleado): Instancia del empleado a consultar/editar.
        
    Returns:
        bool: True si es staff o es el dueño del perfil, False caso contrario.
    """
    if user.is_staff:
        return True
    try:
        return user.empleado == empleado_objetivo
    except AttributeError:
        return False


# --- 1. CONTROLADOR DE TRÁFICO ---
@login_required
def home_redirect(request):
    """
    Controlador inicial: redirige al dashboard si es RRHH o al perfil si es empleado común.
    """
    if request.user.is_staff:
        return redirect('dashboard')
    try:
        empleado = request.user.empleado
        return redirect('detalle_empleado', empleado_id=empleado.id)
    except AttributeError:
        # Failsafe en caso de usuario sin perfil de empleado asociado
        return HttpResponse("<h1>Error</h1><p>Usuario sin empleado asignado.</p>")


# --- 2. DASHBOARD (SOLO RRHH) ---
@login_required
def dashboard(request):
    """
    Vista principal para usuarios con rol de Recursos Humanos (Staff).
    
    Genera el panel de control unificado, compilando métricas de ausencias activas, 
    próximos regresos y solicitudes pendientes de aprobación. Procesa datos para gráficos.
    
    Args:
        request: Objeto HttpRequest.

    Returns:
        HttpResponseRender: Template 'core/dashboard.html' con el contexto analítico.
    """
    if not request.user.is_staff:
        return redirect('home_redirect')

    hoy = timezone.now().date()

    # --- A. ALERTAS DE PENDIENTES (Solo activos) ---
    pendientes = SolicitudVacaciones.objects.filter(estado='PENDIENTE', empleado__activo=True).select_related(
        'empleado').order_by('fecha_inicio')
    licencias_pendientes = Licencia.objects.filter(estado='PENDIENTE', empleado__activo=True).select_related(
        'empleado').order_by('fecha_inicio')
    permisos_pendientes = Permiso.objects.filter(estado='PENDIENTE', empleado__activo=True).select_related(
        'empleado').order_by('fecha_inicio')

    # --- B. LISTA UNIFICADA: AUSENTES HOY ---
    lista_ausentes = []
    procesados_ausentes = set()  # <--- CONJUNTO PARA EVITAR DUPLICADOS

    # 1. Primero buscamos LICENCIAS (Prioridad visual: Rojo mata a Azul)
    lics_ausentes = Licencia.objects.filter(
        fecha_inicio__lte=hoy, fecha_fin__gte=hoy, estado='APROBADO', empleado__activo=True
    ).select_related('empleado')

    for l in lics_ausentes:
        if l.empleado.id not in procesados_ausentes:
            lista_ausentes.append({
                'empleado': l.empleado,
                'hasta': l.fecha_fin,
                'motivo': l.get_tipo_display(),
                'es_licencia': True,
                'empleado_id': l.empleado.id
            })
            procesados_ausentes.add(l.empleado.id)  # Marcamos como procesado

    # 2. Luego buscamos VACACIONES
    vacs_ausentes = SolicitudVacaciones.objects.filter(
        fecha_inicio__lte=hoy, fecha_fin__gte=hoy, estado='APROBADO', empleado__activo=True
    ).select_related('empleado')

    for v in vacs_ausentes:
        if v.empleado.id not in procesados_ausentes:  # Solo si no apareció antes
            lista_ausentes.append({
                'empleado': v.empleado,
                'hasta': v.fecha_fin,
                'motivo': 'Vacaciones',
                'es_licencia': False,
                'empleado_id': v.empleado.id
            })
            procesados_ausentes.add(v.empleado.id)

    # --- C. LISTA UNIFICADA: VUELVEN PRONTO ---
    limite_retorno = hoy + timedelta(days=5)
    lista_regresos = []
    procesados_regresos = set()  # <--- CONJUNTO PARA EVITAR DUPLICADOS AQUI TAMBIEN

    # 1. Licencias que terminan pronto
    lics_ret = Licencia.objects.filter(
        fecha_fin__gte=hoy, fecha_fin__lte=limite_retorno, estado='APROBADO', empleado__activo=True
    ).select_related('empleado')

    for l in lics_ret:
        if l.empleado.id not in procesados_regresos:
            fecha_ret = l.fecha_fin + timedelta(days=1)
            lista_regresos.append({
                'empleado': l.empleado,
                'fecha_retorno': fecha_ret,
                'dias_faltantes': (fecha_ret - hoy).days,
                'motivo': l.get_tipo_display(),
                'es_licencia': True
            })
            procesados_regresos.add(l.empleado.id)

    # 2. Vacaciones que terminan pronto
    vacs_ret = SolicitudVacaciones.objects.filter(
        fecha_fin__gte=hoy, fecha_fin__lte=limite_retorno, estado='APROBADO', empleado__activo=True
    ).select_related('empleado')

    for v in vacs_ret:
        if v.empleado.id not in procesados_regresos:
            fecha_ret = v.fecha_fin + timedelta(days=1)
            lista_regresos.append({
                'empleado': v.empleado,
                'fecha_retorno': fecha_ret,
                'dias_faltantes': (fecha_ret - hoy).days,
                'motivo': 'Vacaciones',
                'es_licencia': False
            })
            procesados_regresos.add(v.empleado.id)

    lista_regresos.sort(key=lambda x: x['fecha_retorno'])

    # --- D. LISTA UNIFICADA: SE VAN PRONTO ---
    limite_salida = hoy + timedelta(days=7)
    lista_salidas = []

    # 1. Vacaciones futuras
    vacs_sal = SolicitudVacaciones.objects.filter(
        fecha_inicio__gt=hoy, fecha_inicio__lte=limite_salida, estado='APROBADO', empleado__activo=True
    ).select_related('empleado')

    for v in vacs_sal:
        lista_salidas.append({
            'empleado': v.empleado,
            'fecha_inicio': v.fecha_inicio,
            'dias_totales': v.dias_totales,
            'motivo': 'Vacaciones',
            'es_licencia': False
        })

    # 2. Licencias futuras
    lics_sal = Licencia.objects.filter(
        fecha_inicio__gt=hoy, fecha_inicio__lte=limite_salida, estado='APROBADO', empleado__activo=True
    ).select_related('empleado')

    for l in lics_sal:
        lista_salidas.append({
            'empleado': l.empleado,
            'fecha_inicio': l.fecha_inicio,
            'dias_totales': l.dias_totales,
            'motivo': l.get_tipo_display(),
            'es_licencia': True
        })

    lista_salidas.sort(key=lambda x: x['fecha_inicio'])

    # --- E. CONTADORES (USANDO LOS CONJUNTOS ÚNICOS) ---
    total_empleados = Empleado.objects.filter(activo=True).count()

    # Usamos el tamaño del SET de ausentes, que garantiza unicidad
    total_ausentes_real = len(procesados_ausentes)

    presentes = total_empleados - total_ausentes_real
    if presentes < 0: presentes = 0

    # Para el cuadro naranja (Solo vacaciones), contamos visualmente los items de la lista
    solo_vacaciones = len([x for x in lista_ausentes if not x['es_licencia']])

    # --- F. DATOS PARA GRÁFICOS (NUEVO) ---

    # 1. Datos para Torta (Simple: Presentes vs Ausentes)
    # Reutilizamos las variables ya calculadas arriba
    grafico_torta_data = [presentes, total_ausentes_real]

    # 2. Datos para Barras (Mensual - Suma de Vacaciones y Licencias)
    anio_actual = timezone.now().year
    datos_mensuales = [0] * 12  # Lista de 12 ceros para Ene-Dic

    # Sumar Vacaciones por mes de inicio
    vacaciones_mes = SolicitudVacaciones.objects.filter(
        fecha_inicio__year=anio_actual,
        estado='APROBADO'
    ).annotate(mes=ExtractMonth('fecha_inicio')).values('mes').annotate(total=Count('id'))

    for v in vacaciones_mes:
        if v['mes']:
            datos_mensuales[v['mes'] - 1] += v['total']  # Restamos 1 porque Enero es mes 1, pero indice 0

    # Sumar Licencias por mes de inicio
    licencias_mes = Licencia.objects.filter(
        fecha_inicio__year=anio_actual,
        estado='APROBADO'
    ).annotate(mes=ExtractMonth('fecha_inicio')).values('mes').annotate(total=Count('id'))

    for l in licencias_mes:
        if l['mes']:
            datos_mensuales[l['mes'] - 1] += l['total']

    # --- CONTEXTO FINAL ---
    context = {
        'pendientes': pendientes,
        'licencias_pendientes': licencias_pendientes,
        'permisos_pendientes': permisos_pendientes,
        'ausentes': lista_ausentes,
        'proximos_regresos': lista_regresos,
        'proximas_salidas': lista_salidas,
        'total_empleados': total_empleados,
        'total_ausentes': solo_vacaciones,  # Dato Visual Naranja
        'total_ausentes_real': total_ausentes_real,  # Dato Real para cálculo
        'presentes': presentes,
        'hoy': hoy,

        # Nuevas variables para charts
        'grafico_torta_data': grafico_torta_data,
        'grafico_barras_data': datos_mensuales,
    }
    return render(request, 'core/dashboard.html', context)


# --- GESTIÓN DE FERIADOS ---

@login_required
def crear_feriado(request):
    if not request.user.is_staff:
        return redirect('dashboard')

    # 1. Procesar Formulario
    if request.method == 'POST':
        form = FeriadoForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('crear_feriado')  # Recargamos la misma pág para ver el nuevo en la lista
    else:
        form = FeriadoForm()

    # 2. Obtener lista para mostrar abajo
    feriados = Feriado.objects.all().order_by('fecha')

    return render(request, 'core/form_feriado.html', {
        'form': form,
        'feriados': feriados  # <--- Enviamos la lista al template
    })


@login_required
def eliminar_feriado(request, feriado_id):
    if not request.user.is_staff:
        return redirect('dashboard')

    feriado = get_object_or_404(Feriado, pk=feriado_id)
    feriado.delete()

    return redirect('crear_feriado')  # Volvemos a la lista

# --- PANEL DE CONFIGURACIÓN ---
@login_required
def configuracion(request):
    if not request.user.is_staff:
        return redirect('home_redirect')
    return render(request, 'core/configuracion.html')

@login_required
def calendario_api(request):
    eventos = []

    # 1. VACACIONES (Colores dinámicos desde el modelo)
    solicitudes = SolicitudVacaciones.objects.filter(estado='APROBADO')
    for sol in solicitudes:
        eventos.append({
            "title": f"🏖️ {sol.empleado.apellido}",
            "start": sol.fecha_inicio.isoformat(),
            "end": (sol.fecha_fin + timedelta(days=1)).isoformat(),
            "color": sol.empleado.color_calendario,  # <--- MAGIA DRY: El modelo decide el color
            "url": reverse('detalle_empleado', args=[sol.empleado.id]),
            "extendedProps": {"observaciones": "Vacaciones: " + (sol.observaciones or "")}
        })
    # 2. LICENCIAS (Color Rojo)
    licencias = Licencia.objects.filter(estado='APROBADO')
    for lic in licencias:
        eventos.append({
            "title": f"🚑 {lic.empleado.apellido} ({lic.get_tipo_display()})",
            "start": lic.fecha_inicio.isoformat(),
            "end": (lic.fecha_fin + timedelta(days=1)).isoformat(),
            "color": '#dc3545',
            "url": reverse('detalle_empleado', args=[lic.empleado.id]),
            "extendedProps": {"observaciones": f"Licencia: {lic.get_tipo_display()}"}
        })

    # 3. FERIADOS (Color Amarillo/Naranja - Fondo)
    feriados = Feriado.objects.all()
    for fer in feriados:
        # Evento de fondo (Pinta todo el día)
        eventos.append({
            "start": fer.fecha.isoformat(),
            "end": (fer.fecha + timedelta(days=1)).isoformat(),
            "color": '#ffc107', # Amarillo fuerte
            "display": 'background', # Esto hace que sea un "telón de fondo"
            "allDay": True
        })
        # Evento de Texto (Para que se lea el nombre del feriado)
        eventos.append({
            "title": f"📅 {fer.descripcion}",
            "start": fer.fecha.isoformat(),
            "allDay": True,
            "color": 'transparent', # Transparente para no tapar el fondo
            "textColor": '#000000', # Texto negro
            "classNames": ['fw-bold', 'text-center'] # Clases Bootstrap
        })

    return JsonResponse(eventos, safe=False)


# --- VISTA DE PERFIL INDIVIDUAL ---
@login_required
def detalle_empleado(request, empleado_id):
    """
    Vista detallada del legajo digital y gestión de saldos de un empleado específico.
    
    Permite visualizar el historial de licencias, vacaciones, documentos adjuntos y bolsas 
    vacacionales. Filtra la información en base al rol del usuario conectado.
    
    Args:
        request: Objeto HttpRequest.
        empleado_id (int): Primary Key del Empleado a inspeccionar.

    Returns:
        HttpResponseRender o HttpResponseForbidden: Template 'core/detalle_empleado.html' 
                                                    o página de error 403 si falla el helper de permisos.
    """
    empleado = get_object_or_404(Empleado, pk=empleado_id)

    if not _tiene_permiso_sobre_empleado(request.user, empleado):
        return HttpResponseForbidden("⛔ Acción no autorizada.")

    # 1. Saldos (CORREGIDO)
    # 👇 ANTES: usabas .filter(dias_restantes__gt=0) -> Ocultaba bolsas vacías
    # 👇 AHORA: usamos .all() -> Muestra TODAS las bolsas (0, positivas o negativas)
    bolsas_activas = empleado.bolsas.all().order_by('anio')


    # El saldo total ahora será la suma real de todo (restará si hay negativos)
    saldo_total = bolsas_activas.aggregate(total=Sum('dias_restantes'))['total'] or 0

    # 2. Historial Vacaciones
    historial = empleado.solicitudes.all().order_by('-fecha_inicio')

    # 3. Historial Licencias
    licencias = empleado.licencias.all().order_by('-fecha_inicio')

    permisos = empleado.permisos.all().order_by('-fecha_inicio')

    # Documentos
    documentos = empleado.documentos.all().order_by('-fecha_subida')

    # Usamos select_related para traer los datos del autor en la misma consulta
    auditorias = AuditoriaSaldo.objects.filter(empleado=empleado).select_related('autor').order_by('-fecha')

    # AGREGAR ESTO 👇
    hoy = timezone.now().date()
    # Traemos los próximos 3 feriados a partir de hoy
    proximos_feriados = Feriado.objects.filter(fecha__gte=hoy).order_by('fecha')[:3]

    context = {
        'empleado': empleado,
        'bolsas': bolsas_activas,  # Ahora incluye las vacías
        'saldo_total': saldo_total,
        'historial': historial,
        'licencias': licencias,
        'permisos': permisos,
        'documentos': documentos,
        'auditorias': auditorias,
        'form_documento': DocumentoForm() if request.user.is_staff else None,
        'proximos_feriados': proximos_feriados,
        'hoy': hoy,
    }
    return render(request, 'core/detalle_empleado.html', context)

# --- GESTIÓN DE VACACIONES ---
@login_required
def registrar_vacaciones(request, empleado_id):
    empleado = get_object_or_404(Empleado, pk=empleado_id)

    # Verificación de permisos
    if not _tiene_permiso_sobre_empleado(request.user, empleado):
        return HttpResponseForbidden("⛔ Acción no autorizada.")

    if request.method == 'POST':
        # Pasamos 'empleado' al __init__ del form para sus validaciones internas
        form = SolicitudForm(request.POST, empleado=empleado)

        # 👇 LA LÍNEA MÁGICA QUE SOLUCIONA EL ERROR "RelatedObjectDoesNotExist" 👇
        # Asignamos el empleado a la instancia del modelo ANTES de validar.
        # Así, si el modelo tiene validaciones internas, no fallará.
        form.instance.empleado = empleado

        if form.is_valid():
            solicitud = form.save(commit=False)
            # (El empleado ya está asignado por la línea de arriba, pero no hace daño dejarlo)
            solicitud.empleado = empleado
            solicitud.estado = 'APROBADO' if request.user.is_staff else 'PENDIENTE'
            solicitud.save()

            # --- 📧 NOTIFICACIÓN A RRHH ---
            if not request.user.is_staff:
                correos_rrhh = _obtener_correos_rrhh()  # DRY: Llamada al helper
                if correos_rrhh:
                    asunto = f'✈️ Nueva Solicitud: {empleado.apellido}'
                    mensaje = f'El empleado {empleado.nombre} {empleado.apellido} ha solicitado vacaciones.\n\nDesde: {solicitud.fecha_inicio}\nHasta: {solicitud.fecha_fin}\nDías: {solicitud.dias_totales}\n\nIngresa al sistema para aprobar o rechazar.'
                    enviar_notificacion_email(asunto, mensaje, correos_rrhh)
            # ------------------------------

            messages.success(request, "✅ Solicitud de vacaciones enviada correctamente.")
            return redirect('detalle_empleado', empleado_id=empleado.id)
    else:
        form = SolicitudForm(empleado=empleado)

    return render(request, 'core/form_vacaciones.html', {'form': form, 'empleado': empleado})


# --- GESTIÓN DE LICENCIAS ---
@login_required
def registrar_licencia(request, empleado_id):
    empleado = get_object_or_404(Empleado, pk=empleado_id)

    # Validación de seguridad
    if not _tiene_permiso_sobre_empleado(request.user, empleado):
        return HttpResponseForbidden("⛔ Acción no autorizada.")

    if request.method == 'POST':
        # 👇 CAMBIO CLAVE: Pasamos 'usuario=request.user' para personalizar el mensaje de error
        form = LicenciaForm(request.POST, empleado=empleado, usuario=request.user)

        if form.is_valid():
            licencia = form.save(commit=False)
            licencia.empleado = empleado
            licencia.estado = 'APROBADO' if request.user.is_staff else 'PENDIENTE'
            licencia.save()

            # --- NOTIFICACIÓN MAIL ---
            if not request.user.is_staff:
                correos_rrhh = _obtener_correos_rrhh()  # DRY: Llamada al helper
                if correos_rrhh:
                    tipo_licencia = licencia.get_tipo_display()
                    asunto = f'🚑 Nueva Licencia: {empleado.apellido}'
                    mensaje = f'El empleado {empleado.nombre} {empleado.apellido} ha informado una ausencia.\n\nMotivo: {tipo_licencia}\nDesde: {licencia.fecha_inicio}\nHasta: {licencia.fecha_fin}\n\nIngresa al sistema para ver el certificado o aprobar.'
                    enviar_notificacion_email(asunto, mensaje, correos_rrhh)
            # -------------------------------------

            messages.success(request, "✅ Licencia registrada correctamente.")
            return redirect('detalle_empleado', empleado_id=empleado.id)

        else:
            # Captura de errores (choques de fechas, etc.)
            for field, errors in form.errors.items():
                for error in errors:
                    # El mensaje de error ya viene personalizado desde forms.py
                    messages.error(request, error) # Quitamos el "⛔" manual porque ya viene en el texto del form

    else:
        # 👇 CAMBIO CLAVE AQUÍ TAMBIÉN
        form = LicenciaForm(empleado=empleado, usuario=request.user)

    return render(request, 'core/form_licencia.html', {'form': form, 'empleado': empleado})

# --- GESTIÓN DE LICENCIAS ---
@login_required
def procesar_licencia(request, licencia_id, accion):
    if not request.user.is_staff: return redirect('dashboard')
    licencia = get_object_or_404(Licencia, pk=licencia_id)
    empleado = licencia.empleado

    if accion == 'aprobar':

        # --- 🛡️ VALIDACIÓN DE SUPERPOSICIÓN (NUEVO) ---
        # 1. Chequear si ya tiene VACACIONES aprobadas en esas fechas
        superposicion_vac = SolicitudVacaciones.objects.filter(
            empleado=empleado,
            estado='APROBADO',
            fecha_inicio__lte=licencia.fecha_fin,
            fecha_fin__gte=licencia.fecha_inicio
        ).exists()

        # 2. Chequear si ya tiene OTRA LICENCIA aprobada en esas fechas (excluyendo esta misma)
        superposicion_lic = Licencia.objects.filter(
            empleado=empleado,
            estado='APROBADO',
            fecha_inicio__lte=licencia.fecha_fin,
            fecha_fin__gte=licencia.fecha_inicio
        ).exclude(id=licencia.id).exists()

        if superposicion_vac or superposicion_lic:
            # SI HAY CHOQUE, NO APROBAMOS Y VOLVEMOS AL DASHBOARD
            print(f"❌ ERROR: {empleado.apellido} ya tiene una ausencia aprobada en esas fechas.")
            return redirect('dashboard')
        # ---------------------------------------------

        licencia.estado = 'APROBADO'
        licencia.save()

        # --- 📧 AVISO AL EMPLEADO: APROBADO ---
        if empleado.usuario and empleado.usuario.email:
            asunto = '✅ Licencia Registrada'
            mensaje = f'Hola {empleado.nombre},\n\nTu licencia por {licencia.get_tipo_display()} ha sido registrada y aprobada correctamente.\n\nFechas: {licencia.fecha_inicio} al {licencia.fecha_fin}.'
            enviar_notificacion_email(asunto, mensaje, [empleado.usuario.email])
        # --------------------------------------

    elif accion == 'rechazar':
        licencia.estado = 'RECHAZADO'
        licencia.save()

        # --- 📧 AVISO AL EMPLEADO: RECHAZADO ---
        if empleado.usuario and empleado.usuario.email:
            asunto = '❌ Licencia Observada'
            mensaje = f'Hola {empleado.nombre},\n\nTu carga de licencia ha sido rechazada o requiere correcciones.\nPor favor contacta a RRHH.'
            enviar_notificacion_email(asunto, mensaje, [empleado.usuario.email])
        # ---------------------------------------

    return redirect('dashboard')


# --- PROCESAR SOLICITUD (VACACIONES) ---
@login_required
def procesar_solicitud(request, solicitud_id, accion):
    if not request.user.is_staff: return redirect('dashboard')
    solicitud = get_object_or_404(SolicitudVacaciones, pk=solicitud_id)
    empleado = solicitud.empleado  # Atajo para usar abajo

    if accion == 'rechazar':
        solicitud.estado = 'RECHAZADO'
        solicitud.save()

        # --- 🕵️‍♂️ AUDITORÍA ---
        AuditoriaSaldo.objects.create(
            autor=request.user, 
            empleado=empleado,
            accion=f"❌ Rechazó solicitud de vacaciones del {solicitud.fecha_inicio} al {solicitud.fecha_fin}."
        )

        # --- 📧 EMAIL DE RECHAZO ---
        if empleado.usuario and empleado.usuario.email:
            asunto = '❌ Solicitud de Vacaciones Rechazada'
            mensaje = f'Hola {empleado.nombre},\n\nTu solicitud de vacaciones para las fechas {solicitud.fecha_inicio} al {solicitud.fecha_fin} ha sido rechazada.\n\nPor favor, comunícate con RRHH para más detalles.'
            enviar_notificacion_email(asunto, mensaje, [empleado.usuario.email])
        # ---------------------------

    elif accion == 'aprobar':

        # --- 🛡️ VALIDACIÓN DE SUPERPOSICIÓN (NUEVO) ---
        # 1. Chequear si ya tiene VACACIONES aprobadas en esas fechas
        superposicion_vac = SolicitudVacaciones.objects.filter(
            empleado=empleado,
            estado='APROBADO',
            fecha_inicio__lte=solicitud.fecha_fin,
            fecha_fin__gte=solicitud.fecha_inicio
        ).exclude(id=solicitud.id).exists()

        # 2. Chequear si ya tiene LICENCIA aprobada en esas fechas
        superposicion_lic = Licencia.objects.filter(
            empleado=empleado,
            estado='APROBADO',
            fecha_inicio__lte=solicitud.fecha_fin,
            fecha_fin__gte=solicitud.fecha_inicio
        ).exists()

        if superposicion_vac or superposicion_lic:
            # SI HAY CHOQUE, NO APROBAMOS Y VOLVEMOS AL DASHBOARD
            print(f"❌ ERROR: {empleado.apellido} ya tiene una ausencia aprobada en esas fechas.")
            return redirect('dashboard')
        # ---------------------------------------------

        with transaction.atomic():
            saldo_total = BolsaVacaciones.objects.filter(empleado=solicitud.empleado, dias_restantes__gt=0).aggregate(
                total=Sum('dias_restantes'))['total'] or 0

            # Validación de Saldo
            if saldo_total < solicitud.dias_totales:
                solicitud.estado = 'RECHAZADO'  # Opcional: podrías rechazarla automáticamente si no tiene saldo
                solicitud.save()
                return redirect('dashboard')

            # Descuento de días
            dias_a_descontar = solicitud.dias_totales
            bolsas = BolsaVacaciones.objects.filter(empleado=solicitud.empleado,
                                                    dias_restantes__gt=0).select_for_update().order_by('anio')

            for bolsa in bolsas:
                if dias_a_descontar == 0: break
                descuento = min(bolsa.dias_restantes, dias_a_descontar)
                bolsa.dias_restantes -= descuento
                bolsa.save()
                ConsumoDetalle.objects.create(solicitud=solicitud, bolsa=bolsa, dias_descontados=descuento)
                dias_a_descontar -= descuento

            solicitud.estado = 'APROBADO'
            solicitud.save()

            # --- 🕵️‍♂️ AUDITORÍA ---
            AuditoriaSaldo.objects.create(
                autor=request.user, 
                empleado=empleado,
                accion=f"✅ Aprobó vacaciones de {solicitud.dias_totales} días (del {solicitud.fecha_inicio} al {solicitud.fecha_fin})."
            )

            # --- 📧 EMAIL DE APROBACIÓN ---
            if empleado.usuario and empleado.usuario.email:
                asunto = '✅ Vacaciones Aprobadas'
                mensaje = f'¡Buenas noticias {empleado.nombre}!\n\nTus vacaciones han sido aprobadas.\nFechas: {solicitud.fecha_inicio} al {solicitud.fecha_fin}\n\n¡Que descanses!'
                enviar_notificacion_email(asunto, mensaje, [empleado.usuario.email])
            # ------------------------------

    return redirect('dashboard')


@login_required
def editar_empleado(request, empleado_id):  # Usamos 'id' para coincidir con la URL
    # 1. Seguridad (Se mantiene)
    if not request.user.is_staff:
        return HttpResponseForbidden("No autorizado")

    empleado = get_object_or_404(Empleado, pk=empleado_id)

    # 2. Preparamos datos iniciales (Se mantiene tu lógica de email)
    initial_data = {}
    if empleado.usuario:
        initial_data['email'] = empleado.usuario.email

    if request.method == 'POST':
        # 👇 CAMBIO CLAVE: Usamos EmpleadoEditarForm en lugar de EmpleadoForm
        form = EmpleadoEditarForm(request.POST, instance=empleado)

        if form.is_valid():
            # Guardamos el empleado
            empleado = form.save()

            # 3. Actualizamos el email del Usuario vinculado (Se mantiene tu lógica)
            nuevo_email = form.cleaned_data.get('email')
            if empleado.usuario:
                empleado.usuario.email = nuevo_email
                empleado.usuario.save()

            # Redirigimos a la lista (o podés dejar 'detalle_empleado' si preferís)
            return redirect('lista_empleados')
    else:
        # 👇 CAMBIO CLAVE: Inicializamos el form de edición
        form = EmpleadoEditarForm(instance=empleado, initial=initial_data)

    # 👇 CAMBIO DE TEMPLATE: Usamos el específico para editar
    return render(request, 'core/editar_empleado.html', {
        'form': form,
        'empleado': empleado
    })
# --- REPORTES Y OTROS ---
@login_required
def exportar_saldos_csv(request):
    if not request.user.is_staff: return HttpResponseForbidden("Acceso denegado")
    
    # Truco Pro: utf-8-sig le pone el BOM para que Excel lea perfecto las tildes y eñes
    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = 'attachment; filename="reporte_saldos_detallado.csv"'
    
    # En Argentina/Latam, Excel suele usar el punto y coma como separador natural de columnas
    writer = csv.writer(response, delimiter=';') 
    
    # 1. Nuevas Cabeceras
    writer.writerow(['Legajo', 'Apellido', 'Nombre', 'DNI', 'Fecha Ingreso', 'Detalle por Periodo', 'Saldo Total'])
    
    # Optimización DB: prefetch_related trae todas las bolsas de una sola vez para evitar 100 consultas SQL
    empleados = Empleado.objects.filter(activo=True).prefetch_related('bolsas').order_by('apellido')
    
    for emp in empleados:
        # Obtenemos todas las bolsas ya precargadas en memoria
        bolsas = emp.bolsas.all()
        
        # Calculamos el total sumando las bolsas
        saldo_total = sum(b.dias_restantes for b in bolsas)
        
        # Filtramos en memoria solo las que tienen días a favor y armamos el texto
        bolsas_activas = [b for b in bolsas if b.dias_restantes > 0]
        detalle_periodos = " | ".join([f"Año {b.anio}: {b.dias_restantes} días" for b in bolsas_activas])
        
        if not detalle_periodos:
            detalle_periodos = "Sin saldo"
            
        # Formateamos la fecha de ingreso (ej: 15/03/2021)
        fecha_ingreso_str = emp.fecha_ingreso.strftime("%d/%m/%Y") if emp.fecha_ingreso else ""
        
        # 2. Escribimos la fila con los nuevos datos
        writer.writerow([
            emp.legajo, 
            emp.apellido, 
            emp.nombre, 
            emp.dni,
            fecha_ingreso_str,
            detalle_periodos,
            saldo_total
        ])
        
    return response


@login_required
def exportar_historial_csv(request, empleado_id):
    empleado = get_object_or_404(Empleado, pk=empleado_id)
    if not _tiene_permiso_sobre_empleado(request.user, empleado): return HttpResponseForbidden("⛔ Acción no autorizada.")
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="historial_{empleado.apellido}.csv"'
    writer = csv.writer(response)
    writer.writerow(['Inicio', 'Fin', 'Días', 'Estado'])
    for sol in empleado.solicitudes.all(): writer.writerow(
        [sol.fecha_inicio, sol.fecha_fin, sol.dias_totales, sol.estado])
    return response


# --- Generar PDF Solicitud (Vacaciones) ---
@login_required
def generar_pdf_solicitud(request, solicitud_id):
    # Optimización DB: select_related para traer al empleado en la misma consulta
    solicitud = get_object_or_404(SolicitudVacaciones.objects.select_related('empleado'), pk=solicitud_id)
    empleado = solicitud.empleado

    # Verificación de permisos
    if not _tiene_permiso_sobre_empleado(request.user, empleado):
        return HttpResponseForbidden("⛔ Acción no autorizada.")

    # 👇 NUEVO CANDADO: Bloquear PDFs de solicitudes rechazadas 👇
    if solicitud.estado == 'RECHAZADO':
        messages.error(request, "⛔ El documento carece de validez porque la solicitud fue rechazada.")
        return redirect('detalle_empleado', empleado_id=empleado.id)

    # --- LÓGICA PARA EL DESGLOSE DETALLADO DEL SALDO ---
    saldo_db = empleado.bolsas.aggregate(total=Sum('dias_restantes'))['total'] or 0
    bolsas_activas = empleado.bolsas.filter(dias_restantes__gt=0).order_by('anio')
    desglose_lista = []

    if solicitud.estado == 'PENDIENTE':
        saldo_para_mostrar = saldo_db - solicitud.dias_totales
        dias_a_descontar = solicitud.dias_totales

        # Simulamos el descuento para saber cómo quedarán las bolsas
        for bolsa in bolsas_activas:
            if dias_a_descontar <= 0:
                desglose_lista.append(f"{bolsa.dias_restantes} días (del periodo {bolsa.anio})")
            else:
                if bolsa.dias_restantes > dias_a_descontar:
                    saldo_simulado = bolsa.dias_restantes - dias_a_descontar
                    desglose_lista.append(f"{saldo_simulado} días (del periodo {bolsa.anio})")
                    dias_a_descontar = 0
                else:
                    dias_a_descontar -= bolsa.dias_restantes
    else:
        # Si ya está aprobada, la base de datos ya tiene el descuento hecho
        saldo_para_mostrar = saldo_db
        for bolsa in bolsas_activas:
            desglose_lista.append(f"{bolsa.dias_restantes} días (del periodo {bolsa.anio})")

    # Armamos la frase final bonita ("X, Y y Z")
    if not desglose_lista:
        texto_desglose = "0 días"
    elif len(desglose_lista) == 1:
        texto_desglose = desglose_lista[0]
    else:
        # Une con comas todos menos el último, y el último con una "y"
        texto_desglose = ", ".join(desglose_lista[:-1]) + " y " + desglose_lista[-1]
    # ----------------------------------------------------

    context = {
        'solicitud': solicitud,
        'empleado': empleado,
        'detalles': solicitud.detalles.all(),
        'saldo_actual': saldo_para_mostrar, 
        'texto_desglose': texto_desglose,  # <--- Pasamos la frase armanda al PDF
        'hoy': timezone.now()
    }

    html = get_template('core/pdf_solicitud.html').render(context)
    response = HttpResponse(content_type='application/pdf')
    filename = f"Solicitud_{empleado.apellido}_{solicitud.fecha_inicio}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'

    pisa_status = pisa.CreatePDF(html, dest=response)
    if pisa_status.err:
        return HttpResponse('Error al generar PDF')

    return response

@login_required
def lista_empleados(request):
    if not request.user.is_staff: return redirect('home_redirect')

    # Empezamos con todos los empleados activos (Total Nómina)
    empleados = Empleado.objects.filter(activo=True).order_by('apellido')

    # --- 0. FILTROS RÁPIDOS DESDE EL DASHBOARD (NUEVO) ---
    filtro_rapido = request.GET.get('filtro')
    hoy = timezone.now().date()

    if filtro_rapido == 'vacaciones':
        # Filtramos solo los que tienen una solicitud aprobada HOY
        ids_vacaciones = SolicitudVacaciones.objects.filter(
            estado='APROBADO',
            fecha_inicio__lte=hoy,
            fecha_fin__gte=hoy
        ).values_list('empleado_id', flat=True)

        empleados = empleados.filter(id__in=ids_vacaciones)

    elif filtro_rapido == 'presentes':
        # Lógica inversa: Excluimos a los que están de Vacaciones O Licencia
        ids_vacaciones = SolicitudVacaciones.objects.filter(
            estado='APROBADO', fecha_inicio__lte=hoy, fecha_fin__gte=hoy
        ).values_list('empleado_id', flat=True)

        ids_licencias = Licencia.objects.filter(
            estado='APROBADO', fecha_inicio__lte=hoy, fecha_fin__gte=hoy
        ).values_list('empleado_id', flat=True)

        # Unimos las dos listas de ausentes
        ids_ausentes = list(ids_vacaciones) + list(ids_licencias)

        # "Exclude" saca a esa gente de la lista
        empleados = empleados.exclude(id__in=ids_ausentes)

    # -----------------------------------------------------

    # 1. BÚSQUEDA POR TEXTO (Nombre, Apellido o Legajo)
    busqueda = request.GET.get('q')
    if busqueda:
        empleados = empleados.filter(
            Q(nombre__icontains=busqueda) |
            Q(apellido__icontains=busqueda) |
            Q(legajo__icontains=busqueda)
        )

    # 2. FILTRO POR SECTOR
    sector_filtro = request.GET.get('sector')
    if sector_filtro:
        empleados = empleados.filter(sector=sector_filtro)

    # 3. ORDENAR POR ANTIGÜEDAD
    orden = request.GET.get('orden')
    if orden == 'antiguedad_mayor':
        empleados = empleados.order_by('fecha_ingreso')
    elif orden == 'antiguedad_menor':
        empleados = empleados.order_by('-fecha_ingreso')

    opciones_sector = Empleado.SECTORES

    context = {
        'empleados': empleados,
        'opciones_sector': opciones_sector,
        'filtro_actual': {
            'q': busqueda or '',
            'sector': sector_filtro or '',
            'orden': orden or '',
            'tipo': filtro_rapido or ''  # Para saber si estamos filtrando visualmente
        }
    }
    return render(request, 'core/lista_empleados.html', context)


@login_required
def crear_empleado(request):
    if not request.user.is_staff: return HttpResponseForbidden("No autorizado")
    if request.method == 'POST':
        form = EmpleadoForm(request.POST)
        if form.is_valid(): form.save(); return redirect('lista_empleados')
    else:
        form = EmpleadoForm()
    return render(request, 'core/form_empleado.html', {'form': form})


@login_required
def eliminar_empleado(request, empleado_id):
    if not request.user.is_staff: return HttpResponseForbidden("No autorizado")
    empleado = get_object_or_404(Empleado, pk=empleado_id)
    if request.method == 'POST': empleado.delete(); return redirect('lista_empleados')
    return render(request, 'core/confirmar_eliminar_empleado.html', {'empleado': empleado})


@login_required
def cargar_saldo_historico(request, empleado_id):
    if not request.user.is_staff: return redirect('home_redirect')
    empleado = get_object_or_404(Empleado, pk=empleado_id)

    if request.method == 'POST':
        form = BolsaManualForm(request.POST, empleado=empleado)
        if form.is_valid():
            b = form.save(commit=False)
            b.empleado = empleado
            b.dias_otorgados = b.dias_restantes
            b.save()

            # 1. MENSAJE DE ÉXITO (VERDE) ✅
            messages.success(request, f"✅ Se cargó la bolsa del {b.anio} para {empleado.nombre}.")

            # Ahora usa la variable 'b' (bolsa) que acabamos de crear, en vez de 'solicitud'.
            AuditoriaSaldo.objects.create(
                autor=request.user,
                empleado=empleado,
                accion=f"⚖️ CARGA MANUAL: Se creó bolsa del año {b.anio} con {b.dias_restantes} días para {empleado.apellido}, {empleado.nombre}."
            )

            return redirect('detalle_empleado', empleado_id=empleado.id)
        else:
            # 2. MENSAJE DE ERROR (ROJO) ❌
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, error)
    else:
        form = BolsaManualForm(empleado=empleado)

    return render(request, 'core/form_bolsa.html', {'form': form, 'empleado': empleado})

@login_required
def editar_bolsa(request, bolsa_id):
    if not request.user.is_staff:
        return HttpResponseForbidden("No autorizado")

    bolsa = get_object_or_404(BolsaVacaciones, pk=bolsa_id)
    empleado = bolsa.empleado

    # 1. 🧮 CÁLCULO DE CONSUMO PREVIO
    consumo_real = bolsa.detalles.aggregate(total=Sum('dias_descontados'))['total'] or 0
    saldo_anterior_db = bolsa.dias_restantes

    if request.method == 'POST':
        form = BolsaManualForm(request.POST, instance=bolsa, empleado=empleado)

        if form.is_valid():
            # El usuario ingresó el TOTAL (ej: 3)
            total_ingresado = form.cleaned_data['dias_restantes']

            # 👇 VALIDACIÓN DE SEGURIDAD MATEMÁTICA 👇
            # No podemos decir que el total del año es 3 si ya se gastó 12.
            if total_ingresado < consumo_real:
                messages.error(request,
                    f"⛔ ERROR: No puedes definir un total de {total_ingresado} días porque el empleado ya consumió {consumo_real}. "
                    f"El total debe ser igual o mayor al consumo.")
                # Recargamos la página para que corrija el dato
                return redirect('editar_bolsa', bolsa_id=bolsa.id)
            # 👆 FIN DE VALIDACIÓN 👆

            # 2. 🧠 LÓGICA DE RESTA AUTOMÁTICA
            nuevo_saldo_real = total_ingresado - consumo_real

            # Sobrescribimos el valor en la instancia antes de guardar
            bolsa.dias_restantes = nuevo_saldo_real
            bolsa.save()

            # --- 🕵️‍♂️ AUDITORÍA ---
            if saldo_anterior_db != bolsa.dias_restantes:
                AuditoriaSaldo.objects.create(
                    autor=request.user,
                    empleado=empleado,
                    accion=f"Editó bolsa {bolsa.anio}: Usuario ingresó total {total_ingresado}. "
                           f"Se descontó consumo ({consumo_real}). Saldo ajustado de {saldo_anterior_db} a {bolsa.dias_restantes}."
                )

            messages.success(request,
                             f"✅ Saldo actualizado. Total {total_ingresado} - Consumidos {consumo_real} = {nuevo_saldo_real} disponibles.")
            return redirect('detalle_empleado', empleado_id=empleado.id)
    else:
        # 3. 👁️ TRUCO VISUAL PARA EL GET
        # Mostramos el TOTAL (Saldo + Consumo) para que sea fácil de entender
        bolsa_visual = bolsa
        bolsa_visual.dias_restantes = bolsa.dias_restantes + consumo_real

        form = BolsaManualForm(instance=bolsa_visual, empleado=empleado)

    return render(request, 'core/form_bolsa.html', {
        'form': form,
        'empleado': empleado,
        'es_edicion': True,
        'consumo': consumo_real
    })

@login_required
def eliminar_bolsa(request, bolsa_id):
    # 1. Seguridad: Solo Admin/Staff
    if not request.user.is_staff:
        return HttpResponseForbidden("No autorizado")

    bolsa = get_object_or_404(BolsaVacaciones, pk=bolsa_id)
    empleado = bolsa.empleado
    anio_borrado = bolsa.anio  # Guardamos el dato antes de borrarlo

    # 2. Auditoría (Registrar quién lo borró) 🕵️‍♂️
    AuditoriaSaldo.objects.create(
        autor=request.user,
        empleado=empleado,
        accion=f"🗑️ Eliminó manualmente la bolsa del año {anio_borrado} ({bolsa.dias_restantes} días)."
    )

    # 3. Eliminar la bolsa 🗑️
    bolsa.delete()

    # 4. Mensaje Flotante de Éxito ✅
    messages.success(request, f"🗑️ La bolsa del año {anio_borrado} fue eliminada correctamente.")

    return redirect('detalle_empleado', empleado_id=empleado.id)


@login_required
def gestion_usuarios(request):
    if not request.user.is_staff: return redirect('home_redirect')

    # 1. PROCESAR FORMULARIO
    if request.method == 'POST':
        form = CrearUsuarioForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                # Creamos el usuario base
                u = User.objects.create_user(
                    username=data['username'],
                    password=data['password'],
                    email=data['email']
                )

                # LÓGICA DE VINCULACIÓN
                if data['empleado']:
                    # Si eligió empleado, lo vinculamos
                    emp = data['empleado']
                    emp.usuario = u
                    emp.save()
                    if data.get('es_admin'):
                        u.is_staff = True
                        u.save()
                else:
                    # Si NO eligió empleado, es un Admin Puro (Soporte/IT)
                    # OBLIGATORIAMENTE debe ser staff, sino no podrá entrar a nada.
                    u.is_staff = True
                    u.save()

                return redirect('gestion_usuarios')
            except Exception as e:
                print(f"Error creando usuario: {e}")
    else:
        form = CrearUsuarioForm()

    # 2. LISTAR USUARIOS (User) EN LUGAR DE EMPLEADOS
    # Usamos select_related para traer el empleado si existe, y evitar consultas lentas
    usuarios = User.objects.select_related('empleado').all().order_by('-is_staff', 'username')

    # Filtros de búsqueda
    busqueda = request.GET.get('q')
    if busqueda:
        usuarios = usuarios.filter(
            Q(username__icontains=busqueda) |
            Q(first_name__icontains=busqueda) |
            Q(last_name__icontains=busqueda) |
            Q(empleado__nombre__icontains=busqueda) |  # Busca también por el nombre del empleado vinculado
            Q(empleado__apellido__icontains=busqueda)
        )

    return render(request, 'core/gestion_usuarios.html', {
        'form': form,
        'usuarios': usuarios,  # <--- Ahora pasamos 'usuarios', no 'empleados_con_usuario'
        'busqueda_actual': busqueda or ''
    })

@login_required
def eliminar_usuario(request, empleado_id):
    if not request.user.is_staff: return redirect('home_redirect')
    empleado = get_object_or_404(Empleado, pk=empleado_id)
    if empleado.usuario:
        u = empleado.usuario
        empleado.usuario = None
        empleado.save()
        u.delete()
    return redirect('gestion_usuarios')

# --- Generar PDF Licencia (Médica/Ausencias) ---
@login_required
def generar_pdf_licencia(request, licencia_id):
    # Optimización DB
    licencia = get_object_or_404(Licencia.objects.select_related('empleado'), pk=licencia_id)
    empleado = licencia.empleado

    # Verificación de permisos
    if not _tiene_permiso_sobre_empleado(request.user, empleado):
        return HttpResponseForbidden("⛔ Acción no autorizada para ver este documento.")

    context = {
        'licencia': licencia,
        'empleado': empleado,
        'hoy': timezone.now()  # <--- FIX: Objeto datetime completo
    }

    html = get_template('core/pdf_licencia.html').render(context)
    response = HttpResponse(content_type='application/pdf')
    filename = f"Licencia_{licencia.get_tipo_display()}_{empleado.apellido}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'

    pisa_status = pisa.CreatePDF(html, dest=response)
    if pisa_status.err:
        return HttpResponse('Error al generar PDF')

    return response


# --- GESTIÓN DE PERMISOS (HOME OFFICE / OTROS) ---
@login_required
def registrar_permiso(request, empleado_id):
    empleado = get_object_or_404(Empleado, pk=empleado_id)

    # Verificación de permisos
    if not _tiene_permiso_sobre_empleado(request.user, empleado):
        return HttpResponseForbidden("⛔ Acción no autorizada.")

    if request.method == 'POST':
        # 👇 CAMBIO CRÍTICO: Pasamos empleado=empleado para activar la validación
        form = PermisoForm(request.POST, empleado=empleado)

        if form.is_valid():
            permiso = form.save(commit=False)
            permiso.empleado = empleado
            permiso.estado = 'APROBADO' if request.user.is_staff else 'PENDIENTE'
            permiso.save()

            # --- 📧 NOTIFICACIÓN A RRHH ---
            if not request.user.is_staff:
                correos_rrhh = _obtener_correos_rrhh()  # DRY: Llamada al helper
                if correos_rrhh:
                    asunto = f'🏠 Nuevo Permiso: {empleado.apellido}'
                    mensaje = f'{empleado.nombre} solicita: {permiso.get_tipo_display()}.\nFechas: {permiso.fecha_inicio} al {permiso.fecha_fin}.\nMotivo: {permiso.motivo}'
                    enviar_notificacion_email(asunto, mensaje, correos_rrhh)
            # ------------------------------

            # ✅ Feedback visual
            messages.success(request, "✅ Solicitud de permiso registrada correctamente.")

            return redirect('detalle_empleado', empleado_id=empleado.id)
    else:
        # 👇 CAMBIO CRÍTICO AQUÍ TAMBIÉN
        form = PermisoForm(empleado=empleado)

    return render(request, 'core/form_permiso.html', {'form': form, 'empleado': empleado})

@login_required
def procesar_permiso(request, permiso_id, accion):
    if not request.user.is_staff: return redirect('dashboard')
    permiso = get_object_or_404(Permiso, pk=permiso_id)

    if accion == 'aprobar':
        permiso.estado = 'APROBADO'
        permiso.save()
        # Email de Aprobación
        if permiso.empleado.usuario and permiso.empleado.usuario.email:
            asunto = '✅ Permiso Aprobado'
            mensaje = f'Tu solicitud de {permiso.get_tipo_display()} ha sido aprobada.'
            enviar_notificacion_email(asunto, mensaje, [permiso.empleado.usuario.email])

    elif accion == 'rechazar':
        permiso.estado = 'RECHAZADO'
        permiso.save()
        # Email de Rechazo
        if permiso.empleado.usuario and permiso.empleado.usuario.email:
            asunto = '❌ Permiso Denegado'
            mensaje = f'Tu solicitud de {permiso.get_tipo_display()} no fue autorizada. Consulta con RRHH.'
            enviar_notificacion_email(asunto, mensaje, [permiso.empleado.usuario.email])

    return redirect('dashboard')

# --- Generar PDF Permiso (Home Office/Otros) ---
@login_required
def generar_pdf_permiso(request, permiso_id):
    # Optimización DB
    permiso = get_object_or_404(Permiso.objects.select_related('empleado'), pk=permiso_id)
    empleado = permiso.empleado

    # Verificación de permisos
    if not _tiene_permiso_sobre_empleado(request.user, empleado):
        return HttpResponseForbidden("⛔ Acción no autorizada para ver este documento.")

    context = {
        'permiso': permiso,
        'empleado': empleado,
        'hoy': timezone.now()  # <--- FIX: Objeto datetime completo
    }

    html = get_template('core/pdf_permiso.html').render(context)
    response = HttpResponse(content_type='application/pdf')
    filename = f"Permiso_{permiso.get_tipo_display()}_{empleado.apellido}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'

    pisa_status = pisa.CreatePDF(html, dest=response)
    if pisa_status.err:
        return HttpResponse('Error al generar PDF')

    return response

# -- Funciones de subir (también notifica via mail) y borrar recibos de sueldo -- #
@login_required
def subir_documento(request, empleado_id):
    # Seguridad básica
    if not request.user.is_staff:
        messages.error(request, "⛔ No autorizado.")
        return redirect('dashboard')

    empleado = get_object_or_404(Empleado, pk=empleado_id)

    if request.method == 'POST':
        form = DocumentoForm(request.POST, request.FILES)

        if form.is_valid():
            # 1. Guardar
            doc = form.save(commit=False)
            doc.empleado = empleado
            doc.save()

            # 2. Auditoría (Opcional pero útil)
            AuditoriaSaldo.objects.create(
                autor=request.user,
                empleado=empleado,
                accion=f"📂 Subió documento: {doc.titulo}"
            )

            # 3. Notificación Email (Refactorizado)
            if empleado.usuario and empleado.usuario.email:
                asunto = f'📄 Nuevo Documento: {doc.titulo}'
                mensaje = f'Hola {empleado.nombre},\n\nRRHH ha cargado un nuevo archivo: "{doc.titulo}".'
                enviar_notificacion_email(asunto, mensaje, [empleado.usuario.email])

            messages.success(request, "✅ Documento subido correctamente.")
            return redirect('detalle_empleado', empleado_id=empleado.id)

        else:
            # 👇 AQUÍ ESTÁ LA CLAVE: Capturar los errores de validación del Modelo
            # Si suben un .exe, el modelo lanza error y aquí lo atrapamos para mostrarlo.
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"⛔ {error}")  # Ej: "Formato no permitido..."

            for error in form.non_field_errors():
                messages.error(request, f"⛔ {error}")

    return redirect('detalle_empleado', empleado_id=empleado.id)


@login_required
def eliminar_documento(request, documento_id):
    if not request.user.is_staff:
        messages.error(request, "⛔ No autorizado.")
        return redirect('dashboard')

    doc = get_object_or_404(Documento, pk=documento_id)
    empleado_id = doc.empleado.id
    titulo = doc.titulo

    doc.delete()  # Esto borra el archivo físico también

    # Feedback visual
    messages.warning(request, f"🗑️ Se eliminó el documento: {titulo}")

    return redirect('detalle_empleado', empleado_id=empleado_id)


@login_required
def eliminar_usuario_sistema(request, user_id):
    if not request.user.is_staff: return redirect('home_redirect')

    user_a_borrar = get_object_or_404(User, pk=user_id)

    # Seguridad: No puedes borrarte a ti mismo
    if user_a_borrar.id == request.user.id:
        return redirect('gestion_usuarios')

    # Borramos el usuario definitivamente
    user_a_borrar.delete()

    return redirect('gestion_usuarios')

# -- editar usuario login -- #
@login_required
def editar_usuario(request, user_id):
    if not request.user.is_staff: return redirect('home_redirect')

    usuario_editar = get_object_or_404(User, pk=user_id)
    es_propio = (usuario_editar.id == request.user.id)

    if request.method == 'POST':
        # 1. Email (Para todos)
        usuario_editar.email = request.POST.get('email')

        if es_propio:
            # A. Cambio de Nombre de Usuario
            nuevo_username = request.POST.get('username')
            if nuevo_username and nuevo_username != usuario_editar.username:
                if User.objects.filter(username=nuevo_username).exists():
                    messages.error(request, "❌ Ese nombre de usuario ya existe.")
                    return render(request, 'core/form_editar_usuario.html',
                                  {'usuario': usuario_editar, 'es_propio': es_propio})
                usuario_editar.username = nuevo_username

            # B. Cambio de Contraseña (Lógica Segura)
            pass_actual = request.POST.get('old_password')
            pass_nueva1 = request.POST.get('new_password_1')
            pass_nueva2 = request.POST.get('new_password_2')

            # Solo intentamos cambiar la clave si escribió algo en los campos nuevos
            if pass_nueva1:
                # 1. Validar que puso la clave actual y es correcta
                if not pass_actual or not usuario_editar.check_password(pass_actual):
                    messages.error(request, "❌ La contraseña actual es incorrecta.")
                    return render(request, 'core/form_editar_usuario.html',
                                  {'usuario': usuario_editar, 'es_propio': es_propio})

                # 2. Validar que las nuevas coincidan
                if pass_nueva1 != pass_nueva2:
                    messages.error(request, "❌ Las nuevas contraseñas no coinciden.")
                    return render(request, 'core/form_editar_usuario.html',
                                  {'usuario': usuario_editar, 'es_propio': es_propio})

                # 3. Todo OK -> Guardar
                usuario_editar.set_password(pass_nueva1)
                usuario_editar.save()
                update_session_auth_hash(request, usuario_editar)  # Mantiene sesión
                messages.success(request, "✅ Contraseña actualizada correctamente.")

        else:
            # Lógica para editar a OTROS (Solo rol)
            usuario_editar.is_staff = (request.POST.get('es_admin') == 'on')

        usuario_editar.save()

        # Si no hubo errores de contraseña, volvemos a la lista
        if not es_propio or not request.POST.get('new_password_1'):
            return redirect('gestion_usuarios')

    return render(request, 'core/form_editar_usuario.html', {
        'usuario': usuario_editar,
        'es_propio': es_propio
    })


@login_required
def ejecutar_renovacion_anual(request):
    if not request.user.is_staff: return redirect('dashboard')

    out = StringIO()
    try:
        call_command('renovar_vacaciones', stdout=out)
        mensaje_salida = out.getvalue().strip()  # Limpiamos espacios extra

        # Detectamos qué tipo de mensaje es para elegir el color
        if "⛔" in mensaje_salida:
            messages.error(request, mensaje_salida)
        elif "✅" in mensaje_salida:
            messages.success(request, mensaje_salida)
        else:
            # Caso "Sin cambios"
            messages.info(request, mensaje_salida)

    except Exception as e:
        messages.error(request, f"❌ Error técnico: {e}")

    return redirect('dashboard')

# --- EDICIÓN DE VACACIONES (VERSIÓN FINAL: SOLO STOP EN APROBADAS) ---
@login_required
def editar_solicitud(request, solicitud_id):
    solicitud = get_object_or_404(SolicitudVacaciones, pk=solicitud_id)
    empleado = solicitud.empleado

    hoy = timezone.now().date()

    # 1. PERMISOS
    es_rrhh = request.user.is_staff
    es_dueno = (hasattr(request.user, 'empleado') and request.user.empleado == empleado)

    if not es_rrhh and not es_dueno:
        messages.error(request, "⛔ No tienes permiso.")
        return redirect('detalle_empleado', empleado_id=empleado.id)

    # 2. RESTRICCIONES DE ESTADO (Empleado solo Pendientes)
    if es_dueno and not es_rrhh and solicitud.estado != 'PENDIENTE':
        messages.error(request, "⛔ No puedes editar vacaciones ya aprobadas. Contacta a RRHH.")
        return redirect('detalle_empleado', empleado_id=empleado.id)
    
    # PROHIBIR EDICIÓN DE PERÍODOS FINALIZADOS 
    if solicitud.fecha_fin < hoy:
        messages.error(request, "⛔ PROHIBIDO: Este período de vacaciones ya finalizó y fue liquidado. No se puede modificar.")
        return redirect('detalle_empleado', empleado_id=empleado.id)

    # 3. DATOS VIEJOS (Para comparar)
    fecha_inicio_vieja = solicitud.fecha_inicio
    fecha_fin_vieja = solicitud.fecha_fin
    dias_viejos = (fecha_fin_vieja - fecha_inicio_vieja).days + 1

    if request.method == 'POST':
        form = SolicitudForm(request.POST, instance=solicitud, empleado=empleado)
        form.instance.empleado = empleado

        if form.is_valid():
            nueva_solicitud = form.save(commit=False)

            # --- CASO A: PENDIENTE (Edición Libre) ---
            if solicitud.estado == 'PENDIENTE':
                nueva_solicitud.save()
                messages.success(request, "📝 Solicitud pendiente actualizada.")

            # --- CASO B: APROBADA (Solo Stop / Recorte) ---
            elif solicitud.estado == 'APROBADO':
                dias_nuevos = (nueva_solicitud.fecha_fin - nueva_solicitud.fecha_inicio).days + 1
                diferencia_a_devolver = dias_viejos - dias_nuevos

                # 👇 CANDADO NUEVO: PROHIBIDO EXTENDER 👇
                if diferencia_a_devolver < 0:  # Si da negativo, es que pidió MÁS días
                    messages.error(request,
                                   "⛔ PROHIBIDO: No puedes extender una vacación ya aprobada. Solo puedes acortarla (Stop). Para agregar días, crea una NUEVA solicitud.")
                    return redirect('detalle_empleado', empleado_id=empleado.id)

                # 👇 CANDADO EXTRA: PROHIBIDO MOVER FECHAS (SHIFT) SIN RECORTAR 👇
                # Si la cantidad de días es igual, pero cambiaron las fechas, también bloqueamos
                # porque podría cambiar el año de la bolsa y romper la contabilidad.
                if diferencia_a_devolver == 0 and nueva_solicitud.fecha_inicio != fecha_inicio_vieja:
                    messages.error(request,
                                   "⛔ PROHIBIDO: No puedes mover las fechas de una vacación aprobada sin acortarla. Cancélala y crea una nueva.")
                    return redirect('detalle_empleado', empleado_id=empleado.id)

                # --- SI PASA LOS CANDADOS, ES UN RECORTE VÁLIDO ---

                # 1. Guardamos la nueva fecha (El Stop)
                nueva_solicitud.save()

                # 2. Lógica de Restitución Histórica (Tu lógica aprobada)
                if diferencia_a_devolver > 0:
                    detalles_consumo = nueva_solicitud.detalles.all().order_by('-bolsa__anio')
                    remanente = diferencia_a_devolver

                    for detalle in detalles_consumo:
                        if remanente <= 0: break

                        bolsa = detalle.bolsa
                        dias_en_este_detalle = detalle.dias_descontados

                        if remanente >= dias_en_este_detalle:
                            bolsa.dias_restantes += dias_en_este_detalle
                            bolsa.save()
                            AuditoriaSaldo.objects.create(
                                autor=request.user, empleado=empleado,
                                accion=f"♻️ Restitución Total ({bolsa.anio}): Se devolvieron {dias_en_este_detalle} días."
                            )
                            remanente -= dias_en_este_detalle
                            detalle.delete()
                        else:
                            bolsa.dias_restantes += remanente
                            bolsa.save()
                            detalle.dias_descontados -= remanente
                            detalle.save()
                            AuditoriaSaldo.objects.create(
                                autor=request.user, empleado=empleado,
                                accion=f"♻️ Restitución Parcial ({bolsa.anio}): Se devolvieron {remanente} días."
                            )
                            remanente = 0

                    messages.warning(request,
                                     f"✅ Vacaciones interrumpidas. Se restituyeron {diferencia_a_devolver} días.")

            return redirect('detalle_empleado', empleado_id=empleado.id)
    else:
        form = SolicitudForm(instance=solicitud, empleado=empleado)

    return render(request, 'core/form_vacaciones.html', {
        'form': form,
        'empleado': empleado,
        'es_edicion': True
    })

# --- ELIMINAR VACACIONES Y DEVOLVER SALDO ---
@login_required
def eliminar_solicitud(request, solicitud_id):
    solicitud = get_object_or_404(SolicitudVacaciones, pk=solicitud_id)
    empleado = solicitud.empleado

    # 1. CANDADO DE SEGURIDAD 🔒
    # Solo RRHH puede eliminar. El empleado debe pedirlo.
    if not request.user.is_staff:
        messages.error(request, "⛔ No puedes eliminar una solicitud. Contacta a RRHH para que la rechacen.")
        return redirect('detalle_empleado', empleado_id=empleado.id)

    if request.method == 'POST':
        # 2. LÓGICA DE DEVOLUCIÓN INTELIGENTE 🧠
        # Solo devolvemos días si la solicitud tenía "consumo" registrado (estaba Aprobada o con detalles)
        detalles = solicitud.detalles.all()
        dias_devueltos = 0

        if detalles.exists():
            # Si hay detalles, es porque se descontaron días. Los devolvemos.
            for detalle in detalles:
                bolsa = detalle.bolsa
                bolsa.dias_restantes += detalle.dias_descontados
                bolsa.save()
                dias_devueltos += detalle.dias_descontados

            # Mensaje para cuando SÍ se devuelve saldo
            mensaje_accion = f"Se restituyeron {dias_devueltos} días a la bolsa de {empleado.apellido}, {empleado.nombre}."

        else:
            # Si NO hay detalles (ej: estaba PENDIENTE), no devolvemos nada porque nunca se restaron.
            # Solo borramos el registro.
            mensaje_accion = "La solicitud estaba PENDIENTE, no afectó el saldo."

        # 3. LOG DE AUDITORÍA
        AuditoriaSaldo.objects.create(
            autor=request.user,
            empleado=empleado,
            accion=f"🗑️ ELIMINÓ VACACIONES ({solicitud.estado}): Borró periodo del {solicitud.fecha_inicio} al {solicitud.fecha_fin}. {mensaje_accion}"
        )

        # 4. ELIMINAR FÍSICAMENTE
        solicitud.delete()

        messages.success(request, f"🗑️ Solicitud eliminada correctamente. {mensaje_accion}")
        return redirect('detalle_empleado', empleado_id=empleado.id)

    return redirect('detalle_empleado', empleado_id=empleado.id)


# Se encarga de buscar los feriados y guardarlos si no existen ya
@login_required
def importar_feriados_nacionales(request):
    if not request.user.is_staff:
        return redirect('dashboard')

    anio_actual = timezone.now().year

    # 1. Obtenemos los feriados de la librería (Backend)
    # Importante: holidays devuelve un diccionario {fecha: "Nombre"}
    feriados_ar = holidays.AR(years=anio_actual)

    contador_nuevos = 0

    for fecha, nombre in feriados_ar.items():
        # 2. Verificamos si ya existe en TU base de datos para no duplicar
        if not Feriado.objects.filter(fecha=fecha).exists():
            Feriado.objects.create(
                fecha=fecha,
                descripcion=nombre.upper()  # Lo guardamos en mayúsculas para que se vea lindo
            )
            contador_nuevos += 1

    # 3. Avisamos el resultado
    if contador_nuevos > 0:
        messages.success(request, f"✅ Se importaron {contador_nuevos} feriados nacionales al calendario {anio_actual}.")
    else:
        messages.info(request, f"ℹ️ El calendario {anio_actual} ya estaba actualizado. No hubo cambios.")

    return redirect('crear_feriado')

# --- ELIMINAR LICENCIA ---
@login_required
def eliminar_licencia(request, licencia_id):
    licencia = get_object_or_404(Licencia, pk=licencia_id)
    empleado = licencia.empleado

    # 1. Seguridad: Solo RRHH
    if not request.user.is_staff:
        messages.error(request, "⛔ Solo RRHH puede eliminar registros.")
        return redirect('detalle_empleado', empleado_id=empleado.id)

    if request.method == 'POST':
        # 2. Auditoría: Registramos quién lo borró y qué borró
        AuditoriaSaldo.objects.create(
            autor=request.user,
            empleado=empleado,
            accion=f"🗑️ ELIMINÓ LICENCIA: {licencia.get_tipo_display()} del {licencia.fecha_inicio} al {licencia.fecha_fin}."
        )
        # 3. Borrado físico
        licencia.delete()
        messages.success(request, "🗑️ Licencia eliminada correctamente.")

    return redirect('detalle_empleado', empleado_id=empleado.id)


# --- ELIMINAR PERMISO ---
@login_required
def eliminar_permiso(request, permiso_id):
    permiso = get_object_or_404(Permiso, pk=permiso_id)
    empleado = permiso.empleado

    # 1. Seguridad: Solo RRHH
    if not request.user.is_staff:
        messages.error(request, "⛔ Solo RRHH puede eliminar registros.")
        return redirect('detalle_empleado', empleado_id=empleado.id)

    if request.method == 'POST':
        # 2. Auditoría: Registramos quién lo borró y qué borró
        AuditoriaSaldo.objects.create(
            autor=request.user,
            empleado=empleado,
            accion=f"🗑️ ELIMINÓ PERMISO: {permiso.get_tipo_display()} del {permiso.fecha_inicio} al {permiso.fecha_fin}."
        )
        # 3. Borrado físico
        permiso.delete()
        messages.success(request, "🗑️ Permiso eliminado correctamente.")

    return redirect('detalle_empleado', empleado_id=empleado.id)

@login_required
def historial_general(request):
    if not request.user.is_staff:
        return redirect('home_redirect')
    
    # Traemos todos los logs, optimizando con select_related 
    logs = AuditoriaSaldo.objects.select_related('autor', 'empleado').all().order_by('-fecha')
    
    # Filtro de búsqueda opcional
    busqueda = request.GET.get('q')
    if busqueda:
        logs = logs.filter(
            Q(autor__username__icontains=busqueda) |
            Q(empleado__apellido__icontains=busqueda) |
            Q(accion__icontains=busqueda)
        )

    return render(request, 'core/historial_general.html', {
        'logs': logs,
        'busqueda_actual': busqueda or ''
    })