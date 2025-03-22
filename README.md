### W1-to-MQTT

w1_run.py is a python script for Raspberries to read temperature sensors (e.g. DS18B20) attached to the W1 bus and publish the results to MQTT.

Most configuration of the script has to be provided by a json file. This includes the MQTT broker connection parameters and the sensor definitions. The sensor definition are used to publish device configuration messages for effortless integration into HomeAssistant.

Files:
w1_run.py: The script
w1_config.json: Configuration of MQTT broker and temperature sensors.
mqtt_wrapper.py: Python class to ease the connection to the MQTT broker.
w1.service: Systemctl service defionition to run w1_run.py as service.
