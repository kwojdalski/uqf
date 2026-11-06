"""The HTTP routes, one module per area (#2168). Each area's `install(app, ...)`
adds its routes to the app create_app builds, in the order app.py calls them.
There is no facade: import a name from the module that defines it."""
