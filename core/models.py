"""
Modelos de la aplicación 'core'.

Define las estructuras de base de datos para Empleados, Vacaciones,
Licencias, Feriados y la lógica de negocio asociada a cada entidad.
"""

import os
from datetime import date, timedelta

import holidays
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver


class RangoTemporalMixin(models.Model):
    """
    Mixin utilitario para extraer lógica de negocio que calcula 
    el total de días transcurridos entre dos fechas.
    Aplicable a modelos como Licencia y Permiso (Evita repetir código - DRY).
    """
    class Meta:
        abstract = True

    @property
    def dias_totales(self):
        """
        Calcula la cantidad de días naturales entre fecha_inicio y fecha_fin inclusivos.
        """
        if hasattr(self, 'fecha_inicio') and hasattr(self, 'fecha_fin'):
            if self.fecha_inicio and self.fecha_fin:
                return (self.fecha_fin - self.fecha_inicio).days + 1
        return 0


class Empleado(models.Model):
    """
    Representa a un miembro del personal dentro de la organización.
    Vinculado opcionalmente a un User de Django para acceso al sistema.
    """
    SECTORES = [
        ('IT', 'Tecnología'),
        ('RRHH', 'Recursos Humanos'),
        ('Ventas', 'Ventas'),
        ('Admin', 'Administración'),
        ('Deposito', 'Depósito/Logística'),
        ('Internet', 'Internet'),
        ('Energía', 'Energía'),
        ('Mantenimiento', 'Mantenimiento'),
    ]

    usuario = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)

    legajo = models.CharField(max_length=20, unique=True, help_text="ID único de empleado")
    dni = models.CharField(max_length=15, unique=True)
    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100)
    fecha_ingreso = models.DateField()
    sector = models.CharField(max_length=50, choices=SECTORES)
    localidad = models.CharField(max_length=100, default="Casa Central", help_text="Sucursal o ciudad")
    cargo = models.CharField(max_length=100)
    activo = models.BooleanField(default=True)
    observaciones = models.TextField(blank=True, null=True)
    email = models.EmailField(max_length=254, null=True, blank=True)

    def __str__(self):
        return f"{self.apellido}, {self.nombre} ({self.legajo})"

    @property
    def antiguedad(self):
        """
        Calcula los años de antigüedad del empleado respecto a la fecha actual.
        Tiene en cuenta el mes y día para un cálculo exacto.
        """
        hoy = date.today()
        # Se resta 1 si aún no ha cumplido el aniversario de ingreso este año
        return hoy.year - self.fecha_ingreso.year - (
            (hoy.month, hoy.day) < (self.fecha_ingreso.month, self.fecha_ingreso.day)
        )

    def calcular_dias_ley_argentina(self, anio_calculo):
        """
        Determina los días de vacaciones correspondientes según la Ley de Contrato de Trabajo.
        
        Args:
            anio_calculo (int): El año para el cual se computan las vacaciones.
            
        Returns:
            int: Cantidad de días de vacaciones correspondientes.
        """
        fecha_cierre_anio = date(anio_calculo, 12, 31)
        antiguedad_al_cierre = fecha_cierre_anio.year - self.fecha_ingreso.year
        
        # Ajuste de año parcial
        if (fecha_cierre_anio.month, fecha_cierre_anio.day) < (self.fecha_ingreso.month, self.fecha_ingreso.day):
            antiguedad_al_cierre -= 1

        if antiguedad_al_cierre < 5:
            return 14
        elif 5 <= antiguedad_al_cierre < 10:
            return 21
        elif 10 <= antiguedad_al_cierre < 20:
            return 28
        else:
            return 35

    @property
    def color_calendario(self):
        """
        Provee un color hexadecimal representativo del sector del empleado.
        Utilizado principalmente para renderizado visual en dashboards.
        """
        colores = {
            'IT': '#0d6efd', 
            'RRHH': '#6610f2', 
            'Ventas': '#198754',
            'Admin': '#ffc107', 
            'Deposito': '#6c757d', 
            'Internet': '#0dcaf0',
            'Energía': '#fd7e14',  # Corregida la tilde para coincidir con el choice 'Energía'
            'Mantenimiento': '#dc3545'
        }
        return colores.get(self.sector, '#6c757d')


