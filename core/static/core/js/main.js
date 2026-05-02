// core/static/core/js/main.js

document.addEventListener('DOMContentLoaded', function () {
    // 1. Inicialización de Toasts
    const toastElList = document.querySelectorAll('.toast');
    [...toastElList].map(toastEl => {
        const toast = new bootstrap.Toast(toastEl, { delay: 5000 });
        toast.show();
        return toast;
    });

    // 2. Lógica del Calendario
    var calendarEl = document.getElementById('calendar');
    var gotoDateEl = document.getElementById('gotoDate');

    if (calendarEl) {
        var calendar = new FullCalendar.Calendar(calendarEl, {
            initialView: 'dayGridMonth',
            locale: 'es',
            buttonText: { today: 'Hoy', month: 'Mes', week: 'Semana', day: 'Día', list: 'Lista' },
            headerToolbar: { left: 'prev,next today', center: 'title', right: 'dayGridMonth,listMonth' },
            views: { dayGridMonth: { buttonText: 'Mes' }, listMonth: { buttonText: 'Lista' } },
            height: 550,
            navLinks: true,
            events: '/api/calendario/',
            windowResize: function (view) {
                if (window.innerWidth < 768) {
                    calendar.changeView('listMonth');
                } else {
                    calendar.changeView('dayGridMonth');
                }
            },
            datesSet: function (info) {
                var currentStr = info.view.currentStart.toISOString().slice(0, 7);
                if (gotoDateEl) gotoDateEl.value = currentStr;
            }
        });

        if (gotoDateEl) {
            gotoDateEl.addEventListener('change', function () {
                if (this.value) calendar.gotoDate(this.value);
            });
        }

        var tabEl = document.querySelector('button[data-bs-target="#calendario-pane"]');
        if (tabEl) {
            tabEl.addEventListener('shown.bs.tab', function (event) {
                calendar.render();
            });
        }

        calendar.render();
    }
});