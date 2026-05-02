<div align="center">

# 🍃 AUSEN
### Sistema de Gestión de Vacaciones y Licencias

**🌐 Web Oficial:** [www.ausen.com.ar](https://www.ausen.com.ar)

---

![Python](https://img.shields.io/badge/Python-3.10-blue?style=for-the-badge&logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-5.0-092E20?style=for-the-badge&logo=django&logoColor=white)
![Bootstrap](https://img.shields.io/badge/Bootstrap-5-563D7C?style=for-the-badge&logo=bootstrap&logoColor=white)
![Status](https://img.shields.io/badge/Status-En_Producción-success?style=for-the-badge)

<br>

<img src="screenshots/light_mode.png" alt="Dashboard Principal Light" width="100%" style="border-radius: 10px; box-shadow: 0 10px 20px rgba(0,0,0,0.1);">

</div>

---

## 📋 Descripción

**AUSEN** es una solución integral desarrollada para optimizar la gestión de recursos humanos en cooperativas y PyMES. El sistema digitaliza el proceso de solicitud, aprobación y seguimiento de vacaciones y licencias, reemplazando las planillas manuales por una interfaz web intuitiva y automatizada.

El objetivo principal es brindar transparencia a los empleados sobre sus días disponibles y facilitar a la administración el control del ausentismo mediante métricas en tiempo real.

## ✨ Características Principales

* **📊 Dashboard Interactivo:** Visualización de métricas clave (ausentismo mensual, distribución diaria, empleados activos) para la toma de decisiones.
* **📅 Motor Inteligente de Feriados:** Integración con API de feriados nacionales para importación automática al calendario.
* **⚡ Cálculo Flexible de Días:** Al solicitar vacaciones, el sistema permite elegir entre descontar **"Días Corridos"** o **"Solo Días Hábiles"**, detectando y saltando automáticamente fines de semana y feriados cargados en el sistema.
* **🧮 Cálculo Automático (LCT):** Algoritmo que determina los días de vacaciones correspondientes según la antigüedad del empleado (basado en la Ley de Contrato de Trabajo Argentina).
* **👥 Gestión de Empleados:** Sistema completo de administración de perfiles (ABM) con historial laboral y datos personales.
* **📩 Notificaciones Automáticas:** Envío de correos electrónicos a RRHH y supervisores cada vez que se genera o aprueba una solicitud.
* **📄 Reportes PDF:** Generación instantánea de reportes de saldos y solicitudes listos para imprimir y firmar.
* **🔐 Seguridad:** Sistema de autenticación robusto con panel de configuración protegido para administradores.

---

## 📸 Tour del Sistema

### Gestión del Personal y Perfiles
| Nómina de Empleados | Portal del Empleado (Legajo) |
| :---: | :---: |
| <img src="screenshots/nomina_empleados.png" alt="Nomina Empleados" width="100%" style="border-radius: 8px;"> | <img src="screenshots/portal_empleado.png" alt="Portal Empleado" width="100%" style="border-radius: 8px;"> |

### Motor de Ausencias y Configuración
| Cálculo Inteligente de Fechas | Panel de Configuración de Sistema |
| :---: | :---: |
| <img src="screenshots/calculo_inteligente.png" alt="Cálculo Inteligente" width="100%" style="border-radius: 8px;"> | <img src="screenshots/config.png" alt="Configuración" width="100%" style="border-radius: 8px;"> |

### Herramientas y Visualización
| Motor de Feriados | Interfaz Adaptativa (Modo Oscuro) |
| :---: | :---: |
| <img src="screenshots/feriados.png" alt="Gestión de Feriados" width="100%" style="border-radius: 8px;"> | <img src="screenshots/dark_mode.png" alt="Modo Oscuro" width="100%" style="border-radius: 8px;"> |

<br>

<div align="center">
  <img src="screenshots/login.png" alt="Login Screen" width="50%" style="border-radius: 10px; box-shadow: 0 10px 20px rgba(0,0,0,0.1);">
  <p><i>Pantalla de Acceso Seguro (Autenticación)</i></p>
</div>

---

## 🛠️ Stack Tecnológico

* **Backend:** Python, Django Framework.
* **Librerías Clave:** `holidays` (Cálculo de fechas), `xhtml2pdf` (Reportes).
* **Base de Datos:** PostgreSQL (Producción) / SQLite (Local).
* **Frontend:** HTML5, CSS3, Bootstrap 5.3, JavaScript.
* **Gráficos:** Chart.js, FullCalendar.
* **Despliegue:** Render + Neon.tech (Gunicorn & WhiteNoise).
* **Control de Versiones:** Git & GitHub.

## 🚀 Instalación y Despliegue Local

Si deseas correr este proyecto en tu entorno local, sigue estos pasos:

1. **Clonar el repositorio**
   ```bash
   git clone [https://github.com/Manuseq94/AUSEN.git](https://github.com/Manuseq94/AUSEN.git)
   cd AUSEN

2. **Crear y activar entorno virtual**
   ```bash
   python -m venv venv
# En Windows:
    venv\Scripts\activate
# En Linux/Mac:
    source venv/bin/activate

3. **Instalar dependencias**
   ```bash
   pip install -r requirements.txt

4. **Configurar variables de entorno**
   Crea un archivo `.env` en la raíz del proyecto (puedes copiar el `.env.example`) y configura tus claves:
   ```env
   SECRET_KEY=tu_clave_secreta_aqui
   DEBUG=True
   EMAIL_HOST_PASSWORD=tu_clave_app_gmail

5. **Migrar base de datos y correr servidor**
   ```bash
   python manage.py migrate
   python manage.py runserver


## 👤 Autor

**Emanuel Sequeira**
* Full Stack Developer
* [LinkedIn](https://www.linkedin.com/in/emanuel-sequeria-a8360b231?utm_source=share_via&utm_content=profile&utm_medium=member_ios)
* [GitHub](https://github.com/Manuseq94)

---
<div align="center">
  <sub>Desarrollado con ❤️ para la gestión eficiente de equipos.</sub>
</div>
