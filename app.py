#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# pylint: disable=W0102,E0712,C0103,R0903

""" PyFlex - VITA 49 decoder """

__author__ = "Fabian Malnero"
__copyright__ = "Copyright 2025, Fabian Malnero"
__license__ = "MIT"
__updated__ = "2025-06-18 23:21:51"

# ----------------------------------------------------------------------------------------------------------------------
# General imports
# ----------------------------------------------------------------------------------------------------------------------
import socket
import threading
import telnetlib
import time
import struct
import re
import sys
import os

# ----------------------------------------------------------------------------------------------------------------------
# Third-party imports
# ----------------------------------------------------------------------------------------------------------------------
import paho.mqtt.client as mqtt

# ----------------------------------------------------------------------------------------------------------------------
# Local imports
# ----------------------------------------------------------------------------------------------------------------------
import settings

# ----------------------------------------------------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------------------------------------------------
# -- Radio settings
UDP_IP = "0.0.0.0"
UDP_PORT = int(settings.Config.UDPPORT)
TELNET_HOST = settings.Config.FLEXIP
TELNET_PORT = int(settings.Config.FLEXPORT)
STN = settings.Config.STN
# -- MQTT settings
MQTT_BROKER = settings.Config.MQTT_HOST
MQTT_PORT = int(settings.Config.MQTT_PORT)
# -- Timers and internal radio settings
TELNET_TIMEOUT = 10
UDP_TIMEOUT_SECONDS = 20
ACTIVE_SLICE = 0
LAST_QRG = 0
LAST_BAND = "[0, 0]"

SUBSCRIBE_MESSAGES = [
    f'C0|client init PYAPP{STN}\n',
    "C1|sub slice 0\n",
    "C2|sub meter 4\n",
    "C3|sub meter 7\n",
    "C4|sub meter 8\n",
    "C5|sub meter 10\n",
    "C6|sub meter 11\n",
    f'C7|client udpport {UDP_PORT}\n'
]

# ----------------------------------------------------------------------------------------------------------------------
# MQTT client
# ----------------------------------------------------------------------------------------------------------------------
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, clean_session=True)  # noqa
mqtt_client.connect(f'{MQTT_BROKER}', MQTT_PORT, 60)

# ----------------------------------------------------------------------------------------------------------------------
# TELNET client
# ----------------------------------------------------------------------------------------------------------------------
def telnet_listener():
    """
    TCP socket control: subscribe meters and get frequency.
    """
    global LAST_QRG, LAST_BAND, ACTIVE_SLICE
    while True:
        try:
            tn = telnetlib.Telnet(TELNET_HOST, TELNET_PORT, TELNET_TIMEOUT)

            for msg in SUBSCRIBE_MESSAGES:
                tn.write(msg.encode('ascii'))
                time.sleep(0.5)

            """
            List slices: subscribe dynamically at correct slice
            """
            def list_slices():
                while True:
                    tn.write(b"C999|slice list\n")
                    time.sleep(2)

            thread_slice = threading.Thread(target=list_slices, daemon=True)
            thread_slice.start()

            while True:
                data = tn.read_until(b'\n', timeout=1)

                if data:
                    line = data.decode('utf-8', errors='ignore').strip()

                    if line.startswith("R999|0|"):
                        if line == "R999|0|" and ACTIVE_SLICE != 9:
                            tn.write(b"C1000|unsub slice all\n")
                            ACTIVE_SLICE = 9
                        elif line == "R999|0|0" and ACTIVE_SLICE != 0:
                            tn.write(b"C1001|sub slice 0\n")
                            ACTIVE_SLICE = 0
                        elif line == "R999|0|1" and ACTIVE_SLICE != 1:
                            tn.write(b"C1002|sub slice 1\n")
                            ACTIVE_SLICE = 1
                        elif line == "R999|0|0 1" and ACTIVE_SLICE != 0:
                            tn.write(b"C1003|sub slice 0\n")
                            ACTIVE_SLICE = 0

                    if ACTIVE_SLICE == 9:
                        mqtt_client.publish(f'{STN}/band', str("[0, 0]"))
                        mqtt_client.publish(f'{STN}/qrg', str(0))
                    else:
                        mqtt_client.publish(f'{STN}/band', LAST_BAND)
                        mqtt_client.publish(f'{STN}/qrg', LAST_QRG)

                    match_qrg = re.search(r"RF_frequency=([0-9.]+)", line)

                    if match_qrg:
                        frequency = float(match_qrg.group(1))
                        LAST_QRG = frequency * 100000
                        mqtt_client.publish(f'{STN}/qrg', LAST_QRG)

                        band = obtain_band(frequency)
                        LAST_BAND = str([band, 0])
                        mqtt_client.publish(f'{STN}/band', LAST_BAND)
                    else:
                        pass

        except Exception as e:
            print(f"[TELNET] Error or disconnected: {e}")
            print("[TELNET] Reconnect in 5 seconds...")
            time.sleep(5)
            mqtt_client.publish(f'{STN}/band', str("[0, 0]"))
            mqtt_client.publish(f'{STN}/qrg', str(0))

