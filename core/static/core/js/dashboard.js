document.addEventListener('DOMContentLoaded', function() {

    // ==========================================
    // 0. RECEPCIÓN DE DATOS DESDE DJANGO
    // ==========================================
    const data = window.AUSEN_DATA || { torta: [], barras: [] };
    const isDarkMode = document.documentElement.getAttribute('data-bs-theme') === 'dark';
    const colorTexto = isDarkMode ? '#e9ecef' : '#495057';
    const colorGrid = isDarkMode ? '#495057' : '#e9ecef';

    // ==========================================
    // 1. INICIALIZACIÓN DEL CALENDARIO
    // ==========================================
    try {
        var calendarEl = document.getElementById('calendar');
        var inputFecha = document.getElementById('ir-fecha');

        if (calendarEl) {
            var calendar = new FullCalendar.Calendar(calendarEl, {
                initialView: 'dayGridMonth',
                locale: 'es', themeSystem: 'bootstrap5',
                headerToolbar: { left: 'prev,next today', center: 'title', right: 'dayGridMonth,listWeek' },
                buttonText: { today: 'Hoy', month: 'Mes', week: 'Semana', day: 'Día', list: 'Lista' },
                events: '/api/calendario/',

                eventDidMount: function(info) {
                    new bootstrap.Tooltip(info.el, { title: info.event.extendedProps.observaciones || "Sin observaciones", placement: 'top', container: 'body' });
                },

                windowResize: function(view) {
                    if (window.innerWidth < 768) {
                        calendar.changeView('listMonth');
                    } else {
                        calendar.changeView('dayGridMonth');
                    }
                },

                datesSet: function(info) {
                    var currentStr = info.view.currentStart.toISOString().slice(0, 7);
                    if(inputFecha) inputFecha.value = currentStr;
                }
            });

            calendar.render();

            if(inputFecha) {
                inputFecha.addEventListener('change', function() {
                    if (this.value) calendar.gotoDate(this.value);
                });
            }
        }
    } catch (error) {
        console.error("Error cargando Calendario:", error);
    }

    // ==========================================
    // 2. CONFIGURACIÓN DE GRÁFICOS (CHARTS)
    // ==========================================
    try {
        // --- Gráfico Torta ---
        const ctxTorta = document.getElementById('chartTorta');
        if (ctxTorta) {
            new Chart(ctxTorta, {
                type: 'doughnut',
                data: {
                    labels: ['Presentes', 'Ausentes'],
                    datasets: [{
                        data: data.torta, // Usamos la variable inyectada
                        // CAMBIO: Verde Salvia y Terracota Suave
                        backgroundColor: ['#68947b', '#d99c85'], 
                        borderWidth: 0,
                        hoverOffset: 4
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'bottom', labels: { color: colorTexto, font: { family: "'Inter', sans-serif" } } }
                    }
                }
            });
        }

        // --- Gráfico Barras ---
        const ctxBarras = document.getElementById('chartBarras');
        if (ctxBarras) {
            new Chart(ctxBarras, {
                type: 'bar',
                data: {
                    labels: ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic'],
                    datasets: [{
                        label: 'Solicitudes y Licencias',
                        data: data.barras, // Usamos la variable inyectada
                        // CAMBIO: Azul Acero translúcido con borde sólido
                        backgroundColor: 'rgba(91, 121, 153, 0.8)', 
                        borderColor: '#5b7999',
                        borderWidth: 1,
                        borderRadius: 4 // Bordes redondeados modernos
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        y: { beginAtZero: true, grid: { color: colorGrid }, ticks: { color: colorTexto, stepSize: 1 } },
                        x: { grid: { display: false }, ticks: { color: colorTexto } }
                    },
                    plugins: { legend: { display: false } }
                }
            });
        }
    } catch (error) {
        console.error("Error cargando Gráficos:", error);
    }
});