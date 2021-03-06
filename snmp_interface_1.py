#!/usr/bin/env python
"""snmp_interface: module called to generate SNMP monitoring data formatted for use with StatusBoard iPad App

# In this monitor, we want to aggregate TX + RX and redundant uplinks into a single value

# How To Calculate Bandwidth Utilization Using SNMP
# http://www.cisco.com/en/US/tech/tk648/tk362/technologies_tech_note09186a008009496e.shtml
"""
from __future__ import division    # So division of integers will result in float
from pysnmp.entity.rfc3413.oneliner import cmdgen
import time
import json

__author__ = 'forge@flakshack.com (Scott Vintinner)'

# Simple HTTP Server for testing
# python -m SimpleHTTPServer 9000


#=================================SETTINGS======================================
MAX_DATAPOINTS = 30
SAMPLE_INTERVAL = 60
GRAPH_TITLE = "Bandwidth (Mbps)"

# Standard SNMP OIDs
# sysUpTime	    1.3.6.1.2.1.1.3.0    (this is hundreds of a second)
# 64-bit counters because 32-bit defaults rollover too quickly
# ifHCInOctets	1.3.6.1.2.1.31.1.1.1.6.interfacenumber
# ifHCOutOctets	1.3.6.1.2.1.31.1.1.1.10.interfacenumber


# Enter the details for each SNMP counter.
# ip:  This is the IP address or resolvable host name
# community:  This is the SNMPv1 community that will grant access to read the OID (usually this is "public")
# oid:  This is the SNMP OID interface counter we'll be measuring.
# uptime_oid:  This is the SNMP OID for the device's uptime (so we know what the time was when we measured the counter)
# name:  This is the name of the device as it will appear on the graph
DEVICE_IP = "your-cisco-switch-ip-address"
DEVICE_SNMP = "public"
DEVICE_UPTIME_OID = "1.3.6.1.2.1.1.3.0"
AGGREGATE_INTERFACES = (
    {"oid": ["1.3.6.1.2.1.31.1.1.1.6.1", "1.3.6.1.2.1.31.1.1.1.10.1", "1.3.6.1.2.1.31.1.1.1.6.62", "1.3.6.1.2.1.31.1.1.1.10.62"], "name": "16"},
    {"oid": ["1.3.6.1.2.1.31.1.1.1.6.2", "1.3.6.1.2.1.31.1.1.1.10.2", "1.3.6.1.2.1.31.1.1.1.6.63", "1.3.6.1.2.1.31.1.1.1.10.63"], "name": "17"},
    {"oid": ["1.3.6.1.2.1.31.1.1.1.6.3", "1.3.6.1.2.1.31.1.1.1.10.3", "1.3.6.1.2.1.31.1.1.1.6.64", "1.3.6.1.2.1.31.1.1.1.10.64"], "name": "18"},
    {"oid": ["1.3.6.1.2.1.31.1.1.1.6.4", "1.3.6.1.2.1.31.1.1.1.10.4", "1.3.6.1.2.1.31.1.1.1.6.65", "1.3.6.1.2.1.31.1.1.1.10.65"], "name": "19"},
    {"oid": ["1.3.6.1.2.1.31.1.1.1.6.5", "1.3.6.1.2.1.31.1.1.1.10.5", "1.3.6.1.2.1.31.1.1.1.6.66", "1.3.6.1.2.1.31.1.1.1.10.66"], "name": "20"}
)
#================================================================================


class MonitorJSON:
    """This is a simple class passed to Monitor threads so we can access the current JSON data in that thread"""
    def __init__(self):
        self.json = output_message("Waiting " + str(SAMPLE_INTERVAL) + " seconds for first run", "")


class AggregateInterface:
    all_aggr_interfaces = []    # Static array containing all interfaces

    def __init__(self, name, oids):
        self.name = name
        self.interface_oids = oids          # List of oids
        self.snmp_data = []                 # Hold raw data
        self.datapoints = []                # Holds pretty data
        self.__class__.all_aggr_interfaces.append(self)     # Add self to static array


class SNMPDatapoint:
    def __init__(self, value, timeticks):
        self.value = value
        self.timeticks = timeticks



def get_snmp(device, community, snmp_oid1, snmp_oid2, snmp_oid3, snmp_oid4, snmp_uptime_oid):
    """Returns the value of the specified snmp OID.
     Also gets the uptime (TimeTicks) so we know exactly when the sample was taken."""

    # Perform a synchronous SNMP GET
    cmd_gen = cmdgen.CommandGenerator()
    error_indication, error_status, error_index, var_binds = cmd_gen.getCmd(
        cmdgen.CommunityData(community), cmdgen.UdpTransportTarget((device, 161)), snmp_oid1, snmp_oid2, snmp_oid3, snmp_oid4, snmp_uptime_oid
    )

    snmp_value1 = None
    snmp_value2 = None
    snmp_value3 = None
    snmp_value4 = None
    snmp_error = None
    snmp_uptime_value = None

    if error_indication:                         # Check for SNMP errors
        snmp_error = str(error_indication)
    else:
        if error_status:
            snmp_error = error_status.prettyPrint()
        else:
            # varBinds are returned as SNMP objects, so convert to integers
            snmp_value1 = int(var_binds[0][1])
            snmp_value2 = int(var_binds[1][1])
            snmp_value3 = int(var_binds[2][1])
            snmp_value4 = int(var_binds[3][1])
            snmp_uptime_value = int(var_binds[4][1])

    return snmp_value1, snmp_value2, snmp_value3, snmp_value4, snmp_uptime_value, snmp_error


