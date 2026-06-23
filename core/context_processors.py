from datetime import date, timedelta
from typing import Any, Dict, List

from django.db.models import QuerySet
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone

from core.models import SolicitudVacaciones, Licencia, Permiso


def _formatear_tiempo(dias: int) -> str:
    """Helper para formatear la cantidad de días en un texto legible."""
    if dias == 0:
        return "Hoy"
    if dias == 1:
        return "Mañana"
    return f"En {dias} días"


def _generar_items_pendientes(
    queryset: QuerySet, prefijo: str, icono: str, color: str, accion: str
) -> List[Dict[str, Any]]:
    """Procesa y estandariza la lista de solicitudes pendientes de acción."""
    items = []
    for obj in queryset:
        items.append({
            'id': f'{prefijo}_{obj.id}',
            'icono': icono,
            'color': color,
            'texto': f"<b>{obj.empleado.nombre} {obj.empleado.apellido}</b> {accion}.",
            'tiempo': "Requiere Acción",
            'url': reverse('detalle_empleado', args=[obj.empleado.id])
        })
    return items


def _generar_items_movimientos(
    queryset: QuerySet, prefijo: str, icono: str, color: str, 
    motivo: str, hoy: date, es_regreso: bool = False
) -> List[Dict[str, Any]]:
    """Procesa y estandariza la lista de salidas o regresos próximos."""
    items = []
    for obj in queryset:
        if es_regreso:
            # Vuelven a trabajar el día DESPUÉS de la fecha_fin
            dias_restantes = (obj.fecha_fin - hoy).days + 1
            texto = f"<b>{obj.empleado.nombre} {obj.empleado.apellido}</b> vuelve de {motivo}."
        else:
            dias_restantes = (obj.fecha_inicio - hoy).days
            texto = f"<b>{obj.empleado.nombre} {obj.empleado.apellido}</b> inicia {motivo}."

        items.append({
            'id': f'{prefijo}_{obj.id}',
            'icono': icono,
            'color': color,
            'texto': texto,
            'tiempo': _formatear_tiempo(dias_restantes),
            'url': reverse('detalle_empleado', args=[obj.empleado.id])
        })
    return items


def notificaciones_rrhh(request: HttpRequest) -> Dict[str, Any]:
    """
    Procesador de contexto que genera las notificaciones globales para el panel de RRHH.
    
    Retorna:
        Dict con la lista truncada de notificaciones y el conteo total real.
    """
    # Validación de seguridad y permisos temprana (Guard Clause)
    if not request.user.is_authenticated or not request.user.is_staff:
        return {'notificaciones': [], 'notificaciones_count': 0}

    hoy = timezone.now().date()
    limite_salida = hoy + timedelta(days=7)
    limite_regreso = hoy + timedelta(days=4)

    notificaciones: List[Dict[str, Any]] = []

    # --- 1. PENDIENTES DE APROBACIÓN ---
    notificaciones.extend(_generar_items_pendientes(
        queryset=SolicitudVacaciones.objects.filter(estado='PENDIENTE').select_related('empleado'),
        prefijo='vp', icono='⏳', color='warning', accion='solicitó Vacaciones'
    ))
    notificaciones.extend(_generar_items_pendientes(
        queryset=Licencia.objects.filter(estado='PENDIENTE').select_related('empleado'),
        prefijo='lp', icono='🚑', color='danger', accion='cargó una Licencia'
    ))
    notificaciones.extend(_generar_items_pendientes(
        queryset=Permiso.objects.filter(estado='PENDIENTE').select_related('empleado'),
        prefijo='pp', icono='🎫', color='info', accion='solicitó un Permiso'
    ))

    # --- 2. PRÓXIMAS SALIDAS ---
    notificaciones.extend(_generar_items_movimientos(
        queryset=SolicitudVacaciones.objects.filter(
            estado='APROBADO', fecha_inicio__gt=hoy, fecha_inicio__lte=limite_salida
        ).select_related('empleado'),
        prefijo='vs', icono='✈️', color='primary', motivo='vacaciones', hoy=hoy
    ))
    notificaciones.extend(_generar_items_movimientos(
        queryset=Licencia.objects.filter(
            estado='APROBADO', fecha_inicio__gt=hoy, fecha_inicio__lte=limite_salida
        ).select_related('empleado'),
        prefijo='ls', icono='🏥', color='danger', motivo='licencia médica', hoy=hoy
    ))
    notificaciones.extend(_generar_items_movimientos(
        queryset=Permiso.objects.filter(
            estado='APROBADO', fecha_inicio__gt=hoy, fecha_inicio__lte=limite_salida
        ).select_related('empleado'),
        prefijo='ps', icono='🚪', color='info', motivo='un permiso', hoy=hoy
    ))

    # --- 3. PRÓXIMOS REGRESOS ---
    notificaciones.extend(_generar_items_movimientos(
        queryset=SolicitudVacaciones.objects.filter(
            estado='APROBADO', fecha_fin__gte=hoy, fecha_fin__lte=limite_regreso
        ).select_related('empleado'),
        prefijo='vr', icono='🔄', color='success', motivo='vacaciones', hoy=hoy, es_regreso=True
    ))
    notificaciones.extend(_generar_items_movimientos(
        queryset=Licencia.objects.filter(
            estado='APROBADO', fecha_fin__gte=hoy, fecha_fin__lte=limite_regreso
        ).select_related('empleado'),
        prefijo='lr', icono='🩺', color='success', motivo='licencia', hoy=hoy, es_regreso=True
    ))

    return {
        'notificaciones': notificaciones[:8],
        'notificaciones_count': len(notificaciones)
    }