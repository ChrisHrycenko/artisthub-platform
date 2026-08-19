"""
app/schemas/__init__.py

Marshmallow validation schema package for ArtistHub.

Each schema module mirrors its corresponding model module:
    schemas/artist.py  ←→  models/artist.py

Schemas are used exclusively in route handlers to validate POST/PUT
request bodies before any database access occurs.
"""