# ----------------------------------------------------------------------------------------------------------------------
# UDP listener
# ----------------------------------------------------------------------------------------------------------------------
def udp_listener():
    """
    UDP socket: to receive radio stream data
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))

    while True:
        data, addr = sock.recvfrom(4096)
        update_udp_timestamp()
        info = process_vita49(data)
        print(f"[Debug] {info}")

# ----------------------------------------------------------------------------------------------------------------------
# Control UDP activity
# ----------------------------------------------------------------------------------------------------------------------
def create_udp_monitor():
    timestamp = {"last_update": time.time()}

    def update_timestamp():
        timestamp["last_update"] = time.time()

    def get_inactive_time():
        return time.time() - timestamp["last_update"]

    return update_timestamp, get_inactive_time


# Create the monitor functions
update_udp_timestamp, get_udp_inactive_time = create_udp_monitor()


# ----------------------------------------------------------------------------------------------------------------------
# UDP activity monitor
# ----------------------------------------------------------------------------------------------------------------------
def udp_activity_monitor():
    """
    Check UDP data: activity in UDP socket
    """
    while True:
        time.sleep(5)
        inactive = get_udp_inactive_time()

        if inactive > UDP_TIMEOUT_SECONDS:
            print(f"[UDP] No data in {UDP_TIMEOUT_SECONDS} seconds. Restarting connection...")
            os.execv(sys.executable, ['python'] + sys.argv)

        else:
            print(f"[UDP] Inactivity: {inactive} seconds")

# ----------------------------------------------------------------------------------------------------------------------
# Process VITA 49
# ----------------------------------------------------------------------------------------------------------------------
def process_vita49(data):
    """
    Process VITA49: unpack, decode and MQTT publish of meters
    """
    header_format = "!BBHIQIQ"
    header_size = struct.calcsize(header_format)

    try:
        (packet_type, timestamp_type,
         length, stream_id, class_id, timestamp_int, timestamp_frac) = struct.unpack(header_format, data[0:header_size])

        print(f"[Debug] Packet type: {packet_type}")
        print(f"[Debug] Timestamp type: {timestamp_type}")
        print(f"[Debug] Length: {length}")
        print(f"[Debug] Stream ID: {stream_id}")
        print(f"[Debug] Class ID: {class_id}")
        print(f"[Debug] Timestamp int: {timestamp_int}")
        print(f"[Debug] Timestamp frac: {timestamp_frac}")

        payload = data[header_size:]
        meter_data = {}
        for meter_id, meter_value in struct.iter_unpack("!hh", payload):
            meter_data[meter_id] = meter_value

        output = ""
        for meter_id, value in meter_data.items():
            meter_id = f'0{meter_id}'[-2:]

            match meter_id:
                case "04":
                    mqtt_client.publish(f'{STN}/tensiona', value / 256)
                case "07":
                    mqtt_client.publish(f'{STN}/fun', value)
                case "08":
                    mqtt_client.publish(f'{STN}/pwr',  10 ** ((value / 128) / 10) * 1e-3)
                case "10":
                    mqtt_client.publish(f'{STN}/swr', value / 128)
                case "11":
                    mqtt_client.publish(f'{STN}/temp', value / 64)

        return output.strip()

    except struct.error as e:
        return f"[UDP] [STRUCT_ERROR]: {e}"
    except Exception as e:
        return f"[UDP] [CATCHALL_ERROR]: {e}"

# ----------------------------------------------------------------------------------------------------------------------
# Convert frequency to band
# ----------------------------------------------------------------------------------------------------------------------
def obtain_band(frequency):
    """
    Return band in meters from frequency in MHz
    """
    bands = {
        160: (1.7, 2.1),
        80: (3.3, 4.0),
        60: (5.0, 5.5),
        40: (6.0, 8.0),
        30: (9.0, 13.0),
        20: (13.0, 16.0),
        17: (16.0, 19.0),
        15: (19.0, 22.0),
        12: (22.0, 25.5),
        10: (25.5, 30.0),
    }

    for band, (start, end) in bands.items():
        if start <= frequency <= end:
            return band
    return 0

# ----------------------------------------------------------------------------------------------------------------------
# Threads init with main loop
# ----------------------------------------------------------------------------------------------------------------------
def main():
    thread_udp = threading.Thread(target=udp_listener, daemon=True)
    thread_telnet = threading.Thread(target=telnet_listener, daemon=True)
    thread_watchdog = threading.Thread(target=udp_activity_monitor, daemon=True)

    thread_udp.start()
    thread_telnet.start()
    thread_watchdog.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[MAIN] Closing program...")
        mqtt_client.disconnect()
        sys.exit(0)

# ----------------------------------------------------------------------------------------------------------------------
# Program main
# ----------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    main()


