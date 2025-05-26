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
import random
import socket
from typing import Optional, Union

import psutil

from dubbo.common.types import HostLike

# Define constants for port ranges
RND_PORT_START = 30000
RND_PORT_RANGE = 10000

# Valid port range is [1, 65535]
MIN_PORT = 1
MAX_PORT = 65535

# Define constants for IP versions
IPV4_VERSION = 4
IPV6_VERSION = 6


# Check if socket reuse address is supported
reuse_address_supported = False
try:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        reuse_address_supported = True
except OSError:
    pass


def is_port_in_use(port: int) -> bool:
    """Check if the specified port is in use.

    Args:
        port: Port number to check.

    Returns:
        True if port is in use, False if available.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if reuse_address_supported:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", port))
            return False  # Port is available
    except OSError:
        return True  # Port is in use


def get_random_port() -> int:
    """Get a random port in the default range.

    Returns:
        Random port number between RND_PORT_START and RND_PORT_START + RND_PORT_RANGE.
    """
    return RND_PORT_START + random.randint(0, RND_PORT_RANGE - 1)


def get_available_port(port: Optional[int] = None) -> int:
    """Find an available port starting from the specified port.

    Args:
        port: Starting port number. If None, uses a random port.

    Returns:
        First available port number found.

    Raises:
        OSError: If no available ports are found.

    Example:
        available_port = get_available_port(8080)
        random_available_port = get_available_port()
    """
    if port is None:
        port = get_random_port()

    if port < MIN_PORT:
        port = MIN_PORT

    for i in range(port, MAX_PORT + 1):
        if not is_port_in_use(i):
            return i

    # If we get here, no ports were available
    raise OSError(f"No available ports found starting from {port}")


def is_valid_host(host: str) -> bool:
    """Check if the provided host is a valid IP address.

    Args:
        host: The host address to validate.

    Returns:
        True if the host is a valid IP address, False otherwise.
    """
    try:
        ipaddress.ip_address(host)  # Try to parse the host address
        return True
    except ValueError:
        return False  # If parsing fails, it's not a valid IP address


def _is_valid_ip_version(host: str, ip_version: int) -> bool:
    """Check if the host is a valid IP address of the specified version.

    Args:
        host: The host address to validate.
        ip_version: The IP version to check against (4 for IPv4, 6 for IPv6).

    Returns:
        True if the host is a valid IP address of the specified version.
    """
    try:
        ip = ipaddress.ip_address(host)
        return ip.version == ip_version
    except ValueError:
        return False  # If parsing fails, the address is invalid


def is_valid_ipv4(host: str) -> bool:
    """Check if the host is a valid IPv4 address.

    Args:
        host: The host address to validate.

    Returns:
        True if the host is a valid IPv4 address.
    """
    return _is_valid_ip_version(host, IPV4_VERSION)


def is_valid_ipv6(host: str) -> bool:
    """Check if the host is a valid IPv6 address.

    Args:
        host: The host address to validate.

    Returns:
        True if the host is a valid IPv6 address.
    """
    return _is_valid_ip_version(host, IPV6_VERSION)


def get_address_family(host: HostLike) -> socket.AddressFamily:
    """Determine the address family (IPv4/IPv6) for the given host.

    This function attempts to resolve the host to an IP address and returns
    the corresponding socket address family. It supports both IP addresses
    and domain names.

    Args:
        host: The host to determine the address family for. Can be a string
              (IP address or domain name), IPv4Address, or IPv6Address object.

    Returns:
        socket.AddressFamily: AF_INET for IPv4 addresses, AF_INET6 for IPv6
                             addresses, or AF_UNSPEC if the address family
                             cannot be determined.
    """
    # If host is already an IP address object, check its version directly
    if isinstance(host, ipaddress.IPv4Address):
        return socket.AF_INET
    elif isinstance(host, ipaddress.IPv6Address):
        return socket.AF_INET6

    # Convert to string if not already
    host_str = str(host)

    # First, try to parse as an IP address directly
    try:
        ip_addr = ipaddress.ip_address(host_str)
        if ip_addr.version == IPV4_VERSION:
            return socket.AF_INET
        elif ip_addr.version == IPV6_VERSION:
            return socket.AF_INET6
    except ValueError:
        # Not a valid IP address, try to resolve as hostname
        pass

    # Try to resolve the hostname using getaddrinfo
    try:
        # Use getaddrinfo to resolve the hostname and get address family
        # We use AF_UNSPEC to allow both IPv4 and IPv6 resolution
        addr_info = socket.getaddrinfo(host_str, None, socket.AF_UNSPEC)
        if addr_info:
            # Return the address family of the first resolved address
            return addr_info[0][0]
    except (socket.gaierror, OSError):
        # Hostname resolution failed
        pass

    # If all attempts fail, return AF_UNSPEC
    return socket.AF_UNSPEC


def get_local_host(ip_version: int = IPV4_VERSION) -> Union[ipaddress.IPv4Address, ipaddress.IPv6Address]:
    """Get a valid local IP address of the specified version.

    Scans all network interfaces to find a suitable local IP address that is
    not multicast, reserved, link-local, or loopback.

    Args:
        ip_version: IP version to search for. Defaults to IPv4 (4).
                   Valid options are 4 (IPv4) or 6 (IPv6).

    Returns:
        A valid local IP address object for the specified version.

    Raises:
        ValueError: If ip_version is not 4 or 6.
        OSError: If no valid IP address is found.

    Example:
        ipv4_addr = get_local_host(4)
        ipv6_addr = get_local_host(6)
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
