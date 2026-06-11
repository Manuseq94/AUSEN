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
        return HttpResponse("<h1>Error</h1><p>Usuario sin empleado asignado.</p>")


# --- 2. DASHBOARD (SOLO RRHH) ---
@login_required
def dashboard(request):
    """
    Vista principal para usuarios con rol de Recursos Humanos (Staff).
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
    procesados_ausentes = set()

    # 1. Primero buscamos LICENCIAS
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
            procesados_ausentes.add(l.empleado.id)

    # 2. Luego buscamos VACACIONES
    vacs_ausentes = SolicitudVacaciones.objects.filter(
        fecha_inicio__lte=hoy, fecha_fin__gte=hoy, estado='APROBADO', empleado__activo=True
    ).select_related('empleado')

    for v in vacs_ausentes:
        if v.empleado.id not in procesados_ausentes:
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
    procesados_regresos = set()

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

    # --- E. CONTADORES ---
    total_empleados = Empleado.objects.filter(activo=True).count()
    total_ausentes_real = len(procesados_ausentes)

    presentes = total_empleados - total_ausentes_real
    if presentes < 0: presentes = 0

    solo_vacaciones = len([x for x in lista_ausentes if not x['es_licencia']])

    # --- F. DATOS PARA GRÁFICOS ---
    grafico_torta_data = [presentes, total_ausentes_real]
    anio_actual = timezone.now().year
    datos_mensuales = [0] * 12

    vacaciones_mes = SolicitudVacaciones.objects.filter(
        fecha_inicio__year=anio_actual, estado='APROBADO'
    ).annotate(mes=ExtractMonth('fecha_inicio')).values('mes').annotate(total=Count('id'))

    for v in vacaciones_mes:
        if v['mes']:
            datos_mensuales[v['mes'] - 1] += v['total']

    licencias_mes = Licencia.objects.filter(
        fecha_inicio__year=anio_actual, estado='APROBADO'
    ).annotate(mes=ExtractMonth('fecha_inicio')).values('mes').annotate(total=Count('id'))

    for l in licencias_mes:
        if l['mes']:
            datos_mensuales[l['mes'] - 1] += l['total']

    context = {
        'pendientes': pendientes,
        'licencias_pendientes': licencias_pendientes,
        'permisos_pendientes': permisos_pendientes,
        'ausentes': lista_ausentes,
        'proximos_regresos': lista_regresos,
        'proximas_salidas': lista_salidas,
        'total_empleados': total_empleados,
        'total_ausentes': solo_vacaciones,
        'total_ausentes_real': total_ausentes_real,
        'presentes': presentes,
        'hoy': hoy,
        'grafico_torta_data': grafico_torta_data,
        'grafico_barras_data': datos_mensuales,
    }
    return render(request, 'core/dashboard.html', context)


# --- GESTIÓN DE FERIADOS ---
@login_required
def crear_feriado(request):
    if not request.user.is_staff:
        return redirect('dashboard')

    if request.method == 'POST':
        form = FeriadoForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('crear_feriado')
    else:
        form = FeriadoForm()

    feriados = Feriado.objects.all().order_by('fecha')
    return render(request, 'core/form_feriado.html', {'form': form, 'feriados': feriados})


@login_required
def eliminar_feriado(request, feriado_id):
    if not request.user.is_staff:
        return redirect('dashboard')
    feriado = get_object_or_404(Feriado, pk=feriado_id)
    feriado.delete()
    return redirect('crear_feriado')


# --- PANEL DE CONFIGURACIÓN ---
@login_required
def configuracion(request):
    if not request.user.is_staff:
        return redirect('home_redirect')
    return render(request, 'core/configuracion.html')


@login_required
def calendario_api(request):
    eventos = []
    solicitudes = SolicitudVacaciones.objects.filter(estado='APROBADO')
    for sol in solicitudes:
        eventos.append({
            "title": f"🏖️ {sol.empleado.apellido}",
            "start": sol.fecha_inicio.isoformat(),
            "end": (sol.fecha_fin + timedelta(days=1)).isoformat(),
            "color": sol.empleado.color_calendario,
            "url": reverse('detalle_empleado', args=[sol.empleado.id]),
            "extendedProps": {"observaciones": "Vacaciones: " + (sol.observaciones or "")}
        })

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

    feriados = Feriado.objects.all()
    for fer in feriados:
        eventos.append({
            "start": fer.fecha.isoformat(),
            "end": (fer.fecha + timedelta(days=1)).isoformat(),
            "color": '#ffc107',
            "display": 'background',
            "allDay": True
        })
        eventos.append({
            "title": f"📅 {fer.descripcion}",
            "start": fer.fecha.isoformat(),
            "allDay": True,
            "color": 'transparent',
            "textColor": '#000000',
            "classNames": ['fw-bold', 'text-center']
        })
    return JsonResponse(eventos, safe=False)


# --- VISTA DE PERFIL INDIVIDUAL ---
@login_required
def detalle_empleado(request, empleado_id):
    empleado = get_object_or_404(Empleado, pk=empleado_id)

    if not _tiene_permiso_sobre_empleado(request.user, empleado):
        return HttpResponseForbidden("⛔ Acción no autorizada.")

    bolsas_activas = empleado.bolsas.all().order_by('anio')
    saldo_total = bolsas_activas.aggregate(total=Sum('dias_restantes'))['total'] or 0
    historial = empleado.solicitudes.all().order_by('-fecha_inicio')
    licencias = empleado.licencias.all().order_by('-fecha_inicio')
    permisos = empleado.permisos.all().order_by('-fecha_inicio')
    documentos = empleado.documentos.all().order_by('-fecha_subida')
    auditorias = AuditoriaSaldo.objects.filter(empleado=empleado).select_related('autor').order_by('-fecha')

    hoy = timezone.now().date()
    proximos_feriados = Feriado.objects.filter(fecha__gte=hoy).order_by('fecha')[:3]

    context = {
        'empleado': empleado,
        'bolsas': bolsas_activas,
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

    if not _tiene_permiso_sobre_empleado(request.user, empleado):
        return HttpResponseForbidden("⛔ Acción no autorizada.")
    
    es_admin = request.user.is_staff

    if request.method == 'POST':
        form = SolicitudForm(request.POST, empleado=empleado)
        form.instance.empleado = empleado

        if form.is_valid():
            solicitud = form.save(commit=False)
            solicitud.empleado = empleado
            solicitud.estado = 'APROBADO' if request.user.is_staff else 'PENDIENTE'
            solicitud.save()
            
            # --- 🕵️‍♂️ AUDITORÍA: CARGA DIRECTA POR ADMIN ---
            if es_admin:
                AuditoriaSaldo.objects.create(
                    autor=request.user,
                    empleado=empleado,
                    accion=f"✅ Carga Directa: @{request.user.username} registró y aprobó vacaciones para {empleado.nombre} {empleado.apellido} por {solicitud.dias_totales} días (del {solicitud.fecha_inicio} al {solicitud.fecha_fin})."
                )

            # --- 📧 NOTIFICACIÓN A RRHH ---
            if not request.user.is_staff:
                correos_rrhh = _obtener_correos_rrhh()
                if correos_rrhh:
                    asunto = f'✈️ Nueva Solicitud: {empleado.apellido}'
                    mensaje = f'El empleado {empleado.nombre} {empleado.apellido} ha solicitado vacaciones.\n\nDesde: {solicitud.fecha_inicio}\nHasta: {solicitud.fecha_fin}\nDías: {solicitud.dias_totales}\n\nIngresa al sistema para aprobar o rechazar.'
                    enviar_notificacion_email(asunto, mensaje, correos_rrhh)

            messages.success(request, "✅ Solicitud de vacaciones enviada correctamente.")
            return redirect('detalle_empleado', empleado_id=empleado.id)
    else:
        form = SolicitudForm(empleado=empleado)

    return render(request, 'core/form_vacaciones.html', {'form': form, 'empleado': empleado})


# --- GESTIÓN DE LICENCIAS ---
@login_required
def registrar_licencia(request, empleado_id):
    empleado = get_object_or_404(Empleado, pk=empleado_id)

    if not _tiene_permiso_sobre_empleado(request.user, empleado):
        return HttpResponseForbidden("⛔ Acción no autorizada.")

    if request.method == 'POST':
        form = LicenciaForm(request.POST, empleado=empleado, usuario=request.user)

        if form.is_valid():
            licencia = form.save(commit=False)
            licencia.empleado = empleado
            licencia.estado = 'APROBADO' if request.user.is_staff else 'PENDIENTE'
            licencia.save()

            # --- 🕵️‍♂️ AUDITORÍA: CARGA DIRECTA POR ADMIN ---
            if request.user.is_staff:
                AuditoriaSaldo.objects.create(
                    autor=request.user,
                    empleado=empleado,
                    accion=f"✅ Carga Directa: @{request.user.username} registró y aprobó licencia de '{licencia.get_tipo_display()}' para {empleado.nombre} {empleado.apellido} (del {licencia.fecha_inicio} al {licencia.fecha_fin})."
                )

            # --- NOTIFICACIÓN MAIL ---
            if not request.user.is_staff:
                correos_rrhh = _obtener_correos_rrhh()
                if correos_rrhh:
                    tipo_licencia = licencia.get_tipo_display()
                    asunto = f'      Nueva Licencia: {empleado.apellido}'
                    mensaje = f'El empleado {empleado.nombre} {empleado.apellido} ha informado una ausencia.\n\nMotivo: {tipo_licencia}\nDesde: {licencia.fecha_inicio}\nHasta: {licencia.fecha_fin}\n\nIngresa al sistema para ver el certificado o aprobar.'
                    enviar_notificacion_email(asunto, mensaje, correos_rrhh)

            messages.success(request, "✅ Licencia registrada correctamente.")
            return redirect('detalle_empleado', empleado_id=empleado.id)
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, error)
    else:
        form = LicenciaForm(empleado=empleado, usuario=request.user)

    return render(request, 'core/form_licencia.html', {'form': form, 'empleado': empleado})


@login_required
def procesar_licencia(request, licencia_id, accion):
    if not request.user.is_staff: return redirect('dashboard')
    licencia = get_object_or_404(Licencia, pk=licencia_id)
    empleado = licencia.empleado

    if accion == 'aprobar':
        # 🔥 ACÁ ACTÚA EL FAT MODEL: Una sola línea resuelve toda la lógica compleja
        if empleado.tiene_ausencia_aprobada(licencia.fecha_inicio, licencia.fecha_fin, excluir_licencia_id=licencia.id):
            messages.error(request, f"⛔ ERROR: {empleado.apellido} ya tiene una ausencia aprobada en esas fechas.")
            return redirect('dashboard')

        licencia.estado = 'APROBADO'
        licencia.save()

        # --- 🕵️‍♂️ AUDITORÍA ---
        AuditoriaSaldo.objects.create(
            autor=request.user,
            empleado=empleado,
            accion=f"✅ @{request.user.username} aprobó la licencia de '{licencia.get_tipo_display()}' para {empleado.nombre} {empleado.apellido} (del {licencia.fecha_inicio} al {licencia.fecha_fin})."
        )

        if empleado.usuario and empleado.usuario.email:
            asunto = '✅ Licencia Registrada'
            mensaje = f'Hola {empleado.nombre},\n\nTu licencia por {licencia.get_tipo_display()} ha sido registrada y aprobada correctamente.\n\nFechas: {licencia.fecha_inicio} al {licencia.fecha_fin}.'
            enviar_notificacion_email(asunto, mensaje, [empleado.usuario.email])

    elif accion == 'rechazar':
        licencia.estado = 'RECHAZADO'
        licencia.save()
        
        # --- 🕵️‍♂️ AUDITORÍA ---
        AuditoriaSaldo.objects.create(
            autor=request.user, empleado=empleado,
            accion=f"❌ @{request.user.username} rechazó la licencia de '{licencia.get_tipo_display()}' para {empleado.nombre} {empleado.apellido}."
        )

        if empleado.usuario and empleado.usuario.email:
            asunto = '❌ Licencia Observada'
            mensaje = f'Hola {empleado.nombre},\n\nTu carga de licencia ha sido rechazada o requiere correcciones.\nPor favor contacta a RRHH.'
            enviar_notificacion_email(asunto, mensaje, [empleado.usuario.email])

    return redirect('dashboard')


# --- PROCESAR SOLICITUD (VACACIONES) ---
@login_required
def procesar_solicitud(request, solicitud_id, accion):
    if not request.user.is_staff: return redirect('dashboard')
    solicitud = get_object_or_404(SolicitudVacaciones, pk=solicitud_id)
    empleado = solicitud.empleado

    if accion == 'rechazar':
        solicitud.estado = 'RECHAZADO'
        solicitud.save()

        AuditoriaSaldo.objects.create(
            autor=request.user, empleado=empleado,
            accion=f"❌ @{request.user.username} rechazó la solicitud de vacaciones de {empleado.nombre} {empleado.apellido} (del {solicitud.fecha_inicio} al {solicitud.fecha_fin})."
        )

        if empleado.usuario and empleado.usuario.email:
            asunto = '❌ Solicitud de Vacaciones Rechazada'
            mensaje = f'Hola {empleado.nombre},\n\nTu solicitud de vacaciones para las fechas {solicitud.fecha_inicio} al {solicitud.fecha_fin} ha sido rechazada.\n\nPor favor, comunícate con RRHH para más detalles.'
            enviar_notificacion_email(asunto, mensaje, [empleado.usuario.email])

    elif accion == 'aprobar':
        # 🔥 VALIDACIÓN CON FAT MODEL
        if empleado.tiene_ausencia_aprobada(solicitud.fecha_inicio, solicitud.fecha_fin, excluir_vacacion_id=solicitud.id):
            messages.error(request, f"⛔ ERROR: {empleado.apellido} ya tiene una ausencia aprobada en esas fechas.")
            return redirect('dashboard')

        # 🔥 EJECUCIÓN DEL DESCUENTO CON FAT MODEL
        exito, mensaje_error = solicitud.ejecutar_aprobacion_y_descuento()
        
        if not exito:
            messages.error(request, f"⛔ ERROR: {mensaje_error}")
            return redirect('dashboard')

        # Si llegó acá, todo salió perfecto
        AuditoriaSaldo.objects.create(
            autor=request.user, empleado=empleado,
            accion=f"✅ @{request.user.username} aprobó las vacaciones de {empleado.nombre} {empleado.apellido} por {solicitud.dias_totales} días (del {solicitud.fecha_inicio} al {solicitud.fecha_fin})."
        )

        if empleado.usuario and empleado.usuario.email:
            asunto = '✅ Vacaciones Aprobadas'
            mensaje = f'¡Buenas noticias {empleado.nombre}!\n\nTus vacaciones han sido aprobadas.\nFechas: {solicitud.fecha_inicio} al {solicitud.fecha_fin}\n\n¡Que descanses!'
            enviar_notificacion_email(asunto, mensaje, [empleado.usuario.email])

    return redirect('dashboard')

@login_required
def editar_empleado(request, empleado_id):
    if not request.user.is_staff:
        return HttpResponseForbidden("No autorizado")

    empleado = get_object_or_404(Empleado, pk=empleado_id)
    initial_data = {}
    if empleado.usuario:
        initial_data['email'] = empleado.usuario.email

    if request.method == 'POST':
        form = EmpleadoEditarForm(request.POST, instance=empleado)

        if form.is_valid():
            empleado = form.save()
            nuevo_email = form.cleaned_data.get('email')
            if empleado.usuario:
                empleado.usuario.email = nuevo_email
                empleado.usuario.save()

            return redirect('lista_empleados')
    else:
        form = EmpleadoEditarForm(instance=empleado, initial=initial_data)

    return render(request, 'core/editar_empleado.html', {'form': form, 'empleado': empleado})


# --- REPORTES Y OTROS ---
@login_required
def exportar_saldos_csv(request):
    if not request.user.is_staff: return HttpResponseForbidden("Acceso denegado")
    
    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = 'attachment; filename="reporte_saldos_detallado.csv"'
    writer = csv.writer(response, delimiter=';') 
    
    writer.writerow(['Legajo', 'Apellido', 'Nombre', 'DNI', 'Fecha Ingreso', 'Detalle por Periodo', 'Saldo Total'])
    empleados = Empleado.objects.filter(activo=True).prefetch_related('bolsas').order_by('apellido')
    
    for emp in empleados:
        bolsas = emp.bolsas.all()
        saldo_total = sum(b.dias_restantes for b in bolsas)
        bolsas_activas = [b for b in bolsas if b.dias_restantes > 0]
        detalle_periodos = " | ".join([f"Año {b.anio}: {b.dias_restantes} días" for b in bolsas_activas])
        
        if not detalle_periodos:
            detalle_periodos = "Sin saldo"
            
        fecha_ingreso_str = emp.fecha_ingreso.strftime("%d/%m/%Y") if emp.fecha_ingreso else ""
        
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
    for sol in empleado.solicitudes.all(): 
        writer.writerow([sol.fecha_inicio, sol.fecha_fin, sol.dias_totales, sol.estado])
    return response


@login_required
def generar_pdf_solicitud(request, solicitud_id):
    solicitud = get_object_or_404(SolicitudVacaciones.objects.select_related('empleado'), pk=solicitud_id)
    empleado = solicitud.empleado

    if not _tiene_permiso_sobre_empleado(request.user, empleado):
        return HttpResponseForbidden("⛔ Acción no autorizada.")

    if solicitud.estado == 'RECHAZADO':
        messages.error(request, "⛔ El documento carece de validez porque la solicitud fue rechazada.")
        return redirect('detalle_empleado', empleado_id=empleado.id)

    saldo_db = empleado.bolsas.aggregate(total=Sum('dias_restantes'))['total'] or 0
    bolsas_activas = empleado.bolsas.filter(dias_restantes__gt=0).order_by('anio')
    desglose_lista = []

    if solicitud.estado == 'PENDIENTE':
        saldo_para_mostrar = saldo_db - solicitud.dias_totales
        dias_a_descontar = solicitud.dias_totales

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
        saldo_para_mostrar = saldo_db
        for bolsa in bolsas_activas:
            desglose_lista.append(f"{bolsa.dias_restantes} días (del periodo {bolsa.anio})")

    if not desglose_lista:
        texto_desglose = "0 días"
    elif len(desglose_lista) == 1:
        texto_desglose = desglose_lista[0]
    else:
        texto_desglose = ", ".join(desglose_lista[:-1]) + " y " + desglose_lista[-1]

    context = {
        'solicitud': solicitud,
        'empleado': empleado,
        'detalles': solicitud.detalles.all(),
        'saldo_actual': saldo_para_mostrar, 
        'texto_desglose': texto_desglose,
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

    empleados = Empleado.objects.filter(activo=True).order_by('apellido')
    filtro_rapido = request.GET.get('filtro')
    hoy = timezone.now().date()

    if filtro_rapido == 'vacaciones':
        ids_vacaciones = SolicitudVacaciones.objects.filter(
            estado='APROBADO', fecha_inicio__lte=hoy, fecha_fin__gte=hoy
        ).values_list('empleado_id', flat=True)
        empleados = empleados.filter(id__in=ids_vacaciones)

    elif filtro_rapido == 'presentes':
        ids_vacaciones = SolicitudVacaciones.objects.filter(
            estado='APROBADO', fecha_inicio__lte=hoy, fecha_fin__gte=hoy
        ).values_list('empleado_id', flat=True)

        ids_licencias = Licencia.objects.filter(
            estado='APROBADO', fecha_inicio__lte=hoy, fecha_fin__gte=hoy
        ).values_list('empleado_id', flat=True)

        ids_ausentes = list(ids_vacaciones) + list(ids_licencias)
        empleados = empleados.exclude(id__in=ids_ausentes)

    busqueda = request.GET.get('q')
    if busqueda:
        empleados = empleados.filter(
            Q(nombre__icontains=busqueda) | Q(apellido__icontains=busqueda) | Q(legajo__icontains=busqueda)
        )

    sector_filtro = request.GET.get('sector')
    if sector_filtro:
        empleados = empleados.filter(sector=sector_filtro)

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
            'tipo': filtro_rapido or ''
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

            messages.success(request, f"✅ Se cargó la bolsa del {b.anio} para {empleado.nombre}.")

            # --- 🕵️‍♂️ AUDITORÍA ---
            AuditoriaSaldo.objects.create(
                autor=request.user,
                empleado=empleado,
                accion=f"⚖️ CARGA MANUAL: @{request.user.username} creó la bolsa del año {b.anio} con {b.dias_restantes} días para {empleado.nombre} {empleado.apellido}."
            )

            return redirect('detalle_empleado', empleado_id=empleado.id)
        else:
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
    consumo_real = bolsa.detalles.aggregate(total=Sum('dias_descontados'))['total'] or 0
    saldo_anterior_db = bolsa.dias_restantes

    if request.method == 'POST':
        form = BolsaManualForm(request.POST, instance=bolsa, empleado=empleado)

        if form.is_valid():
            total_ingresado = form.cleaned_data['dias_restantes']

            if total_ingresado < consumo_real:
                messages.error(request,
                    f"⛔ ERROR: No puedes definir un total de {total_ingresado} días porque el empleado ya consumió {consumo_real}. "
                    f"El total debe ser igual o mayor al consumo.")
                return redirect('editar_bolsa', bolsa_id=bolsa.id)

            nuevo_saldo_real = total_ingresado - consumo_real
            bolsa.dias_restantes = nuevo_saldo_real
            bolsa.save()

            # --- 🕵️‍♂️ AUDITORÍA ---
            if saldo_anterior_db != bolsa.dias_restantes:
                AuditoriaSaldo.objects.create(
                    autor=request.user,
                    empleado=empleado,
                    accion=f"✏️ @{request.user.username} editó la bolsa {bolsa.anio} de {empleado.nombre} {empleado.apellido}: Ingresó total {total_ingresado}. Se descontó consumo ({consumo_real}). Saldo ajustado de {saldo_anterior_db} a {bolsa.dias_restantes}."
                )

            messages.success(request, f"✅ Saldo actualizado. Total {total_ingresado} - Consumidos {consumo_real} = {nuevo_saldo_real} disponibles.")
            return redirect('detalle_empleado', empleado_id=empleado.id)
    else:
        bolsa_visual = bolsa
        bolsa_visual.dias_restantes = bolsa.dias_restantes + consumo_real
        form = BolsaManualForm(instance=bolsa_visual, empleado=empleado)

    return render(request, 'core/form_bolsa.html', {
        'form': form, 'empleado': empleado, 'es_edicion': True, 'consumo': consumo_real
    })


@login_required
def eliminar_bolsa(request, bolsa_id):
    if not request.user.is_staff:
        return HttpResponseForbidden("No autorizado")

    bolsa = get_object_or_404(BolsaVacaciones, pk=bolsa_id)
    empleado = bolsa.empleado
    anio_borrado = bolsa.anio

    # --- 🕵️‍♂️ AUDITORÍA ---
    AuditoriaSaldo.objects.create(
        autor=request.user,
        empleado=empleado,
        accion=f"🗑️ @{request.user.username} eliminó manualmente la bolsa del año {anio_borrado} ({bolsa.dias_restantes} días) de {empleado.nombre} {empleado.apellido}."
    )

    bolsa.delete()
    messages.success(request, f"🗑️ La bolsa del año {anio_borrado} fue eliminada correctamente.")
    return redirect('detalle_empleado', empleado_id=empleado.id)


@login_required
def gestion_usuarios(request):
    if not request.user.is_staff: return redirect('home_redirect')

    if request.method == 'POST':
        form = CrearUsuarioForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                u = User.objects.create_user(
                    username=data['username'], password=data['password'], email=data['email']
                )

                if data['empleado']:
                    emp = data['empleado']
                    emp.usuario = u
                    emp.save()
                    if data.get('es_admin'):
                        u.is_staff = True
                        u.save()
                else:
                    u.is_staff = True
                    u.save()

                return redirect('gestion_usuarios')
            except Exception as e:
                print(f"Error creando usuario: {e}")
    else:
        form = CrearUsuarioForm()

    usuarios = User.objects.select_related('empleado').all().order_by('-is_staff', 'username')
    busqueda = request.GET.get('q')
    if busqueda:
        usuarios = usuarios.filter(
            Q(username__icontains=busqueda) | Q(first_name__icontains=busqueda) | Q(last_name__icontains=busqueda) |
            Q(empleado__nombre__icontains=busqueda) | Q(empleado__apellido__icontains=busqueda)
        )

    return render(request, 'core/gestion_usuarios.html', {
        'form': form, 'usuarios': usuarios, 'busqueda_actual': busqueda or ''
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


@login_required
def generar_pdf_licencia(request, licencia_id):
    licencia = get_object_or_404(Licencia.objects.select_related('empleado'), pk=licencia_id)
    empleado = licencia.empleado

    if not _tiene_permiso_sobre_empleado(request.user, empleado):
        return HttpResponseForbidden("⛔ Acción no autorizada para ver este documento.")

    context = {'licencia': licencia, 'empleado': empleado, 'hoy': timezone.now()}
    html = get_template('core/pdf_licencia.html').render(context)
    response = HttpResponse(content_type='application/pdf')
    filename = f"Licencia_{licencia.get_tipo_display()}_{empleado.apellido}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'

    pisa_status = pisa.CreatePDF(html, dest=response)
    if pisa_status.err: return HttpResponse('Error al generar PDF')
    return response


# --- GESTIÓN DE PERMISOS (HOME OFFICE / OTROS) ---
@login_required
def registrar_permiso(request, empleado_id):
    empleado = get_object_or_404(Empleado, pk=empleado_id)

    if not _tiene_permiso_sobre_empleado(request.user, empleado):
        return HttpResponseForbidden("⛔ Acción no autorizada.")

    if request.method == 'POST':
        form = PermisoForm(request.POST, empleado=empleado)

        if form.is_valid():
            permiso = form.save(commit=False)
            permiso.empleado = empleado
            permiso.estado = 'APROBADO' if request.user.is_staff else 'PENDIENTE'
            permiso.save()

            # --- 🕵️‍♂️ AUDITORÍA: CARGA DIRECTA POR ADMIN ---
            if request.user.is_staff:
                AuditoriaSaldo.objects.create(
                    autor=request.user,
                    empleado=empleado,
                    accion=f"✅ Carga Directa: @{request.user.username} registró y aprobó permiso de '{permiso.get_tipo_display()}' para {empleado.nombre} {empleado.apellido} (del {permiso.fecha_inicio} al {permiso.fecha_fin})."
                )

            # --- 📧 NOTIFICACIÓN A RRHH ---
            if not request.user.is_staff:
                correos_rrhh = _obtener_correos_rrhh()
                if correos_rrhh:
                    asunto = f'🏠 Nuevo Permiso: {empleado.apellido}'
                    mensaje = f'{empleado.nombre} solicita: {permiso.get_tipo_display()}.\nFechas: {permiso.fecha_inicio} al {permiso.fecha_fin}.\nMotivo: {permiso.motivo}'
                    enviar_notificacion_email(asunto, mensaje, correos_rrhh)

            messages.success(request, "✅ Solicitud de permiso registrada correctamente.")
            return redirect('detalle_empleado', empleado_id=empleado.id)
    else:
        form = PermisoForm(empleado=empleado)

    return render(request, 'core/form_permiso.html', {'form': form, 'empleado': empleado})


@login_required
def procesar_permiso(request, permiso_id, accion):
    if not request.user.is_staff: return redirect('dashboard')
    permiso = get_object_or_404(Permiso, pk=permiso_id)
    empleado = permiso.empleado

    if accion == 'aprobar':
        permiso.estado = 'APROBADO'
        permiso.save()

        # --- 🕵️‍♂️ AUDITORÍA (CORREGIDO: Agregado el Log faltante) ---
        AuditoriaSaldo.objects.create(
            autor=request.user,
            empleado=empleado,
            accion=f"✅ @{request.user.username} aprobó el permiso de '{permiso.get_tipo_display()}' para {empleado.nombre} {empleado.apellido} (del {permiso.fecha_inicio} al {permiso.fecha_fin})."
        )

        if empleado.usuario and empleado.usuario.email:
            asunto = '✅ Permiso Aprobado'
            mensaje = f'Tu solicitud de {permiso.get_tipo_display()} ha sido aprobada.'
            enviar_notificacion_email(asunto, mensaje, [empleado.usuario.email])

    elif accion == 'rechazar':
        permiso.estado = 'RECHAZADO'
        permiso.save()

        # --- 🕵️‍♂️ AUDITORÍA (CORREGIDO: Agregado el Log faltante) ---
        AuditoriaSaldo.objects.create(
            autor=request.user,
            empleado=empleado,
            accion=f"❌ @{request.user.username} rechazó el permiso de '{permiso.get_tipo_display()}' para {empleado.nombre} {empleado.apellido}."
        )

        if empleado.usuario and empleado.usuario.email:
            asunto = '❌ Permiso Denegado'
            mensaje = f'Tu solicitud de {permiso.get_tipo_display()} no fue autorizada. Consulta con RRHH.'
            enviar_notificacion_email(asunto, mensaje, [empleado.usuario.email])

    return redirect('dashboard')


@login_required
def generar_pdf_permiso(request, permiso_id):
    permiso = get_object_or_404(Permiso.objects.select_related('empleado'), pk=permiso_id)
    empleado = permiso.empleado

    if not _tiene_permiso_sobre_empleado(request.user, empleado):
        return HttpResponseForbidden("⛔ Acción no autorizada para ver este documento.")

    context = {'permiso': permiso, 'empleado': empleado, 'hoy': timezone.now()}
    html = get_template('core/pdf_permiso.html').render(context)
    response = HttpResponse(content_type='application/pdf')
    filename = f"Permiso_{permiso.get_tipo_display()}_{empleado.apellido}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'

    pisa_status = pisa.CreatePDF(html, dest=response)
    if pisa_status.err: return HttpResponse('Error al generar PDF')
    return response


@login_required
def subir_documento(request, empleado_id):
    if not request.user.is_staff:
        messages.error(request, "⛔ No autorizado.")
        return redirect('dashboard')

    empleado = get_object_or_404(Empleado, pk=empleado_id)

    if request.method == 'POST':
        form = DocumentoForm(request.POST, request.FILES)

        if form.is_valid():
            doc = form.save(commit=False)
            doc.empleado = empleado
            doc.save()

            # --- 🕵️‍♂️ AUDITORÍA ---
            AuditoriaSaldo.objects.create(
                autor=request.user,
                empleado=empleado,
                accion=f"📂 @{request.user.username} subió documento: '{doc.titulo}' para {empleado.nombre} {empleado.apellido}."
            )

            if empleado.usuario and empleado.usuario.email:
                asunto = f'📄 Nuevo Documento: {doc.titulo}'
                mensaje = f'Hola {empleado.nombre},\n\nRRHH ha cargado un nuevo archivo: "{doc.titulo}".'
                enviar_notificacion_email(asunto, mensaje, [empleado.usuario.email])

            messages.success(request, "✅ Documento subido correctamente.")
            return redirect('detalle_empleado', empleado_id=empleado.id)
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"⛔ {error}")

            for error in form.non_field_errors():
                messages.error(request, f"⛔ {error}")

    return redirect('detalle_empleado', empleado_id=empleado.id)


@login_required
def eliminar_documento(request, documento_id):
    if not request.user.is_staff:
        messages.error(request, "⛔ No autorizado.")
        return redirect('dashboard')

    doc = get_object_or_404(Documento, pk=documento_id)
    empleado = doc.empleado
    titulo = doc.titulo

    # --- 🕵️‍♂️ AUDITORÍA ---
    AuditoriaSaldo.objects.create(
        autor=request.user,
        empleado=empleado,
        accion=f"🗑️ @{request.user.username} eliminó el documento '{titulo}' de {empleado.nombre} {empleado.apellido}."
    )

    doc.delete()
    messages.warning(request, f"🗑️ Se eliminó el documento: {titulo}")
    return redirect('detalle_empleado', empleado_id=empleado.id)


@login_required
def eliminar_usuario_sistema(request, user_id):
    if not request.user.is_staff: return redirect('home_redirect')
    user_a_borrar = get_object_or_404(User, pk=user_id)

    if user_a_borrar.id == request.user.id:
        return redirect('gestion_usuarios')

    user_a_borrar.delete()
    return redirect('gestion_usuarios')


@login_required
def editar_usuario(request, user_id):
    if not request.user.is_staff: return redirect('home_redirect')

    usuario_editar = get_object_or_404(User, pk=user_id)
    es_propio = (usuario_editar.id == request.user.id)

    if request.method == 'POST':
        usuario_editar.email = request.POST.get('email')

        if es_propio:
            nuevo_username = request.POST.get('username')
            if nuevo_username and nuevo_username != usuario_editar.username:
                if User.objects.filter(username=nuevo_username).exists():
                    messages.error(request, "❌ Ese nombre de usuario ya existe.")
                    return render(request, 'core/form_editar_usuario.html',
                                  {'usuario': usuario_editar, 'es_propio': es_propio})
                usuario_editar.username = nuevo_username

            pass_actual = request.POST.get('old_password')
            pass_nueva1 = request.POST.get('new_password_1')
            pass_nueva2 = request.POST.get('new_password_2')

            if pass_nueva1:
                if not pass_actual or not usuario_editar.check_password(pass_actual):
                    messages.error(request, "❌ La contraseña actual es incorrecta.")
                    return render(request, 'core/form_editar_usuario.html',
                                  {'usuario': usuario_editar, 'es_propio': es_propio})

                if pass_nueva1 != pass_nueva2:
                    messages.error(request, "❌ Las nuevas contraseñas no coinciden.")
                    return render(request, 'core/form_editar_usuario.html',
                                  {'usuario': usuario_editar, 'es_propio': es_propio})

                usuario_editar.set_password(pass_nueva1)
                usuario_editar.save()
                update_session_auth_hash(request, usuario_editar)
                messages.success(request, "✅ Contraseña actualizada correctamente.")
        else:
            usuario_editar.is_staff = (request.POST.get('es_admin') == 'on')

        usuario_editar.save()
        if not es_propio or not request.POST.get('new_password_1'):
            return redirect('gestion_usuarios')

    return render(request, 'core/form_editar_usuario.html', {'usuario': usuario_editar, 'es_propio': es_propio})


@login_required
def ejecutar_renovacion_anual(request):
    if not request.user.is_staff: return redirect('dashboard')

    out = StringIO()
    try:
        call_command('renovar_vacaciones', stdout=out)
        mensaje_salida = out.getvalue().strip()

        if "⛔" in mensaje_salida:
            messages.error(request, mensaje_salida)
        elif "✅" in mensaje_salida:
            messages.success(request, mensaje_salida)
        else:
            messages.info(request, mensaje_salida)

    except Exception as e:
        messages.error(request, f"❌ Error técnico: {e}")

    return redirect('dashboard')


@login_required
def editar_solicitud(request, solicitud_id):
    solicitud = get_object_or_404(SolicitudVacaciones, pk=solicitud_id)
    empleado = solicitud.empleado
    hoy = timezone.now().date()

    es_rrhh = request.user.is_staff
    es_dueno = (hasattr(request.user, 'empleado') and request.user.empleado == empleado)

    if not es_rrhh and not es_dueno:
        messages.error(request, "⛔ No tienes permiso.")
        return redirect('detalle_empleado', empleado_id=empleado.id)

    if es_dueno and not es_rrhh and solicitud.estado != 'PENDIENTE':
        messages.error(request, "⛔ No puedes editar vacaciones ya aprobadas. Contacta a RRHH.")
        return redirect('detalle_empleado', empleado_id=empleado.id)
        
    if solicitud.fecha_fin < hoy:
        messages.error(request, "⛔ PROHIBIDO: Este período de vacaciones ya finalizó y fue liquidado. No se puede modificar.")
        return redirect('detalle_empleado', empleado_id=empleado.id)

    fecha_inicio_vieja = solicitud.fecha_inicio
    fecha_fin_vieja = solicitud.fecha_fin
    dias_viejos = (fecha_fin_vieja - fecha_inicio_vieja).days + 1

    if request.method == 'POST':
        form = SolicitudForm(request.POST, instance=solicitud, empleado=empleado)
        form.instance.empleado = empleado

        if form.is_valid():
            nueva_solicitud = form.save(commit=False)

            if solicitud.estado == 'PENDIENTE':
                nueva_solicitud.save()
                messages.success(request, "📝 Solicitud pendiente actualizada.")

            elif solicitud.estado == 'APROBADO':
                dias_nuevos = (nueva_solicitud.fecha_fin - nueva_solicitud.fecha_inicio).days + 1
                diferencia_a_devolver = dias_viejos - dias_nuevos

                if diferencia_a_devolver < 0:
                    messages.error(request, "⛔ PROHIBIDO: No puedes extender una vacación ya aprobada. Solo puedes acortarla (Stop). Para agregar días, crea una NUEVA solicitud.")
                    return redirect('detalle_empleado', empleado_id=empleado.id)

                if diferencia_a_devolver == 0 and nueva_solicitud.fecha_inicio != fecha_inicio_vieja:
                    messages.error(request, "⛔ PROHIBIDO: No puedes mover las fechas de una vacación aprobada sin acortarla. Cancélala y crea una nueva.")
                    return redirect('detalle_empleado', empleado_id=empleado.id)

                nueva_solicitud.save()

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
                            
                            # --- 🕵️‍♂️ AUDITORÍA ---
                            AuditoriaSaldo.objects.create(
                                autor=request.user, empleado=empleado,
                                accion=f"♻️ @{request.user.username} aplicó Restitución Total ({bolsa.anio}) a {empleado.nombre} {empleado.apellido}: Se devolvieron {dias_en_este_detalle} días."
                            )
                            remanente -= dias_en_este_detalle
                            detalle.delete()
                        else:
                            bolsa.dias_restantes += remanente
                            bolsa.save()
                            detalle.dias_descontados -= remanente
                            detalle.save()
                            
                            # --- 🕵️‍♂️ AUDITORÍA ---
                            AuditoriaSaldo.objects.create(
                                autor=request.user, empleado=empleado,
                                accion=f"♻️ @{request.user.username} aplicó Restitución Parcial ({bolsa.anio}) a {empleado.nombre} {empleado.apellido}: Se devolvieron {remanente} días."
                            )
                            remanente = 0

                    messages.warning(request, f"✅ Vacaciones interrumpidas. Se restituyeron {diferencia_a_devolver} días.")

            return redirect('detalle_empleado', empleado_id=empleado.id)
    else:
        form = SolicitudForm(instance=solicitud, empleado=empleado)

    return render(request, 'core/form_vacaciones.html', {
        'form': form, 'empleado': empleado, 'es_edicion': True
    })


# --- ELIMINAR VACACIONES Y DEVOLVER SALDO ---
@login_required
def eliminar_solicitud(request, solicitud_id):
    solicitud = get_object_or_404(SolicitudVacaciones, pk=solicitud_id)
    empleado = solicitud.empleado
    hoy = timezone.now().date()  # 📅 Traemos el día de hoy para comparar

    # 1. CANDADO DE SEGURIDAD 🔒
    # Solo RRHH puede eliminar. El empleado debe pedirlo.
    if not request.user.is_staff:
        messages.error(request, "⛔ No puedes eliminar una solicitud. Contacta a RRHH para que la rechacen.")
        return redirect('detalle_empleado', empleado_id=empleado.id)

    # 👇 NUEVO CANDADO AUTOMÁTICO: PROHIBIDO BORRAR EL PASADO 👇
    if solicitud.fecha_fin < hoy:
        messages.error(request, "⛔ PROHIBIDO: No puedes eliminar vacaciones que ya finalizaron y fueron tomadas por el empleado.")
        return redirect('detalle_empleado', empleado_id=empleado.id)

    if request.method == 'POST':
        # 2. LÓGICA DE DEVOLUCIÓN INTELIGENTE 🧠
        detalles = solicitud.detalles.all()
        dias_devueltos = 0

        if detalles.exists():
            for detalle in detalles:
                bolsa = detalle.bolsa
                bolsa.dias_restantes += detalle.dias_descontados
                bolsa.save()
                dias_devueltos += detalle.dias_descontados

            mensaje_accion = f"Se restituyeron {dias_devueltos} días a la bolsa de {empleado.nombre} {empleado.apellido}."
        else:
            mensaje_accion = "La solicitud estaba PENDIENTE, no afectó el saldo."

        # 3. LOG DE AUDITORÍA
        AuditoriaSaldo.objects.create(
            autor=request.user,
            empleado=empleado,
            accion=f"🗑️ @{request.user.username} ELIMINÓ VACACIONES ({solicitud.estado}) de {empleado.nombre} {empleado.apellido}: Borró periodo del {solicitud.fecha_inicio} al {solicitud.fecha_fin}. {mensaje_accion}"
        )

        solicitud.delete()
        messages.success(request, f"🗑️ Solicitud eliminada correctamente. {mensaje_accion}")
        return redirect('detalle_empleado', empleado_id=empleado.id)

    return redirect('detalle_empleado', empleado_id=empleado.id)


@login_required
def importar_feriados_nacionales(request):
    if not request.user.is_staff:
        return redirect('dashboard')

    anio_actual = timezone.now().year
    feriados_ar = holidays.AR(years=anio_actual)
    contador_nuevos = 0

    for fecha, nombre in feriados_ar.items():
        if not Feriado.objects.filter(fecha=fecha).exists():
            Feriado.objects.create(fecha=fecha, descripcion=nombre.upper())
            contador_nuevos += 1

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
    hoy = timezone.now().date()  # 📅 Traemos el día de hoy para comparar

    # 1. Seguridad: Solo RRHH
    if not request.user.is_staff:
        messages.error(request, "⛔ Solo RRHH puede eliminar registros.")
        return redirect('detalle_empleado', empleado_id=empleado.id)

    # 👇 NUEVO CANDADO AUTOMÁTICO: PROHIBIDO BORRAR EL PASADO 👇
    if licencia.fecha_fin < hoy:
        messages.error(request, "⛔ PROHIBIDO: No puedes eliminar licencias del pasado que ya finalizaron.")
        return redirect('detalle_empleado', empleado_id=empleado.id)

    if request.method == 'POST':
        # 2. Auditoría: Registramos quién lo borró y qué borró
        AuditoriaSaldo.objects.create(
            autor=request.user,
            empleado=empleado,
            accion=f"🗑️ @{request.user.username} eliminó la licencia de '{licencia.get_tipo_display()}' de {empleado.nombre} {empleado.apellido} (del {licencia.fecha_inicio} al {licencia.fecha_fin})."
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
    hoy = timezone.now().date()  # 📅 Traemos el día de hoy para comparar

    # 1. Seguridad: Solo RRHH
    if not request.user.is_staff:
        messages.error(request, "⛔ Solo RRHH puede eliminar registros.")
        return redirect('detalle_empleado', empleado_id=empleado.id)

    # 👇 NUEVO CANDADO AUTOMÁTICO: PROHIBIDO BORRAR EL PASADO 👇
    if permiso.fecha_fin < hoy:
        messages.error(request, "⛔ PROHIBIDO: No puedes eliminar permisos del pasado que ya finalizaron.")
        return redirect('detalle_empleado', empleado_id=empleado.id)

    if request.method == 'POST':
        # 2. Auditoría: Registramos quién lo borró y qué borró
        AuditoriaSaldo.objects.create(
            autor=request.user,
            empleado=empleado,
            accion=f"🗑️ @{request.user.username} eliminó el permiso de '{permiso.get_tipo_display()}' de {empleado.nombre} {empleado.apellido} (del {permiso.fecha_inicio} al {permiso.fecha_fin})."
        )
        # 3. Borrado físico
        permiso.delete()
        messages.success(request, "🗑️ Permiso eliminado correctamente.")

    return redirect('detalle_empleado', empleado_id=empleado.id)


@login_required
def historial_general(request):
    if not request.user.is_staff:
        return redirect('home_redirect')
    
    logs = AuditoriaSaldo.objects.select_related('autor', 'empleado').all().order_by('-fecha')
    busqueda = request.GET.get('q')
    if busqueda:
        logs = logs.filter(
            Q(autor__username__icontains=busqueda) | Q(empleado__apellido__icontains=busqueda) | Q(accion__icontains=busqueda)
        )

    return render(request, 'core/historial_general.html', {'logs': logs, 'busqueda_actual': busqueda or ''})