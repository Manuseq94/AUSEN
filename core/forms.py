"""
Formularios para la aplicación 'core'.

Gestiona la creación, edición y validación de datos para Empleados,
Solicitudes de Vacaciones, Licencias, Permisos y otros modelos afines.
"""

from datetime import timedelta

import holidays
from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.utils import timezone

from .models import (
    BolsaVacaciones, Documento, Empleado, Feriado, Licencia, Permiso,
    SolicitudVacaciones
)


def obtener_vacaciones_solapadas(empleado, inicio, fin, solo_aprobadas=True, exclude_id=None):
    """
    Identifica si un rango de fechas interseca con vacaciones existentes del empleado.

    Args:
        empleado (Empleado): Referencia al modelo empleado.
        inicio (date): Límite inferior de la fecha a consultar.
        fin (date): Límite superior de la fecha a consultar.
        solo_aprobadas (bool): Si es True, ignora vacaciones 'PENDIENTES'. 
                               Si es False, excluye solo aquellas 'RECHAZADAS'.
        exclude_id (int, optional): PK de registro a ignorar (usado en vistas de edición para evitar falsos positivos con el propio registro).

    Returns:
        QuerySet: Registros de solicitudes que chocan con este rango temporal.
    """
    qs = SolicitudVacaciones.objects.filter(
        empleado=empleado
    ).filter(
        Q(fecha_inicio__lte=fin) & Q(fecha_fin__gte=inicio)
    )

    if solo_aprobadas:
        qs = qs.filter(estado='APROBADO')
    else:
        qs = qs.exclude(estado='RECHAZADO')

    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
        
    return qs


class EmpleadoForm(forms.ModelForm):
    """
    Formulario base para la creación y edición estructurada del modelo Empleado.

    Atributos:
        email (EmailField): Campo adicional no obligatorio para enlazar un usuario futuro.
    """
    email = forms.EmailField(
        label="Email (Vinculado al Usuario)",
        required=False,
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'ejemplo@cooperativa.com'})
    )

    class Meta:
        model = Empleado
        fields = ['nombre', 'apellido', 'dni', 'legajo', 'email', 'fecha_ingreso', 'sector', 'localidad', 'cargo', 'observaciones']
        widgets = {
            'fecha_ingreso': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'}),
            'observaciones': forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
            'nombre': forms.TextInput(attrs={'class': 'form-control'}),
            'apellido': forms.TextInput(attrs={'class': 'form-control'}),
            'dni': forms.TextInput(attrs={'class': 'form-control'}),
            'legajo': forms.TextInput(attrs={'class': 'form-control'}),
            'sector': forms.Select(attrs={'class': 'form-select'}),
            'localidad': forms.TextInput(attrs={'class': 'form-control'}),
            'cargo': forms.TextInput(attrs={'class': 'form-control'}),
        }


