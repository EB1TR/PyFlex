#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# pylint: disable=W0102,E0712,C0103,R0903

""" PyFlex - VITA 49 decoder """

__author__ = "Fabian Malnero"
__copyright__ = "Copyright 2025, Fabian Malnero"
__license__ = "MIT"
__updated__ = "2025-06-18 23:19:54"


from os import environ
from dotenv import load_dotenv, find_dotenv


load_dotenv(find_dotenv())


class Config:
    """
    This is the generic loader that sets common values in the absence of an .env file or environment variables.
    """

    # -- FlexRadio radio settings
    UDPPORT = environ.get('UDPPORT', "60000")
    FLEXIP = environ.get('FLEXIP', "127.0.0.1")
    FLEXPORT = environ.get('FLEXPORT', "5000")
    STN = environ.get('STN', "0")

    # -- MQTT Server
    MQTT_HOST = environ.get('MQTT_HOST', "localhost")
    MQTT_PORT = environ.get('MQTT_PORT', "1883")
