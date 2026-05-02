from django.db import migrations
from datetime import date

def curar_fechas_historicas(apps, schema_editor):
    # Obtenemos los modelos de esta forma especial para las migraciones
    Licencia = apps.get_model('core', 'Licencia')
    Permiso = apps.get_model('core', 'Permiso')
    hoy = date.today()

    # Curar Licencias
    for lic in Licencia.objects.filter(fecha_solicitud=hoy):
        lic.fecha_solicitud = lic.fecha_inicio
        lic.save()

    # Curar Permisos
    for per in Permiso.objects.filter(fecha_solicitud=hoy):
        per.fecha_solicitud = per.fecha_inicio
        per.save()

class Migration(migrations.Migration):

    dependencies = [
        # 👇 CAMBIA '0001_initial' POR EL NOMBRE DE TU MIGRACIÓN ANTERIOR SI ES DIFERENTE 👇
        ('core', '0002_licencia_fecha_solicitud_permiso_fecha_solicitud'), 
    ]

    operations = [
        migrations.RunPython(curar_fechas_historicas),
    ]


    