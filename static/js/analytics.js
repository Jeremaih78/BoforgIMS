function boforgTrackWhatsApp() {
    if (typeof gtag === "function") {
        gtag('event', 'whatsapp_click', {
            'event_category': 'engagement',
            'event_label': window.location.pathname,
            'value': 1
        });
    }
}
