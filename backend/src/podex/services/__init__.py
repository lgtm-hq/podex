"""Application service layer.

Modules under this package group storage-focused helpers that the API routes
delegate to (query services), plus cross-cutting infrastructure like rate
limiting and caching. Route handlers stay thin: validate inputs, call a
service, then serialize.
"""