class BolsaVacaciones(models.Model):
    """
    Representa el saldo anual de vacaciones disponible para un empleado específico.
    """
    empleado = models.ForeignKey(Empleado, on_delete=models.CASCADE, related_name='bolsas')
    anio = models.IntegerField(verbose_name="Año Correspondiente")
    dias_otorgados = models.IntegerField(default=0)
    dias_restantes = models.IntegerField(default=0)

    class Meta:
        unique_together = ('empleado', 'anio')
        ordering = ['anio']

    def __str__(self):
        return f"{self.empleado.legajo} - {self.anio}: {self.dias_restantes} días"


class SolicitudVacaciones(models.Model):
    """
    Modela una petición de descanso por parte del empleado, que de ser aprobada,
    descuenta automáticamente días de sus bolsas vacacionales disponibles.
    """
    ESTADOS = [
        ('PENDIENTE', 'Pendiente'), 
        ('APROBADO', 'Aprobado'), 
        ('RECHAZADO', 'Rechazado')
    ]

    empleado = models.ForeignKey(Empleado, on_delete=models.CASCADE, related_name='solicitudes')
    fecha_solicitud = models.DateField(auto_now_add=True)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()
    dias_totales = models.IntegerField(editable=False)
    observaciones = models.TextField(blank=True, null=True)

    solo_habiles = models.BooleanField(
        default=False,
        verbose_name="¿Contar solo días hábiles?",
        help_text="Si se marca, el sistema NO descontará fines de semana ni feriados."
    )

    estado = models.CharField(max_length=20, choices=ESTADOS, default='APROBADO')

    def calcular_dias_reales(self):
        """
        Devuelve la cantidad de días a descontar, ignorando feriados y findes si aplica.
        
        Returns:
            int: Días hábiles o corridos requeridos según configuración.
        """
        if not self.fecha_inicio or not self.fecha_fin:
            return 0

        if not self.solo_habiles:
            return (self.fecha_fin - self.fecha_inicio).days + 1

        # Optimización: Carga parcial del módulo holidays basada en los años afectados
        ar_feriados = holidays.AR(years=list(range(self.fecha_inicio.year, self.fecha_fin.year + 1)))

        # Se carga de manera lazy para evitar dependencias circulares a nivel módulo
        from django.apps import apps
        FeriadoModel = apps.get_model('core', 'Feriado')
        
        # Optimización: Queryset plano transformado a set O(1) para lookups veloces
        feriados_manuales = set(FeriadoModel.objects.values_list('fecha', flat=True))

        dias_a_contar = 0
        fecha_actual = self.fecha_inicio

        while fecha_actual <= self.fecha_fin:
            # Lunes=0 ... Sábado=5, Domingo=6. Excluimos fines de semana
            es_finde = fecha_actual.weekday() >= 5
            
            # Solo iteramos el contador si es laborable a todos los niveles
            if not es_finde and fecha_actual not in ar_feriados and fecha_actual not in feriados_manuales:
                dias_a_contar += 1

            fecha_actual += timedelta(days=1)

        return dias_a_contar

    def clean(self):
        """
        Valida integridad cronológica y capacidad máxima de saldo antes de guardar.
        """
        super().clean()
        if not self.fecha_inicio or not self.fecha_fin:
            return
            
        if self.fecha_fin < self.fecha_inicio:
            raise ValidationError("La fecha de fin no puede ser anterior a la de inicio.")

        dias_solicitados = self.calcular_dias_reales()

        if self.solo_habiles and dias_solicitados == 0:
            raise ValidationError("El rango seleccionado no tiene días hábiles (son todos feriados o findes).")

        # Validación estricta de saldo para evitar consumos en negativo
        if not self.pk and self.estado == 'APROBADO':
            saldo_total = BolsaVacaciones.objects.filter(
                empleado=self.empleado, dias_restantes__gt=0
            ).aggregate(total=models.Sum('dias_restantes'))['total'] or 0

            if saldo_total < dias_solicitados:
                raise ValidationError(f"Saldo insuficiente. Tiene {saldo_total}, requiere {dias_solicitados}.")

    def save(self, *args, **kwargs):
        """
        Intercepta el guardado para orquestar la transacción atómica
        de descuento de días en las bolsas asociadas al ser aprobada.
        """
        self.dias_totales = self.calcular_dias_reales()

        if self.pk:
            # Si es edición, se asume que las bolsas ya fueron modificadas en el pasado.
            # Lógica de re-liquidación suele delegarse a un servicio aparte.
            super().save(*args, **kwargs)
            return

        if self.estado == 'APROBADO':
            # Bloque atómico: Garantiza que un error en el descuento revierta la solicitud entera.
            with transaction.atomic():
                super().save(*args, **kwargs)
                bolsas = BolsaVacaciones.objects.filter(
                    empleado=self.empleado, dias_restantes__gt=0
                ).select_for_update()  # Bloqueo de concurrencia en lectura de DB

                dias_a_descontar = self.dias_totales
                for bolsa in bolsas:
                    if dias_a_descontar == 0:
                        break
                    
                    descuento = min(bolsa.dias_restantes, dias_a_descontar)
                    bolsa.dias_restantes -= descuento
                    bolsa.save()
                    
                    ConsumoDetalle.objects.create(solicitud=self, bolsa=bolsa, dias_descontados=descuento)
                    dias_a_descontar -= descuento
        else:
            super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.empleado} ({self.fecha_inicio} al {self.fecha_fin})"


