$(document).ready(function () {

    // ── Sidebar Toggle ──────────────────────────────────────────────────────
    $('#sidebarToggle').click(function () {
        if ($(window).width() <= 768) {
            $('.sidebar').toggleClass('active');
        } else {
            $('body').toggleClass('sidebar-collapsed');
        }
    });

    // Close sidebar when clicking outside on mobile
    $(document).click(function (e) {
        if ($(window).width() <= 768) {
            if (!$(e.target).closest('.sidebar').length &&
                !$(e.target).closest('#sidebarToggle').length) {
                $('.sidebar').removeClass('active');
            }
        }
    });

    // ── Staggered card animation ────────────────────────────────────────────
    $('.stat-card, .chart-card, .card, .segment-card').each(function (i) {
        $(this).css('animation-delay', `${i * 0.06}s`);
    });

});

// ── Toast notification ──────────────────────────────────────────────────────
function showToast(message, type = 'success') {
    const icon = type === 'success' ? 'fa-check-circle' : 'fa-exclamation-circle';
    const color = type === 'success' ? '#10b981' : '#ef4444';
    const toast = $(`
        <div class="toast-notification ${type}">
            <i class="fas ${icon}" style="color:${color}; font-size:18px"></i>
            <span>${message}</span>
        </div>
    `);
    $('body').append(toast);
    setTimeout(() => toast.fadeOut(300, () => toast.remove()), 3000);
}

// ── Number formatter ────────────────────────────────────────────────────────
function formatRupiah(num) {
    return new Intl.NumberFormat('id-ID', { style: 'currency', currency: 'IDR', maximumFractionDigits: 0 }).format(num);
}
