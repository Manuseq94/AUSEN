"""
Configuración de la interfaz de administración para la aplicación 'core'.

Registra los modelos y define cómo se muestran en el panel de control (Django Admin).
"""

from django.contrib import admin
from .models import BolsaVacaciones, ConsumoDetalle, Empleado, Feriado, SolicitudVacaciones


class BolsaInline(admin.TabularInline):
    """
    Representación tabular de las bolsas de vacaciones en la vista de Empleado.

    Atributos:
        model (Model): Modelo asociado, en este caso BolsaVacaciones.
        extra (int): Formulario extra en blanco (0 para mantener la interfaz limpia).
        readonly_fields (tuple): Campos no editables directamente por los usuarios admin.
    """
    model = BolsaVacaciones
    extra = 0
    # Evitar la edición manual de 'dias_restantes' para preservar la integridad de datos,
    # dado que estos valores deberían actualizarse solo a través de la lógica de negocio.
    readonly_fields = ('dias_restantes',)


@admin.register(Empleado)
class EmpleadoAdmin(admin.ModelAdmin):
    """
    Configuración de la vista de administración para el modelo Empleado.

    Atributos:
        list_display (tuple): Campos a mostrar como columnas en el listado general.
        search_fields (tuple): Campos sobre los que operará la barra de búsqueda.
        list_filter (tuple): Campos bajo los cuales agrupar los filtros laterales.
        inlines (list): Integración de componentes relacionados (BolsaVacaciones) en la vista de detalle.
    """
    list_display = ('legajo', 'apellido', 'nombre', 'sector', 'antiguedad', 'activo')
    search_fields = ('apellido', 'dni', 'legajo')
    list_filter = ('sector', 'activo')
    # Administrar bolsas desde la vista principal de Empleado para reducir navegación.
    inlines = [BolsaInline]


@admin.register(SolicitudVacaciones)
class SolicitudAdmin(admin.ModelAdmin):
    """
    Configuración de la vista de administración para el modelo SolicitudVacaciones.

    Atributos:
        list_display (tuple): Campos a mostrar en la grilla del panel.
        list_filter (tuple): Opciones en la barra de la derecha para filtrar registros.
        date_hierarchy (str): Fechas empleadas para renderizar atajos de navegación (drill-down por fecha).
        list_select_related (tuple): Para optimizar carga forzando INNER JOIN SQL.
    """
    list_display = ('empleado', 'fecha_inicio', 'fecha_fin', 'dias_totales', 'estado')
    list_filter = ('estado', 'fecha_inicio')
    date_hierarchy = 'fecha_inicio'
    
    # Optimización: Reducir problemas de consultas N+1 cargando 'empleado' en el mismo query.
    list_select_related = ('empleado',)


@admin.register(Feriado)
class FeriadoAdmin(admin.ModelAdmin):
    """
    Configuración de la vista de administración para el modelo Feriado.

    Atributos:
        list_display (tuple): Atributos visibles dentro del panel lista.
        ordering (tuple): Configura el orden por defecto por fecha.
    """
    list_display = ('fecha', 'descripcion')
    ordering = ('fecha',)


@admin.register(BolsaVacaciones)
class BolsaVacacionesAdmin(admin.ModelAdmin):
    """
    Configuración de la vista general para el modelo BolsaVacaciones.
    Aunque suele gestionarse mediante inlines, permite una vista unificada si fuera necesario.
    """
    pass


@admin.register(ConsumoDetalle)
class ConsumoDetalleAdmin(admin.ModelAdmin):
    """
    Configuración del panel de administración para ConsumoDetalle.
    """
    pass