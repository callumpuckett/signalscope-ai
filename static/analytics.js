(function () {
    'use strict';
    var root = document.getElementById('sr-analytics');
    if (!root) return;
    var config = JSON.parse(root.getAttribute('data-config'));
    var banner = document.getElementById('sr-consent-banner');
    var status = document.getElementById('sr-consent-status');
    var allowed = false;
    var loaded = false;
    var localChoice = '';
    try { localChoice = localStorage.getItem('sr_analytics_choice') || ''; } catch (_) {}

    function clearAnalyticsCookies() {
        document.cookie.split(';').forEach(function (entry) {
            var name = entry.split('=')[0].trim();
            if (!/^_ga(?:_|$)/.test(name)) return;
            ['', location.hostname, '.stockradarhq.com'].forEach(function (domain) {
                document.cookie = name + '=; Max-Age=0; Path=/; SameSite=Lax' +
                    (domain ? '; Domain=' + domain : '') + (location.protocol === 'https:' ? '; Secure' : '');
            });
        });
    }

    function event(name) {
        if (!allowed) return;
        // Only fixed, non-personal values enter the data layer. Never include event targets,
        // form values, document.title, the real URL/query, or Stripe references.
        var args = ['event', name, {send_to: config.measurement,
            page_location: location.origin + config.page_path, page_title: config.page_title}];
        (function () { window.dataLayer.push(arguments); }).apply(null, args);
    }

    function start() {
        if (loaded || !config.client_id) return;
        allowed = true;
        loaded = true;
        window['ga-disable-' + config.measurement] = false;
        window.dataLayer = window.dataLayer || [];
        function gtag() { window.dataLayer.push(arguments); }
        // Basic mode: these commands and the container exist only after acceptance.
        gtag('consent', 'default', {analytics_storage: 'denied', ad_storage: 'denied',
            ad_user_data: 'denied', ad_personalization: 'denied'});
        gtag('consent', 'update', {analytics_storage: 'granted'});
        var referrer = '';
        try { referrer = new URL(document.referrer).origin + '/'; } catch (_) {}
        var campaign = {};
        var query = new URLSearchParams(location.search);
        // Only known campaign conventions are forwarded; unknown incoming values are omitted.
        var source = query.get('utm_source') || '';
        var medium = query.get('utm_medium') || '';
        var name = query.get('utm_campaign') || '';
        if (/^(beehiiv|stockradar_weekly|stockradar)$/.test(source)) campaign.source = source;
        if (/^(email|newsletter|website)$/.test(medium)) campaign.medium = medium;
        if (/^(stockradar_week[0-9]{2}|stockradar_weekly|weekly_brief)$/.test(name)) campaign.name = name;
        // The existing GTM GA4 tag owns configuration and its one automatic page_view.
        // Set privacy-safe defaults before the container can initialize that tag.
        gtag('set', {client_id: config.client_id, session_id: config.session_id,
            page_location: location.origin + config.page_path, page_title: config.page_title,
            page_referrer: referrer, allow_google_signals: false,
            allow_ad_personalization_signals: false,
            campaign_source: campaign.source || undefined,
            campaign_medium: campaign.medium || undefined,
            campaign_name: campaign.name || undefined});
        window.dataLayer.push({'gtm.start': Date.now(), event: 'gtm.js'});
        var script = document.createElement('script');
        script.async = true;
        script.src = 'https://www.googletagmanager.com/gtm.js?id=' + config.container;
        document.head.appendChild(script);
        if (config.page_path === '/upgrade') event('premium_view');
    }

    document.getElementById('sr-consent-preferences').addEventListener('click', function () {
        banner.hidden = false;
        banner.querySelector('button').focus();
    });
    root.querySelectorAll('[data-sr-consent]').forEach(function (button) {
        button.addEventListener('click', async function () {
            var choice = button.getAttribute('data-sr-consent');
            if (choice === 'rejected') {
                allowed = false;
                window['ga-disable-' + config.measurement] = true;
                clearAnalyticsCookies();
                try { localStorage.setItem('sr_analytics_choice', 'rejected'); } catch (_) {}
            }
            status.textContent = 'Saving preferences…';
            root.querySelectorAll('[data-sr-consent]').forEach(function (b) { b.disabled = true; });
            try {
                var response = await fetch('/analytics/consent', {method: 'POST', credentials: 'same-origin',
                    headers: {'Content-Type': 'application/json', 'X-CSRFToken': config.csrf},
                    body: JSON.stringify({choice: choice})});
                var result = await response.json();
                if (!response.ok || !result.saved) throw new Error('save failed');
                try { localStorage.setItem('sr_analytics_choice', choice); } catch (_) {}
                location.reload();
            } catch (_) {
                status.textContent = 'Preferences could not be saved. Analytics is off. Please try again.';
                allowed = false;
                window['ga-disable-' + config.measurement] = true;
                root.querySelectorAll('[data-sr-consent]').forEach(function (b) { b.disabled = false; });
            }
        });
    });
    document.addEventListener('click', function (e) {
        var link = e.target.closest('a');
        if (!link) return;
        try {
            var target = new URL(link.href, location.origin);
            if (target.origin === location.origin && /^\/(upgrade|premium)\/?$/.test(target.pathname)) {
                event('premium_upgrade_click');
            }
        } catch (_) {}
    });
    document.addEventListener('submit', function (e) {
        if (e.target.matches('form[action="/create-checkout-session"]')) event('premium_upgrade_click');
        // begin_checkout is emitted server-side only after Stripe Session creation succeeds.
    });
    window.addEventListener('storage', function (e) {
        if (e.key === 'sr_analytics_choice' && e.newValue === 'rejected') {
            allowed = false;
            window['ga-disable-' + config.measurement] = true;
            clearAnalyticsCookies();
            location.reload();
        }
    });
    if (config.choice === 'accepted' && localChoice !== 'rejected') start();
    else {
        clearAnalyticsCookies();
        banner.hidden = !(config.choice === '' || (config.choice === 'accepted' && localChoice === 'rejected'));
    }
}());