class ConsumoDetalle(models.Model):
    """
    Pivot transaccional que rastrea de qué bolsa exacta se descontaron
    los días de una solicitud en particular. Vital para auditorías.
    """
    solicitud = models.ForeignKey(SolicitudVacaciones, on_delete=models.CASCADE, related_name='detalles')
    bolsa = models.ForeignKey(BolsaVacaciones, on_delete=models.CASCADE, related_name='detalles')
    dias_descontados = models.IntegerField()

    def __str__(self):
        return f"{self.dias_descontados} días del {self.bolsa.anio}"


class Licencia(RangoTemporalMixin):
    """
    Registro de ausencias contempladas en el marco legal o convenciones,
    distintas del régimen ordinario de vacaciones.
    """
    TIPOS = [
        ('ENFERMEDAD', 'Enfermedad'),
        ('ESTUDIO', 'Examen / Estudio'),
        ('MUDANZA', 'Mudanza'),
        ('MATRIMONIO', 'Matrimonio'),
        ('NACIMIENTO', 'Nacimiento / Paternidad'),
        ('OTRA', 'Otra Causa'),
    ]

    ESTADOS = [
        ('PENDIENTE', 'Pendiente de Revisión'),
        ('APROBADO', 'Aprobado'),
        ('RECHAZADO', 'Rechazado'),
    ]

    empleado = models.ForeignKey(Empleado, on_delete=models.CASCADE, related_name='licencias')
    tipo = models.CharField(max_length=20, choices=TIPOS)
    fecha_solicitud = models.DateField(auto_now_add=True, null=True)
    fecha_inicio = models.DateField(verbose_name="Fecha de Inicio")
    fecha_fin = models.DateField(verbose_name="Fecha de Fin")
    observaciones = models.TextField(blank=True, null=True)
    estado = models.CharField(max_length=10, choices=ESTADOS, default='PENDIENTE')

    def __str__(self):
        return f"{self.empleado} - {self.get_tipo_display()}"


class Feriado(models.Model):
    """
    Registro manual de fechas no laborables, ya sea por disposiciones
    locales, asuetos o eventos empresariales.
    """
    fecha = models.DateField(unique=True)
    descripcion = models.CharField(max_length=100)

    class Meta:
        ordering = ['fecha']
        verbose_name = "Feriado"
        verbose_name_plural = "Feriados"

    def __str__(self):
        return f"{self.descripcion} ({self.fecha.strftime('%d/%m')})"


