"""
Configuración principal de la aplicación 'core'.

Este módulo define la clase de configuración requerida por Django
para registrar la aplicación y establecer sus parámetros principales.
"""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    """
    Configuración específica para la aplicación 'core'.

    Define el tipo de campo auto-incremental por defecto y el nombre
    de la aplicación dentro del ecosistema del proyecto Django.

    Atributos:
        default_auto_field (str): Tipo de campo primario por defecto para los modelos.
        name (str): Nombre interno identificador de la aplicación.
        verbose_name (str): Nombre legible de la aplicación para mostrar en interfaces 
                            como el panel de administración (Django Admin).
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
    
    # Nombre amigable para mostrar en el panel de control, mejorando la experiencia del superusuario.
    verbose_name = 'Gestión Principal (Core)'
