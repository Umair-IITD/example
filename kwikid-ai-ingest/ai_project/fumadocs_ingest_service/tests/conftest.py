"""
tests/conftest.py

Test suite configuration.

Sets safe defaults for environment variables that .env may override
with production settings, ensuring tests are hermetic and don't require
a real Supabase / external service connection to pass.

Uses os.environ.setdefault so an explicit override in the shell still wins.
"""
import os

# Ensure tests default to inmemory audit backend regardless of what .env contains.
# Individual tests that need to test the supabase backend use monkeypatch.setenv.
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