class Permiso(RangoTemporalMixin):
    """
    Modela autorizaciones excepcionales que no catalogan ni como vacación ni como licencia formal.
    (Ej: Trabajo remoto, salidas por trámites).
    """
    TIPOS = [
        ('REMOTO', '🏠 Trabajo Remoto'),
        ('TRAMITE', '🏦 Trámite Personal / Salida Anticipada'),
        ('COMPENSATORIO', '⏱️ Franco Compensatorio'),
        ('OTRO', '📝 Otro Motivo'),
    ]

    ESTADOS = [
        ('PENDIENTE', 'Pendiente'), 
        ('APROBADO', 'Aprobado'), 
        ('RECHAZADO', 'Rechazado')
    ]

    empleado = models.ForeignKey(Empleado, on_delete=models.CASCADE, related_name='permisos')
    tipo = models.CharField(max_length=20, choices=TIPOS, default='REMOTO')
    fecha_solicitud = models.DateField(auto_now_add=True, null=True)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()
    motivo = models.TextField(blank=True, null=True, help_text="Detallar brevemente (opcional)")
    estado = models.CharField(max_length=10, choices=ESTADOS, default='PENDIENTE')

    def __str__(self):
        return f"{self.empleado} - {self.get_tipo_display()}"


def validar_extension(archivo):
    """
    Validador estricto para modelos de subida de archivos.
    
    Asegura que solo extensiones documentales seguras sean procesadas,
    previniendo vectores de ataque como inyección de ejecutables (.exe, .php).
    
    Args:
        archivo (File): El archivo temporal interceptado antes de guardarlo.
        
    Raises:
        ValidationError: Si la extensión o el tamaño infringen las reglas.
    """
    ext = os.path.splitext(archivo.name)[1].lower()
    extensiones_validas = ['.pdf', '.doc', '.docx', '.jpg', '.jpeg', '.png', '.txt']

    if ext not in extensiones_validas:
        raise ValidationError(f"Formato no permitido ({ext}). Solo se aceptan: PDF, Word o Imágenes.")

    # Defensa en profundidad: Bloquear uploads gigantescos que llenen el disco.
    limit_mb = 5
    if archivo.size > limit_mb * 1024 * 1024:
        raise ValidationError(f"El archivo es muy pesado. Máximo permitido: {limit_mb}MB")


class Documento(models.Model):
    """
    Digitalización del legajo: Almacena recibos de sueldo, certificados 
    y otros comprobantes vinculados a un empleado.
    """
    empleado = models.ForeignKey(Empleado, on_delete=models.CASCADE, related_name='documentos')
    titulo = models.CharField(max_length=100, help_text="Ej: Recibo Enero 2026")
    
    archivo = models.FileField(
        upload_to='legajos/%Y/',
        validators=[validar_extension]
    )
    fecha_subida = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.titulo} - {self.empleado}"


class AuditoriaSaldo(models.Model):
    """
    Traza de seguridad que registra qué usuario manipuló un saldo de vacaciones.
    No se borra en cascada respecto del User para no perder el historial legal.
    """
    autor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    empleado = models.ForeignKey(Empleado, on_delete=models.CASCADE)
    fecha = models.DateTimeField(auto_now_add=True)
    accion = models.CharField(max_length=200)

    def __str__(self):
        return f"{self.fecha.strftime('%d/%m %H:%M')} - {self.autor} -> {self.empleado}"


# =========================================================
# SEÑALES (SIGNALS) - MANTENIMIENTO AUTOMÁTICO
# =========================================================

@receiver(post_delete, sender=Documento)
def auto_delete_file_on_delete(sender, instance, **kwargs):
    """
    Asegura la coherencia de datos borrando el fichero de disco 
    cuando la base de datos elimina el puntero (el registro Documento).
    Aplica para borrados individuales o cascadas desde un Empleado padre.
    """
    if instance.archivo:
        if os.path.isfile(instance.archivo.path):
            os.remove(instance.archivo.path)