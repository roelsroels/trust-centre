#!/usr/bin/env python3
from flask.cli import FlaskGroup

from portal.app import create_app


if __name__ == "__main__":
    FlaskGroup(create_app=create_app)()
