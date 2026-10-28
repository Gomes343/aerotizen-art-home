/* Bridge between Art Home and AeroTizen's js/homewatch.js (MIT, Hamza El Alaoui), which ships
   unchanged in the package. It expects a window.AeroTizen object with these pieces; we provide only
   what it uses. The service (service/index.js, also unchanged) listens on 127.0.0.1:47790 on the TV. */
(function () {
  'use strict';
  window.AeroTizen = {
    config: { SERVICE_ID: 'DclHome001.Watcher', SERVICE_URL: 'http://127.0.0.1:47790' },
    tizen: window.tizen || null,
    // In AeroTizen this maps preloaded app ids to store ids; ours are already the real ids.
    catalog: { preloadedStoreId: function () { return null; } },
    toast: function (text) { if (window.ArtHome) { window.ArtHome.toast(text); } }
  };
})();
