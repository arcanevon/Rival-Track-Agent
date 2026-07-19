# Frontend Structure

The frontend is intentionally kept framework-free, but the runtime is split by concern:

- `index.html` contains the page structure, vendor script tags, and server-injected runtime config.
- `styles.css` contains the visual system, layout, responsive rules, and component styles.
- `app.js` contains dashboard state, WebSocket handling, form submission, graph rendering, charts, and result rendering.
- `vendor/` contains pinned third-party browser libraries.

When adding new UI behavior, keep markup in `index.html`, styling in `styles.css`, and browser logic in `app.js`.
