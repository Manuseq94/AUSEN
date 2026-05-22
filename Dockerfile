# Usamos una imagen oficial y súper liviana de Python
FROM python:3.10-slim

# Evitamos que Python genere archivos basura (.pyc) y forzamos la salida por consola
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Creamos la carpeta de trabajo dentro del contenedor
WORKDIR /app

# 👇 NUEVO: Instalamos los compiladores y librerías C que necesita pycairo/xhtml2pdf
RUN apt-get update && apt-get install -y \
    gcc \
    pkg-config \
    libcairo2-dev \
    && rm -rf /var/lib/apt/lists/*
# 👆 El rm -rf final es para borrar la caché de descargas de Linux y mantener el contenedor liviano

# Copiamos solo el requirements primero para aprovechar el caché de Docker
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Ahora sí, copiamos todo el resto del código del proyecto
COPY . /app/

# Exponemos el puerto 8000 que es el que Render espera escuchar
EXPOSE 8000

# Comando para encender el motor de la app
# IMPORTANTE: Reemplazá 'nombre_de_tu_proyecto' por el nombre de la carpeta donde está tu archivo wsgi.py (suele llamarse 'ausen' o 'config')
CMD ["gunicorn", "ausen.wsgi:application", "--bind", "0.0.0.0:8000"]