def calculate_bps(current_sample_octets, current_sample_time, historical_sample_octets, historical_sample_time):
    """Calculate the bits-per-second based on the octets and timeticks (hundreths of a second)."""

    # When the SNMP counter reaches 18446744073709551615, it will rollover and reset to ZERO.
    # If this happens, we want to make sure we don't output a negative bps
    if current_sample_octets < historical_sample_octets:
        # If we reset to 0, add the max value of the octets counter
        current_sample_octets += 18446744073709551615

    delta = current_sample_octets - historical_sample_octets

    # SysUpTime is in TimeTicks (Hundreds of a second), so covert to seconds
    seconds_between_samples = (current_sample_time - historical_sample_time) / 100.0

    # Multiply octets by 8 to get bits
    bps = (delta * 8) / seconds_between_samples
    bps /= 1048576          # Convert to Mbps
    bps = round(bps, 2)
    return bps


def output_message(message, detail):
    """This function will output an error message formatted in JSON to display on the StatusBoard app"""
    output = {"graph": {"title": GRAPH_TITLE, "error": {"message": message, "detail": detail}}}
    return json.dumps(output)


def generate_json(snmp_monitor):
    """This function will take the device config and raw data (if any) from the snmp_monitor and output JSON data
    formatted for the StatusBar iPad App"""

    time_x_axis = time.strftime("%H:%M")         # Use the same time value for all samples per iteration
    datasequences = []
    snmp_error = None

    # First time through, create a list of InterfaceDevices using the contants provided above
    if len(AggregateInterface.all_aggr_interfaces) == 0:
        for aggr_interface in AGGREGATE_INTERFACES:
            AggregateInterface(aggr_interface["name"], aggr_interface["oid"])

    # Loop through each aggregate interface, update the SNMP data for each item
    for aggr_interface in AggregateInterface.all_aggr_interfaces:

        # Get the SNMP data
        try:
            snmp_value1, snmp_value2, snmp_value3, snmp_value4, snmp_uptime_value, \
                snmp_error = get_snmp(
                    DEVICE_IP,
                    DEVICE_SNMP,
                    aggr_interface.interface_oids[0],
                    aggr_interface.interface_oids[1],
                    aggr_interface.interface_oids[2],
                    aggr_interface.interface_oids[3],
                    DEVICE_UPTIME_OID
                )
        except Exception as error:
            if not snmp_error:
                snmp_error = error.message
            if snmp_error:
                break
        else:
            # Total the value of our 4 interfaces
            snmp_value = snmp_value1 + snmp_value2 + snmp_value3 + snmp_value4

            # Add the raw SNMP data to a list
            if len(aggr_interface.snmp_data) == 0:                # first time through, initialize the list
                aggr_interface.snmp_data = [SNMPDatapoint(snmp_value, snmp_uptime_value)]
            else:
                aggr_interface.snmp_data.append(SNMPDatapoint(snmp_value, snmp_uptime_value))
            # If we already have the max number of datapoints in our list, delete the oldest item
            if len(aggr_interface.snmp_data) >= MAX_DATAPOINTS:
                del(aggr_interface.snmp_data[0])

            # If we have at least 2 samples, calculate bps by comparing the last item with the second to last item
            if len(aggr_interface.snmp_data) > 1:
                bps = calculate_bps(
                    aggr_interface.snmp_data[-1].value,
                    aggr_interface.snmp_data[-1].timeticks,
                    aggr_interface.snmp_data[-2].value,
                    aggr_interface.snmp_data[-2].timeticks
                )
                bps = round(bps, 2)
                if len(aggr_interface.datapoints) == 0:
                    aggr_interface.datapoints = [{"title": time_x_axis, "value": bps}]
                else:
                    aggr_interface.datapoints.append({"title": time_x_axis, "value": bps})
                # If we already have the max number of datapoints, delete the oldest item.
                if len(aggr_interface.datapoints) >= MAX_DATAPOINTS:
                    del(aggr_interface.datapoints[0])

        # Generate the data sequence
        datasequences.append({"title": aggr_interface.name, "datapoints": aggr_interface.datapoints})


    # If we ran into an SNMP error, go ahead and write out the JSON file with the error
    if snmp_error:
        snmp_monitor.json = output_message("Error retrieving SNMP data", snmp_error)

    # If this is the first run through, show Initializing on iPad
    elif len(AggregateInterface.all_aggr_interfaces[-1].snmp_data) <= 2:
        snmp_monitor.json = output_message(
            "Initializing bandwidth dataset: " +
            str(SAMPLE_INTERVAL * (3 - len(AggregateInterface.all_aggr_interfaces[-1].snmp_data))) +
            " seconds...", ""
        )
    else:

        # Generate JSON output and assign to snmp_monitor object (for return back to caller module)
        graph = {
            "title": GRAPH_TITLE, "type": "line",
            "refreshEveryNSeconds": SAMPLE_INTERVAL,
            "datasequences": datasequences
        }
        snmp_monitor.json = json.dumps({"graph": graph})

    if __debug__:
        print snmp_monitor.json



# If you run this module by itself, it will instantiate the MonitorJSON class and start an infinite loop printing data.
if __name__ == '__main__':
    monitor = MonitorJSON()
    while True:
        generate_json(monitor)
        # Wait X seconds for the next iteration
        time.sleep(SAMPLE_INTERVAL)