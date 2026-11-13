"""The widget engine: runs widget.lua in an embedded Lua and draws it with Cairo.

It needs nothing but lupa and pycairo, so it works headless on every platform; the windows
the frames end up in come from `deskwidgets.platform`.
"""
