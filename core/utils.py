"""
Módulo de utilidades compartidas para la aplicación 'core'.

Contiene funciones auxiliares, helpers y servicios transversales 
que pueden ser consumidos por cualquier vista, señal o modelo del sistema.
"""

import logging
import threading
from typing import List, Union

from django.conf import settings
from django.core.mail import send_mail

# Instanciación del logger para registrar eventos de este módulo específico
logger = logging.getLogger(__name__)


def _tarea_enviar_email_background(asunto: str, mensaje: str, from_email: str, destinatarios: list):
    """
    Función interna oculta que hace el trabajo pesado en segundo plano.
    Maneja sus propias excepciones para no interrumpir el hilo principal.
    """
    try:
        send_mail(
            subject=asunto,
            message=mensaje,
            from_email=from_email,
            recipient_list=destinatarios,
            fail_silently=False,
        )
    except Exception as error:
        # Registro silencioso del error para auditoría
        logger.error(
            f"Fallo crítico enviando email en segundo plano.\n"
            f"Asunto: '{asunto}'\n"
            f"Destinatarios: {destinatarios}\n"
            f"Detalle Técnico: {str(error)}"
        )


def enviar_notificacion_email(asunto: str, mensaje: str, destinatarios: Union[str, List[str]]) -> bool:
    """
    Servicio centralizado para el envío de correos electrónicos a través de la plataforma.
    
    Abstrae la complejidad integrando normalización de datos y lanza el proceso
    en un Hilo (Thread) separado. Esto evita cuellos de botella y tiempos de carga 
    largos en las vistas web, previniendo errores 500 por Timeouts del servidor.

    Args:
        asunto (str): Título del correo a enviar.
        mensaje (str): Cuerpo del correo en texto plano.
        destinatarios (str | list): Correo individual o lista de correos destino.

    Returns:
        bool: Siempre retorna True para liberar la vista rápidamente. El éxito real 
              del envío se gestiona de forma asíncrona.
    """
    # 1. Normalización de entrada
    if isinstance(destinatarios, str):
        destinatarios = [destinatarios]
        
    # 2. Obtener el remitente desde settings
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', settings.EMAIL_HOST_USER)
    
    # 3. Lanzar la tarea en un hilo secundario (Background Thread)
    hilo = threading.Thread(
        target=_tarea_enviar_email_background,
        args=(asunto, mensaje, from_email, destinatarios)
    )
    # Iniciamos el hilo (arranca el proceso en paralelo)
    hilo.start()
    
    # 4. Devolvemos True inmediatamente para que la página cargue al instante
    return True