#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import ipaddress
from typing import Union

import psutil

# Define constants for IP versions
IPV4_VERSION = 4
IPV6_VERSION = 6


def is_valid_host(host: str) -> bool:
    """
    Check if the provided host is a valid IP address.
    :param host: The host address to check
    :return: True if the host is a valid IP address, False otherwise
    """
    try:
        ipaddress.ip_address(host)  # Try to parse the host address
        return True
    except ValueError:
        return False  # If parsing fails, it's not a valid IP address


def _is_valid_ip_version(host: str, ip_version: int) -> bool:
    """
    Check if the provided host is a valid IP address of the specified version (IPv4/IPv6).
    :param host: The host address to check
    :param ip_version: The IP version to check against (4 for IPv4, 6 for IPv6)
    :return: True if the host is a valid IP address of the specified version, False otherwise
    """
    try:
        ip = ipaddress.ip_address(host)
        return ip.version == ip_version
    except ValueError:
        return False  # If parsing fails, the address is invalid


def is_valid_ipv4(host: str) -> bool:
    """
    Check if the provided host is a valid IPv4 address.
    :param host: The host address to check
    :return: True if the host is a valid IPv4 address, False otherwise
    """
    return _is_valid_ip_version(host, IPV4_VERSION)


def is_valid_ipv6(host: str) -> bool:
    """
    Check if the provided host is a valid IPv6 address.
    :param host: The host address to check
    :return: True if the host is a valid IPv6 address, False otherwise
    """
    return _is_valid_ip_version(host, IPV6_VERSION)


def get_local_host(ip_version: int = IPV4_VERSION) -> Union[ipaddress.IPv4Address, ipaddress.IPv6Address]:
    """
    Get a valid local IP address of the specified version.
    If no valid address is found, raises an OSError.

    :param ip_version: IP version, default is IPv4. Options: IPv4 (4), IPv6 (6)
    :type ip_version: int
    :return: Returns a valid local IP address object
    :rtype: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]
    :raises OSError: If no valid IP address is found, an exception is raised
    """
    if ip_version not in (IPV4_VERSION, IPV6_VERSION):
        raise ValueError(f"Invalid IP version: {ip_version}. Must be {IPV4_VERSION} or {IPV6_VERSION}.")

    # Retrieve all network interfaces and their addresses
    for interface_name, address_list in psutil.net_if_addrs().items():
        for address_info in address_list:
            # Skip invalid address information
            try:
                address = ipaddress.ip_address(address_info.address)

                # Filter out addresses that don't meet the following criteria:
                # 1. Not multicast (multicast addresses are for one-to-many communication)
                # 2. Not reserved (reserved for future use or special purposes)
                # 3. Not link-local (addresses valid only within the local network segment)
                # 4. Not loopback (addresses valid only for local communication on the host)

                if (
                    not address.is_multicast
                    and not address.is_reserved
                    and not address.is_link_local
                    and not address.is_loopback
                    and address.version == ip_version
                ):
                    return address
            except ValueError:
                # Skip invalid address
                continue

    # If no valid address is found, raise an exception
    raise OSError(f"No available local host found for IP version {ip_version}.")
