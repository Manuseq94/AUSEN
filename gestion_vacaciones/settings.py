import os
import dj_database_url
from pathlib import Path
import dj_database_url
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured

# ==========================================
# 1. DIRECTORIO BASE Y ENTORNO
# ==========================================
# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Cargar las variables de entorno desde el archivo .env de forma unificada
load_dotenv(BASE_DIR / '.env')


# ==========================================
# 2. CONFIGURACIÓN DE SEGURIDAD GENERAL
# ==========================================

SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    raise ImproperlyConfigured("FALTA LA VARIABLE DE ENTORNO: SECRET_KEY")

# Seguridad contra entornos de producción: DEBUG será True o False de forma robusta
DEBUG = str(os.environ.get('DEBUG')).strip() == 'True'

# Dominios permitidos. Si DEBUG es False, Django solo funcionará en estos dominios.
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")


# ==========================================
# 3. DEFINICIÓN DE APLICACIONES Y MIDDLEWARES
# ==========================================
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'gestion_vacaciones.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'gestion_vacaciones.wsgi.application'


# ==========================================
# 4. BASE DE DATOS
# ==========================================
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

if 'DATABASE_URL' in os.environ:
    DATABASES['default'] = dj_database_url.config(conn_max_age=600, conn_health_checks=True,)

# ==========================================
# 5. VALIDACIÓN DE CONTRASEÑAS (AUTH)
# ==========================================
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# ==========================================
# 6. INTERNACIONALIZACIÓN (I18N)
# ==========================================
# https://docs.djangoproject.com/en/5.2/topics/i18n/
LANGUAGE_CODE = 'es-ar'
TIME_ZONE = 'America/Argentina/Buenos_Aires'
USE_I18N = True
USE_TZ = True


# ==========================================
# 7. ARCHIVOS ESTÁTICOS Y MULTIMEDIA
# ==========================================
# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'

# La carpeta donde Django juntará los estáticos en Render
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# (Opcional, si usaste carpetas static extra en tu proyecto)
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'core/static'),
]

# Configuración de WhiteNoise para comprimir y cachear los estáticos
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# --- MANEJO DE ARCHIVOS MULTIMEDIA (LEGAJO) ---
MEDIA_URL = '/media/'
# DRY: Uso de Pathlib en lugar de os.path.join
MEDIA_ROOT = BASE_DIR / 'media'


# ==========================================
# 8. CONFIGURACIONES CUSTOM DE LA APLICACIÓN
# ==========================================
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

# --- CONFIGURACIÓN DE SEGURIDAD Y SESIONES ---
# 1. Tiempo de Inactividad (en segundos): 600s = 10 minutos.
SESSION_COOKIE_AGE = 600

# 2. Reiniciar reloj con la actividad en cada page load.
SESSION_SAVE_EVERY_REQUEST = True

# 3. Cerrar al cerrar navegador (Opcional pero recomendado).
SESSION_EXPIRE_AT_BROWSER_CLOSE = True


# ==========================================
# 9. CONFIGURACIÓN DE EMAIL (SMTP EXT)
# ==========================================
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get('EMAIL_USER')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_PASS')
# F-String para mayor limpieza visual del remitente
DEFAULT_FROM_EMAIL = f"Sistema AUSEN <{EMAIL_HOST_USER}>"

# NOTA: MAILS_RRHH fue purgado. Ahora las notificaciones a RRHH se despachan 
# detectando dinámicamente a los administradores del sistema en views.py mediante _obtener_correos_rrhh().

# Si quieres probar sin enviar correos reales (solo consola),
# comenta las configuraciones SMTP de arriba y descomenta esta:
# EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
