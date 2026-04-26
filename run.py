#!/usr/bin/env python3
"""Entry point for Task Board."""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8893))
    debug = os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true')
    app.run(host='0.0.0.0', port=port, debug=debug)