class EmpleadoEditarForm(EmpleadoForm):
    """
    Formulario especializado para editar un empleado existente.
    
    Hereda de EmpleadoForm para reutilizar widgets y validaciones, pero 
    aplica restricciones lógicas de inmutabilidad en campos críticos.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Inmutabilidad de datos históricos:
        # Se bloquea la fecha de ingreso dado que recalcular la antigüedad
        # retrospectivamente podría generar inconsistencias en vacaciones ya tomadas.
        self.fields['fecha_ingreso'].disabled = True
        self.fields['fecha_ingreso'].widget.attrs['class'] += ' bg-secondary-subtle'
        self.fields['fecha_ingreso'].help_text = "🔒 Dato crítico para cálculo de antigüedad."


class BolsaManualForm(forms.ModelForm):
    """
    Formulario para la carga o ajuste manual de días en una Bolsa de Vacaciones.
    """
    class Meta:
        model = BolsaVacaciones
        fields = ['anio', 'dias_restantes']
        labels = {
            'anio': 'Año Correspondiente',
            'dias_restantes': 'Días a cargar',
        }
        widgets = {
            'anio': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Ej: 2025'}),
            'dias_restantes': forms.NumberInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        self.empleado = kwargs.pop('empleado', None)
        super().__init__(*args, **kwargs)

    def clean_anio(self):
        """
        Garantiza que no se generen bolsas vacacionales para años futuros 
        que aún no sean exigibles por calendario comercial.

        Returns:
            int: Año limpio y validado.
        
        Raises:
            ValidationError: Si el año ingresado excede el límite permitido.
        """
        anio_ingresado = self.cleaned_data.get('anio')
        if not anio_ingresado:
            return anio_ingresado

        hoy = timezone.now().date()
        # Regla de negocio: A partir de octubre se puede otorgar la bolsa del año en curso.
        anio_limite = hoy.year if hoy.month >= 10 else hoy.year - 1

        if anio_ingresado > anio_limite:
            raise ValidationError(
                f"⛔ No puedes cargar el año {anio_ingresado} todavía. "
                f"Según la fecha actual ({hoy.strftime('%d/%m/%Y')}), "
                f"solo se permite cargar hasta el período {anio_limite}."
            )
        return anio_ingresado

    def clean(self):
        """
        Validaciones cruzadas complejas para la integridad de la bolsa de vacaciones.
        """
        cleaned_data = super().clean()
        dias_restantes = cleaned_data.get('dias_restantes')
        anio = cleaned_data.get('anio')

        if dias_restantes is not None and anio is not None and getattr(self, 'empleado', None):
            
            # Impedir duplicidad: Un empleado no debe tener múltiples bolsas para el mismo año.
            if self.instance.pk is None:
                if BolsaVacaciones.objects.filter(empleado=self.empleado, anio=anio).exists():
                    raise ValidationError({
                        'anio': f"⛔ El empleado ya tiene una bolsa para el año {anio}. Para modificarla, vuelve al perfil y haz clic sobre la tarjeta de ese año."
                    })

            # Límite Legal: Verificar que no estemos otorgando más vacaciones de las debidas por ley (LCT).
            maximo_legal = self.empleado.calcular_dias_ley_argentina(anio)
            if dias_restantes > maximo_legal:
                raise ValidationError({
                    'dias_restantes': f"Por LCT, a la fecha base del año {anio}, le corresponden como máximo {maximo_legal} días."
                })

        return cleaned_data


class SolicitudForm(forms.ModelForm):
    """
    Formulario transaccional para solicitar y programar vacaciones.
    """
    class Meta:
        model = SolicitudVacaciones
        fields = ['fecha_inicio', 'fecha_fin', 'solo_habiles', 'observaciones']
        widgets = {
            'fecha_inicio': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'}),
            'fecha_fin': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'}),
            'observaciones': forms.Textarea(
                attrs={'rows': 3, 'class': 'form-control', 'placeholder': 'Ej: Vacaciones de invierno'}
            ),
            'solo_habiles': forms.CheckboxInput(attrs={'class': 'form-check-input', 'role': 'switch'}),
        }

    def __init__(self, *args, **kwargs):
        self.empleado = kwargs.pop('empleado', None)
        super().__init__(*args, **kwargs)

        # Inmutabilidad en progreso: Si la solicitud ya fue aprobada,
        # su fecha de inicio no debe ser manipulable porque alteraría históricos de RRHH.
        if self.instance.pk and self.instance.estado == 'APROBADO':
            self.fields['fecha_inicio'].disabled = True
            self.fields['fecha_inicio'].widget.attrs['readonly'] = True
            self.fields['fecha_inicio'].widget.attrs['class'] += ' bg-secondary bg-opacity-10'
            self.fields['fecha_inicio'].help_text = "🔒 La fecha de inicio no se puede cambiar en una vacación en curso o ya pactada."

    def clean(self):
        """
        Validaciones profundas para aprobar una solicitud:
        Calcula saldo, choques con otras ausencias y lógica de días hábiles.
        """
        cleaned_data = super().clean()
        inicio = cleaned_data.get("fecha_inicio")
        fin = cleaned_data.get("fecha_fin")
        solo_habiles = cleaned_data.get("solo_habiles")

        if inicio and fin:
            if fin < inicio:
                self.add_error('fecha_fin', "La fecha de fin no puede ser anterior a la de inicio.")
                return cleaned_data

            if getattr(self, 'empleado', None):
                dias_pedidos = 0
                if not solo_habiles:
                    # Lógica natural: Todos los días del calendario cuentan
                    dias_pedidos = (fin - inicio).days + 1
                else:
                    # Optimización del cálculo indexando feriados en un rango específico para evitar overhead
                    ar_feriados = holidays.AR(years=list(range(inicio.year, fin.year + 1)))
                    fecha_act = inicio
                    while fecha_act <= fin:
                        # Identifica días laborables (Lun a Vie, sin feriados de LCT)
                        if fecha_act.weekday() < 5 and fecha_act not in ar_feriados:
                            dias_pedidos += 1
                        fecha_act += timedelta(days=1)

                # Cálculo del saldo agregado (ya consolidado por DB en un solo query sumatorio)
                saldo_total = BolsaVacaciones.objects.filter(
                    empleado=self.empleado
                ).aggregate(total=Sum('dias_restantes'))['total'] or 0

                # Reintegro virtual durante edición: Suma los días actualmente bloqueados por esta solicitud
                if self.instance.pk and self.instance.estado == 'APROBADO':
                    saldo_total += self.instance.dias_totales

                if dias_pedidos > saldo_total:
                    raise ValidationError(
                        f"⛔ Saldo insuficiente. Solicitas {dias_pedidos} días, pero solo tienes {saldo_total} disponibles."
                    )

                # Control de choques inter-módulos: Vacaciones vs Licencias médicas (Ej. Art. 208)
                licencias_chocan = Licencia.objects.filter(
                    empleado=self.empleado, estado='APROBADO'
                ).filter(
                    Q(fecha_inicio__lte=fin) & Q(fecha_fin__gte=inicio)
                )

                if licencias_chocan.exists():
                    raise ValidationError("⛔ CONFLICTO: Tienes una LICENCIA médica aprobada en estas fechas.")

                # Control intra-módulo: Vacaciones vs Otras Vacaciones
                # Importante: Excluimos las rechazadas para validar correctamente según la lógica de negocio original
                otras_vacas = obtener_vacaciones_solapadas(
                    self.empleado, inicio, fin, solo_aprobadas=False, exclude_id=self.instance.pk
                )

                if otras_vacas.exists():
                    raise ValidationError("⛔ Ya tienes otra solicitud de vacaciones en este rango.")

        return cleaned_data


class CrearUsuarioForm(forms.Form):
    """
    Formulario no vinculado a modelo para orquestar la creación 
    del auth.User de Django y su asignación a un Empleado.
    """
    empleado = forms.ModelChoiceField(
        queryset=Empleado.objects.filter(activo=True, usuario__isnull=True),
        required=False,
        label="Vincular a Empleado",
        empty_label="Seleccione un empleado...",
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs={'class': 'form-control'}))
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Obligatorio'})
    )
    password = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-control'}))
    es_admin = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))

    def clean_username(self):
        """Valida unicidad del nombre de usuario para el sistema Login."""
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise ValidationError("Este nombre de usuario ya existe.")
        return username


class LicenciaForm(forms.ModelForm):
    """
    Formulario para la gestión de licencias médicas, psiquiátricas, de estudio, etc.
    """
    class Meta:
        model = Licencia
        fields = ['tipo', 'fecha_inicio', 'fecha_fin', 'observaciones']
        widgets = {
            'tipo': forms.Select(attrs={'class': 'form-select'}),
            'fecha_inicio': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'fecha_fin': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'observaciones': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        self.empleado = kwargs.pop('empleado', None)
        self.usuario_actual = kwargs.pop('usuario', None)
        super().__init__(*args, **kwargs)

    def clean(self):
        """
        Garantiza que una licencia (imprevista usualmente) prevalezca correctamente 
        sobre vacaciones existentes, forzando la acción manual del RRHH para interrumpirlas.
        """
        cleaned_data = super().clean()
        inicio = cleaned_data.get('fecha_inicio')
        fin = cleaned_data.get('fecha_fin')

        if inicio and fin:
            if inicio > fin:
                self.add_error('fecha_inicio', "La fecha de inicio no puede ser posterior a la fecha de fin.")
                return cleaned_data

            if getattr(self, 'empleado', None):
                # Validar colisión contra vacaciones que ya estén aprobadas estrictamente
                vacaciones_chocan = obtener_vacaciones_solapadas(
                    self.empleado, inicio, fin, solo_aprobadas=True
                )

                if vacaciones_chocan.exists():
                    vaca = vacaciones_chocan.first()
                    fecha_fin_str = vaca.fecha_fin.strftime('%d/%m/%Y')

                    # Derivación de UX: RRHH tiene poder para alterar la DB on the fly (instrucciones resolutivas),
                    # el empleado solo recibe una advertencia.
                    if self.usuario_actual and self.usuario_actual.is_staff:
                        fecha_corte_sugerida = inicio - timedelta(days=1)
                        msg = (
                            f"⛔ INTERRUPCIÓN REQUERIDA: El empleado tiene vacaciones hasta el {fecha_fin_str}. "
                            f"Para cargar esta licencia, primero debes ACORTAR esas vacaciones. "
                            f"👉 Ve a la solicitud de vacaciones y cambia su Fecha de Fin al: {fecha_corte_sugerida.strftime('%d/%m/%Y')}. "
                            f"Al hacerlo, los días sobrantes volverán a la bolsa."
                        )
                    else:
                        msg = (
                            f"⛔ Tienes vacaciones aprobadas hasta el {fecha_fin_str}. "
                            f"Comunícate con tu empleador o RRHH para suspender tus vacaciones (y recuperar los días restantes) "
                            f"antes de cargar esta licencia."
                        )

                    raise ValidationError(msg)

        return cleaned_data


class FeriadoForm(forms.ModelForm):
    """
    Formulario para carga manual de Feriados o Asuetos excepcionales 
    que no estén pre-mapeados en la librería base de holidays.
    """
    class Meta:
        model = Feriado
        fields = ['fecha', 'descripcion']
        widgets = {
            'fecha': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'descripcion': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej: Día de la Independencia'}),
        }


class PermisoForm(forms.ModelForm):
    """
    Formulario transaccional para permisos excepcionales (salidas antes de hora, trámites).
    """
    class Meta:
        model = Permiso
        fields = ['tipo', 'fecha_inicio', 'fecha_fin', 'motivo']
        widgets = {
            'tipo': forms.Select(attrs={'class': 'form-select'}),
            'fecha_inicio': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'}),
            'fecha_fin': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'}),
            'motivo': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Ej: Necesito estar en casa...'}),
        }

    def __init__(self, *args, **kwargs):
        self.empleado = kwargs.pop('empleado', None)
        super().__init__(*args, **kwargs)

    def clean(self):
        """Evita la carga de permisos superfluos si el empleado se encuentra de vacaciones."""
        cleaned_data = super().clean()
        inicio = cleaned_data.get('fecha_inicio')
        fin = cleaned_data.get('fecha_fin')

        if inicio and fin:
            if inicio > fin:
                self.add_error('fecha_inicio', "Error en las fechas.")
                return cleaned_data

            if getattr(self, 'empleado', None):
                # Validar colisión contra vacaciones aprobadas
                vacaciones_chocan = obtener_vacaciones_solapadas(
                    self.empleado, inicio, fin, solo_aprobadas=True
                )

                if vacaciones_chocan.exists():
                    raise ValidationError("⛔ Conflicto: El empleado ya se encuentra de VACACIONES para estas fechas.")
        return cleaned_data


class DocumentoForm(forms.ModelForm):
    """
    Gestión de subida de archivos adjuntos y comprobantes varios al legajo del empleado.
    """
    class Meta:
        model = Documento
        fields = ['titulo', 'archivo']
        widgets = {
            'titulo': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej: Recibo Sueldo Enero'}),
            'archivo': forms.FileInput(attrs={'class': 'form-control'}),
        }