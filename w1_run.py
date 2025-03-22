# -*- coding: utf-8 -*-
"""
@author: Martin
"""
import re
import argparse
import json
import time
import logging
import logging.config
from pathlib import Path
from mqtt_wrapper import mqtt_wrapper
from graceful_killer import GracefulKiller

# =========================================================================
# argument handling definitions
DESCRIPTION = '''
Read devices on W1 bus and push measurements to a mqtt broker.
'''
parser = argparse.ArgumentParser(
    description=DESCRIPTION,
    formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument(
    '-j', '--jsonfile', type=str,
    default="./w1_config.json",
    help='Device definitions provided as json formatted text file.')
parser.add_argument(
    '-p', '--print', default=False,
    action=argparse.BooleanOptionalAction,
    help="Print sensor definitions once at startup.")
parser.add_argument(
    '-o', '--outfile', type=str,
    default="./w1-print.txt",
    help='Print inverter configuration and sensors definitions to given file.')
args = parser.parse_args()

# ======================================================

# initialize program configuraton -----------------
with open(Path(args.jsonfile), 'r') as fp:
    config = json.load(fp)

# setup logging
logging.config.dictConfig(config["logging"])
logging.info("Initial configuration of w1_run finalized.")

logging.debug(f"arg --jsonfile:   {args.jsonfile}")
logging.debug(f"arg --print:      {args.print}")
logging.debug(f"arg --outfile:    {args.outfile}")

# -- w1 protocol comes with access via file system
base_dir = Path('/sys/bus/w1/devices/')

# -- w1 functionality ---------------------------------------------------------

# regular expression for file content evaluation
reg_yes = re.compile('.*YES$')          # 1st line of w1_slave
reg_temp = re.compile('.*t=(\d*)$')     # 2nd line of w1_slave


def read_device_id(file_path):
    '''
    Reads the device name from file directory/name. The device name
    is the string given as first line of the name file.

    Assumption: The first line is the device_id.
    '''
    if file_path.is_file:
        f = open(file_path, 'r')
        rc = f.readline().strip()
        f.close
        return rc
    else:
        logging.warning(f"Opening of device {str(file_path)} failed.")
        return None


def read_temperature(file_path):
    '''
    Read temperature value from a file.
    Return values in temeperature [C] and [F]

    Assumptions:
    The file consist at least of 2 lines.
    The first lineends wit "YES", if a measurement is available.
    The 2nd line ends with "t=" followed by digits representing
    the temperature value.
    '''
    if file_path.is_file:
        f = open(file_path, 'r')
        lines = f.readlines()
        f.close()
    else:
        logging.warning(f"Reading temperature from {str(file_path)} failed.")
        return None

    if len(lines) < 2:
        logging.warning(f"Reading temperature - file empty {str(file_path)}.")
        return None

    # Analyze if the last 3 characters are 'YES'.
    match_yes = reg_yes.match(lines[0].strip())
    match_temp = reg_temp.match(lines[1].strip())
    if match_yes and match_temp:
        temp_C = float(match_temp.group(1)) / 1000.
        return temp_C
    else:
        logging.debug("Reading temperature - no match.")
        return None


def get_devices():
    # find all device name files
    name_files = sorted(base_dir.glob('*/name'))

    # build list with id and data file
    devices = []
    for name_file in name_files:
        device_id = read_device_id(name_file)
        logging.debug(f"Found device {str(device_id)}.")
        if device_id:
            device = {
                'id': device_id,
                'data_file': name_file.with_name('w1_slave')
                }
            devices.append(device)
    return devices


def read_device_values(devices):
    '''
    Read temperature values from devices found on w1 bus and
    push results to mqtt.

    Devices are checked if configured in configuration file.

    Parameters
    ----------
    devices: dictionary of device configuration (id -> dict)

    Returns
    -------
    present_devices: list of device ids found on w1 bus

    '''
    # initiate empty list for devices actually present on bus
    present_devices = []
    # ----------------------------------------------------
    # for each device on bus read the temperature values
    for found in get_devices():  # found device on bus
        fid = found['id']
        temp_C = read_temperature(found['data_file'])
        try:
            name = devices[fid]["name"]
            try:
                topic = config["topic_prefix"] + devices[fid]["state_topic"]
                client.pub(topic,
                           json.dumps({
                                "temperature": temp_C,
                                "status": "PRESENT",
                                "name": name,
                                "device_id": fid,
                                "timestamp": time.time(),
                                })
                           )
            # "time": time.strftime("%Y-%m-%d ") + time.strftime("%H:%M:%S"),
            # do not report if MQTT value topic of device is undefined
            except KeyError:
                logging.warning(f"Inconsistent device configuration {fid}.")
                pass
        except KeyError:
            logging.info(f"Unconfigured device {fid}.")
            topic = config["topic_prefix"] + str(fid)
            client.pub(topic,
                       json.dumps({
                           "status": "NOT CONFIGURED",
                           "name": "unknown",
                           "device_id": fid,
                           "timestamp": time.time(),
                           })
                       )
        present_devices.append(fid)
    return present_devices


def check_present_devices(devices, present_devices):
    '''
    Check if all devices configured are member of the list present_devices.
    If a configured device is not found in the list, a state message with the
    state value "MISSING" is pushed to mqtt.

    Parameters
    ----------
    devices: dictionary of device configuration (id -> dict)
    present_devices : array of device ids


    Returns
    -------
    None.

    '''
    # ----------------------------------------------------
    # search for configured but missing devices
    for cid in devices.keys():
        if cid not in present_devices:  # if device is missing
            try:
                logging.info(f"Missing device {cid}.")
                topic = config["topic_prefix"] + devices[cid]["state_topic"]
                client.pub(topic,
                           json.dumps({
                                "temperature": "none",
                                "status": "MISSING",
                                "name": devices[cid]["name"],
                                "device_id": cid,
                                "timestamp": time.time(),
                                })
                           )
            except KeyError:
                logging.warning(f"Inconsistent device configuration for {cid}.")
                continue  # do nothing if configuration is invalid


def ha_device_discovery(devices, template):
    '''
    Publish device discovery messages for configured devices.

    Parameters
    ----------
    devices: dictionary of device configuration (id -> dict)
    template: common content for all device discovery messages

    Returns
    -------
    None.

    '''
    # ----------------------------------------------------
    # search for configured but missing devices
    for cid, device in devices.items():

        discovery = {"unique_id": cid, "object_id": cid}
        discovery.update(template)
        discovery.update(device)
        discovery.update({
            "state_topic": config["topic_prefix"] + device["state_topic"],
            })
        try:
            topic = config["ha_discovery_topic"] + device["name"] + "/config"
            logging.debug(f"Device discovery topic: {topic}.")
            logging.debug(str(discovery))
            client.pub(topic, json.dumps(discovery), qos=1, retain=True)
        except KeyError:
            continue  # do nothing if configuration is invalid


# ----------------------------------------------------------------------------
if __name__ == '__main__':

    # set up mqtt connection
    qos = config["mqtt"]["qos"]
    topic_prefix = config["topic_prefix"]

    client = mqtt_wrapper(config["mqtt"]["broker"],
                          config["mqtt"]["port"],
                          config["mqtt"]["topic"])

    # read values of devices defined in configuration file
    devices = config["devices"]

    # initialize loop parameters for device discovery
    ha_max_loop = config["ha_discovery_rep"]
    ha_current_loop = ha_max_loop

    # loop until terminated or killed
    killer = GracefulKiller()
    while not killer.kill_now:
        then = time.time()    # for measurement of time to read sensors

        # scan w1 bus and read temperature from found devices
        present_devices = read_device_values(devices)
        # all hands up ?
        check_present_devices(devices, present_devices)

        # push mqtt device discovery messages
        if ha_current_loop >= ha_max_loop:
            ha_device_discovery(devices, config["template"])
            ha_current_loop = 0

        if killer.kill_now:
            # terminate if SIGINT (^C) or SIGTERM (kill) has been caught
            break
        else:
            ha_current_loop += 1
            # sleep for the rest of the configured period
            elapsed = time.time() - then
            remains = config["period"] - elapsed
            if remains > 0:
                time.sleep(remains)

    # ----------------------------------------------------
    # -- tidy up MQTT
    client.close()
