from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import Empleado, BolsaVacaciones, AuditoriaSaldo
from django.contrib.auth.models import User
import datetime


class Command(BaseCommand):
    help = 'Carga automática de vacaciones (LCT Argentina)'

    def handle(self, *args, **kwargs):
        hoy = timezone.now().date()

        # 1. CANDADO DE FECHA 🔒
        if hoy.month < 10:
            # Mensaje especial para el dashboard
            self.stdout.write("⛔ DENEGADO: Aún no es 1 de Octubre. No se realizaron cambios.")
            return

        anio_a_cargar = hoy.year
        usuario_sistema = User.objects.filter(is_superuser=True).first()

        empleados = Empleado.objects.filter(activo=True)
        contador_nuevos = 0
        contador_omitidos = 0

        for emp in empleados:
            # 2. EVITAR DUPLICADOS
            if BolsaVacaciones.objects.filter(empleado=emp, anio=anio_a_cargar).exists():
                contador_omitidos += 1
                continue

            # 3. LÓGICA DE ANTIGÜEDAD (LCT ARGENTINA)
            fecha_calculo = datetime.date(anio_a_cargar, 12, 31)
            antiguedad_anios = fecha_calculo.year - emp.fecha_ingreso.year - (
                    (fecha_calculo.month, fecha_calculo.day) < (emp.fecha_ingreso.month, emp.fecha_ingreso.day)
            )
            delta = fecha_calculo - emp.fecha_ingreso
            dias_antiguedad = delta.days

            dias_corresponden = 0
            if antiguedad_anios >= 20:
                dias_corresponden = 35
            elif antiguedad_anios >= 10:
                dias_corresponden = 28
            elif antiguedad_anios >= 5:
                dias_corresponden = 21
            elif dias_antiguedad >= 180:
                dias_corresponden = 14
            else:
                dias_corresponden = dias_antiguedad // 20

            if dias_corresponden > 0:
                BolsaVacaciones.objects.create(
                    empleado=emp,
                    anio=anio_a_cargar,
                    dias_restantes=dias_corresponden,  # Ajusta según tus campos
                    fecha_vencimiento=datetime.date(anio_a_cargar + 2, 12, 31)
                )
                # Auditoría silenciosa
                AuditoriaSaldo.objects.create(
                    autor=usuario_sistema,
                    empleado=emp,
                    accion=f"🤖 Renovación {anio_a_cargar}: +{dias_corresponden} días."
                )
                contador_nuevos += 1

        # 4. MENSAJE FINAL INTELIGENTE 🧠
        if contador_nuevos > 0:
            self.stdout.write(
                f"✅ ÉXITO: Se cargaron vacaciones a {contador_nuevos} empleados para el año {anio_a_cargar}.")
        else:
            self.stdout.write(
                f"👌 SIN CAMBIOS: Las vacaciones del año {anio_a_cargar} ya estaban cargadas para todo el personal.")