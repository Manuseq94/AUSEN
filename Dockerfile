# Usamos una imagen oficial y súper liviana de Python
FROM python:3.11-slim

# Evitamos que Python genere archivos basura (.pyc) y forzamos la salida por consola
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Creamos la carpeta de trabajo dentro del contenedor
WORKDIR /app

# Instalamos los compiladores y librerías C que necesita pycairo/xhtml2pdf
RUN apt-get update && apt-get install -y \
    gcc \
    pkg-config \
    libcairo2-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*
# El rm -rf final es para borrar la caché de descargas de Linux y mantener el contenedor liviano

# Copiamos solo el requirements primero para aprovechar el caché de Docker
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Ahora sí, copiamos todo el resto del código del proyecto
COPY . /app/


ENV SECRET_KEY=clave_temporal_para_build_12345
ENV DEBUG=False

# 👇 PASO CLAVE: Recolectamos todos los archivos estáticos (CSS/JS) dentro del contenedor
RUN python manage.py collectstatic --noinput

# Exponemos el puerto 8000 que es el que Render espera escuchar
EXPOSE 8000

# Comando para encender el motor de la app
CMD ["gunicorn", "gestion_vacaciones.wsgi:application", "--bind", "0.0.0.0:8000"]