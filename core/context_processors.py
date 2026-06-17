from datetime import timedelta
from django.utils import timezone
from django.urls import reverse
from core.models import SolicitudVacaciones, Licencia, Permiso

def notificaciones_rrhh(request):
    if not request.user.is_authenticated or not request.user.is_staff:
        return {'notificaciones': [], 'notificaciones_count': 0}

    hoy = timezone.now().date()
    limite_salida = hoy + timedelta(days=7)
    limite_regreso = hoy + timedelta(days=4)

    notificaciones = []

    # --- 1. PENDIENTES DE APROBACIÓN ---
    pendientes_vac = SolicitudVacaciones.objects.filter(estado='PENDIENTE').select_related('empleado')
    for v in pendientes_vac:
        notificaciones.append({'id': f'vp_{v.id}', 'icono': '⏳', 'color': 'warning', 'texto': f"<b>{v.empleado.nombre} {v.empleado.apellido}</b> solicitó Vacaciones.", 'tiempo': "Requiere Acción", 'url': reverse('detalle_empleado', args=[v.empleado.id])})
        
    pendientes_lic = Licencia.objects.filter(estado='PENDIENTE').select_related('empleado')
    for l in pendientes_lic:
        notificaciones.append({'id': f'lp_{l.id}', 'icono': '🚑', 'color': 'danger', 'texto': f"<b>{l.empleado.nombre} {l.empleado.apellido}</b> cargó una Licencia.", 'tiempo': "Requiere Acción", 'url': reverse('detalle_empleado', args=[l.empleado.id])})

    pendientes_per = Permiso.objects.filter(estado='PENDIENTE').select_related('empleado')
    for p in pendientes_per:
        notificaciones.append({'id': f'pp_{p.id}', 'icono': '🎫', 'color': 'info', 'texto': f"<b>{p.empleado.nombre} {p.empleado.apellido}</b> solicitó un Permiso.", 'tiempo': "Requiere Acción", 'url': reverse('detalle_empleado', args=[p.empleado.id])})


    # --- HELPER: Función para procesar Salidas ---
    def agregar_salida(queryset, prefijo_id, icono, color, motivo):
        for obj in queryset:
            dias = (obj.fecha_inicio - hoy).days
            tiempo_str = "Hoy" if dias == 0 else ("Mañana" if dias == 1 else f"En {dias} días")
            notificaciones.append({
                'id': f'{prefijo_id}_{obj.id}', 'icono': icono, 'color': color, 
                'texto': f"<b>{obj.empleado.nombre} {obj.empleado.apellido}</b> inicia {motivo}.", 
                'tiempo': tiempo_str, 'url': reverse('detalle_empleado', args=[obj.empleado.id])
            })

    # Evaluamos Salidas para los 3 tipos
    agregar_salida(SolicitudVacaciones.objects.filter(estado='APROBADO', fecha_inicio__gt=hoy, fecha_inicio__lte=limite_salida).select_related('empleado'), 'vs', '✈️', 'primary', 'vacaciones')
    agregar_salida(Licencia.objects.filter(estado='APROBADO', fecha_inicio__gt=hoy, fecha_inicio__lte=limite_salida).select_related('empleado'), 'ls', '🏥', 'danger', 'licencia médica')
    agregar_salida(Permiso.objects.filter(estado='APROBADO', fecha_inicio__gt=hoy, fecha_inicio__lte=limite_salida).select_related('empleado'), 'ps', '🚪', 'info', 'un permiso')


    # --- HELPER: Función para procesar Regresos ---
    def agregar_regreso(queryset, prefijo_id, icono, color, motivo):
        for obj in queryset:
            dias = (obj.fecha_fin - hoy).days + 1  # +1 porque vuelven a trabajar el día DESPUÉS de finalizada
            tiempo_str = "Hoy" if dias == 0 else ("Mañana" if dias == 1 else f"En {dias} días")
            notificaciones.append({
                'id': f'{prefijo_id}_{obj.id}', 'icono': icono, 'color': color, 
                'texto': f"<b>{obj.empleado.nombre} {obj.empleado.apellido}</b> vuelve de {motivo}.", 
                'tiempo': tiempo_str, 'url': reverse('detalle_empleado', args=[obj.empleado.id])
            })

    # Evaluamos Regresos (generalmente los permisos son de 1 día, así que evaluamos solo vacas y licencias)
    agregar_regreso(SolicitudVacaciones.objects.filter(estado='APROBADO', fecha_fin__gte=hoy, fecha_fin__lte=limite_regreso).select_related('empleado'), 'vr', '🔄', 'success', 'vacaciones')
    agregar_regreso(Licencia.objects.filter(estado='APROBADO', fecha_fin__gte=hoy, fecha_fin__lte=limite_regreso).select_related('empleado'), 'lr', '🩺', 'success', 'licencia')

    return {
        'notificaciones': notificaciones[:8],
        'notificaciones_count': len(notificaciones)
    }