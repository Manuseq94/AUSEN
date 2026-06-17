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

// Lógica Global de Modo Oscuro
const btnTheme = document.getElementById('btnTheme');
const iconTheme = btnTheme ? btnTheme.querySelector('i') : null;
const html = document.documentElement;
const savedTheme = localStorage.getItem('theme') || 'light';

html.setAttribute('data-bs-theme', savedTheme);
updateIcon(savedTheme);

if (btnTheme) {
    btnTheme.addEventListener('click', () => {
        const currentTheme = html.getAttribute('data-bs-theme');
        const newTheme = currentTheme === 'light' ? 'dark' : 'light';
        html.setAttribute('data-bs-theme', newTheme);
        localStorage.setItem('theme', newTheme);
        updateIcon(newTheme);
    });
}

function updateIcon(theme) {
    if (!iconTheme) return;
    if(theme === 'dark') { 
        iconTheme.classList.replace('bi-moon-stars-fill', 'bi-sun-fill'); 
        btnTheme.classList.replace('btn-outline-secondary', 'btn-outline-light'); 
    } else { 
        iconTheme.classList.replace('bi-sun-fill', 'bi-moon-stars-fill'); 
        btnTheme.classList.replace('btn-outline-light', 'btn-outline-secondary'); 
    }
}

/* ==========================================
   SISTEMA DE NOTIFICACIONES (RRHH)
   ========================================== */
document.addEventListener('DOMContentLoaded', function() {
    const notiItems = document.querySelectorAll('.noti-item');
    const badgeCount = document.getElementById('badgeNotisCount');
    const redDot = document.getElementById('redDotNotis');

    // 1. Buscamos qué notificaciones ya clickeó el usuario hoy
    let leidas = JSON.parse(localStorage.getItem('ausen_notis_leidas')) || [];
    let unreadCount = 0;

    // 2. Revisamos cada notificación que mandó el servidor
    notiItems.forEach(item => {
        const notiId = item.getAttribute('data-noti-id');
        
        if (leidas.includes(notiId)) {
            // Si ya está leída, le sacamos el fondo oscuro
            item.classList.remove('unread-noti');
        } else {
            // Si no está leída, sumamos 1 al contador
            unreadCount++;
        }

        // 3. Al hacer clic, la marcamos como leída en memoria
        item.addEventListener('click', function() {
            if (!leidas.includes(notiId)) {
                leidas.push(notiId);
                // Limpiamos historial viejo para no saturar memoria
                if (leidas.length > 50) leidas.shift(); 
                localStorage.setItem('ausen_notis_leidas', JSON.stringify(leidas));
            }
        });
    });

    // 4. Actualizamos los numeritos en el HTML
    if (badgeCount) badgeCount.textContent = unreadCount;
    if (redDot && unreadCount > 0) {
        redDot.style.display = 'block';
    }
});