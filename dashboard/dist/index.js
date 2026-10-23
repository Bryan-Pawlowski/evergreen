/*
 * Evergreen has no browser-dashboard tab.
 *
 * The Configurator ships as a desktop pane (desktop/plugin.js), not a page in
 * the web dashboard, so this plugin's manifest marks its tab `hidden`. The web
 * dashboard still loads every manifest's `entry`, though, and a manifest whose
 * entry is missing logs a load failure on every visit. This file exists so that
 * load succeeds.
 *
 * The loader is a classic <script> (not an ES module), so there is no `import`
 * or `export` here, and it reports NO_REGISTER for a plugin that loads but
 * registers nothing. Registering a component that renders null satisfies that
 * check without adding a tab or any visible surface.
 */
(function () {
  "use strict";

  var registry = window.__HERMES_PLUGINS__;
  if (!registry || typeof registry.register !== "function") {
    return;
  }

  function EvergreenHasNoTab() {
    return null;
  }

  registry.register("evergreen", EvergreenHasNoTab);
})();
