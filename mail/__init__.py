"""
Mail package for the Proton Mail MCP server.

Intentionally empty of imports: the pure helpers here (date_utils, folders, ids)
must stay importable without configuration or a Bridge connection, so tool
registration is left to ``mail.tools``, which main.py imports explicitly.
"""
