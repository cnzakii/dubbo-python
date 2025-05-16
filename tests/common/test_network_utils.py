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

"""
Tests for network utility functions in dubbo.common.utils.network module.
"""

import ipaddress
import socket
import unittest.mock as mock

import pytest

from dubbo.common.utils.network import (
    IPV4_VERSION,
    IPV6_VERSION,
    MAX_PORT,
    MIN_PORT,
    RND_PORT_RANGE,
    RND_PORT_START,
    _is_valid_ip_version,
    get_available_port,
    get_local_host,
    get_random_port,
    is_port_in_use,
    is_valid_host,
    is_valid_ipv4,
    is_valid_ipv6,
    reuse_address_supported,
)


class TestPortFunctions:
    """
    Tests for port-related functions.
    """

    def test_reuse_address_supported_flag(self):
        """
        Test that reuse_address_supported flag is a boolean.

        :return: None
        """
        assert isinstance(reuse_address_supported, bool)

    def test_is_port_in_use_with_available_port(self):
        """
        Test is_port_in_use with a port that is available.

        :return: None
        """
        # Mock socket.socket so we can control its behavior
        with mock.patch("socket.socket") as mock_socket:
            # Setup the context manager mock
            mock_sock = mock.MagicMock()
            mock_socket.return_value.__enter__.return_value = mock_sock

            # Test with port that is not in use (bind succeeds)
            assert not is_port_in_use(8080)

            # Verify socket was created and bind was called with correct arguments
            mock_socket.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
            mock_sock.bind.assert_called_once_with(("0.0.0.0", 8080))

    def test_is_port_in_use_with_unavailable_port(self):
        """
        Test is_port_in_use with a port that is unavailable.

        :return: None
        """
        # Mock socket.socket to raise an error during bind
        with mock.patch("socket.socket") as mock_socket:
            mock_sock = mock.MagicMock()
            mock_socket.return_value.__enter__.return_value = mock_sock
            mock_sock.bind.side_effect = OSError("Port already in use")

            # Test with port that is in use (bind fails)
            assert is_port_in_use(8080)

            # Verify socket was created and bind was called
            mock_socket.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
            mock_sock.bind.assert_called_once_with(("0.0.0.0", 8080))

    def test_get_random_port(self):
        """
        Test get_random_port returns a port within the expected range.

        :return: None
        """
        # Since this uses random.randint, we'll just verify the bounds
        for _ in range(100):  # Test multiple times to increase confidence
            port = get_random_port()
            assert RND_PORT_START <= port < RND_PORT_START + RND_PORT_RANGE

    def test_get_available_port_with_default(self):
        """
        Test get_available_port with default parameters.

        :return: None
        """
        # Mock get_random_port and is_port_in_use
        with mock.patch("dubbo.common.utils.network.get_random_port") as mock_random:
            with mock.patch("dubbo.common.utils.network.is_port_in_use") as mock_in_use:
                # Setup mocks to return a specific port and mark it as available
                mock_random.return_value = 30000
                mock_in_use.return_value = False

                # Test get_available_port with default parameters
                port = get_available_port()

                # Verify functions were called correctly
                mock_random.assert_called_once()
                mock_in_use.assert_called_once_with(30000)
                assert port == 30000

    def test_get_available_port_with_specified_port(self):
        """
        Test get_available_port with a specified port.

        :return: None
        """
        # Mock is_port_in_use
        with mock.patch("dubbo.common.utils.network.is_port_in_use") as mock_in_use:
            # Setup mock to mark the port as available
            mock_in_use.return_value = False

            # Test get_available_port with a specific port
            port = get_available_port(8080)

            # Verify functions were called correctly
            mock_in_use.assert_called_once_with(8080)
            assert port == 8080

    def test_get_available_port_with_first_port_unavailable(self):
        """
        Test get_available_port when the first port checked is unavailable.

        :return: None
        """
        # Mock is_port_in_use
        with mock.patch("dubbo.common.utils.network.is_port_in_use") as mock_in_use:
            # Setup mock to mark first port as unavailable, second port as available
            mock_in_use.side_effect = [True, False]

            # Test get_available_port
            port = get_available_port(8080)

            # Verify functions were called correctly
            assert mock_in_use.call_args_list == [mock.call(8080), mock.call(8081)]
            assert port == 8081

    def test_get_available_port_with_invalid_port(self):
        """
        Test get_available_port with an invalid port (below MIN_PORT).

        :return: None
        """
        # Mock is_port_in_use
        with mock.patch("dubbo.common.utils.network.is_port_in_use") as mock_in_use:
            # Setup mock to mark port as available
            mock_in_use.return_value = False

            # Test get_available_port with an invalid port
            port = get_available_port(0)

            # Verify functions were called correctly
            mock_in_use.assert_called_once_with(MIN_PORT)
            assert port == MIN_PORT

    def test_get_available_port_no_ports_available(self):
        """
        Test get_available_port when no ports are available.

        :return: None
        """
        # Mock is_port_in_use to always return True (all ports in use)
        with mock.patch("dubbo.common.utils.network.is_port_in_use") as mock_in_use:
            mock_in_use.return_value = True

            # Define a smaller range for testing
            test_port = MAX_PORT - 10

            # Test get_available_port, should raise OSError
            with pytest.raises(OSError) as excinfo:
                get_available_port(test_port)

            # Verify error message
            assert f"No available ports found starting from {test_port}" in str(excinfo.value)

            # Verify all ports in range were checked
            assert len(mock_in_use.call_args_list) == MAX_PORT - test_port + 1


class TestIPFunctions:
    """
    Tests for IP address related functions.
    """

    def test_is_valid_host_with_valid_ipv4(self):
        """
        Test is_valid_host with valid IPv4 addresses.

        :return: None
        """
        assert is_valid_host("192.168.1.1") is True
        assert is_valid_host("127.0.0.1") is True
        assert is_valid_host("0.0.0.0") is True
        assert is_valid_host("255.255.255.255") is True

    def test_is_valid_host_with_valid_ipv6(self):
        """
        Test is_valid_host with valid IPv6 addresses.

        :return: None
        """
        assert is_valid_host("::1") is True
        assert is_valid_host("2001:db8::1") is True
        assert is_valid_host("fe80::1") is True

    def test_is_valid_host_with_invalid_ip(self):
        """
        Test is_valid_host with invalid IP addresses.

        :return: None
        """
        assert is_valid_host("") is False
        assert is_valid_host("localhost") is False
        assert is_valid_host("256.256.256.256") is False
        assert is_valid_host("192.168.1") is False
        assert is_valid_host("2001:db8::xyz") is False

    def test_is_valid_ipv4_with_valid_ipv4(self):
        """
        Test is_valid_ipv4 with valid IPv4 addresses.

        :return: None
        """
        assert is_valid_ipv4("192.168.1.1") is True
        assert is_valid_ipv4("127.0.0.1") is True
        assert is_valid_ipv4("0.0.0.0") is True
        assert is_valid_ipv4("255.255.255.255") is True

    def test_is_valid_ipv4_with_invalid_ipv4(self):
        """
        Test is_valid_ipv4 with invalid IPv4 addresses.

        :return: None
        """
        assert is_valid_ipv4("") is False
        assert is_valid_ipv4("localhost") is False
        assert is_valid_ipv4("256.256.256.256") is False
        assert is_valid_ipv4("192.168.1") is False

        # IPv6 address should not be valid as IPv4
        assert is_valid_ipv4("2001:db8::1") is False

    def test_is_valid_ipv6_with_valid_ipv6(self):
        """
        Test is_valid_ipv6 with valid IPv6 addresses.

        :return: None
        """
        assert is_valid_ipv6("::1") is True
        assert is_valid_ipv6("2001:db8::1") is True
        assert is_valid_ipv6("fe80::1") is True

    def test_is_valid_ipv6_with_invalid_ipv6(self):
        """
        Test is_valid_ipv6 with invalid IPv6 addresses.

        :return: None
        """
        assert is_valid_ipv6("") is False
        assert is_valid_ipv6("localhost") is False
        assert is_valid_ipv6("2001:db8::xyz") is False

        # IPv4 address should not be valid as IPv6
        assert is_valid_ipv6("192.168.1.1") is False

    def test_is_valid_ip_version_with_ipv4(self):
        """
        Test _is_valid_ip_version with IPv4 addresses.

        :return: None
        """
        assert _is_valid_ip_version("192.168.1.1", IPV4_VERSION) is True
        assert _is_valid_ip_version("192.168.1.1", IPV6_VERSION) is False

    def test_is_valid_ip_version_with_ipv6(self):
        """
        Test _is_valid_ip_version with IPv6 addresses.

        :return: None
        """
        assert _is_valid_ip_version("2001:db8::1", IPV6_VERSION) is True
        assert _is_valid_ip_version("2001:db8::1", IPV4_VERSION) is False

    def test_is_valid_ip_version_with_invalid_ip(self):
        """
        Test _is_valid_ip_version with invalid IP addresses.

        :return: None
        """
        assert _is_valid_ip_version("", IPV4_VERSION) is False
        assert _is_valid_ip_version("localhost", IPV4_VERSION) is False
        assert _is_valid_ip_version("256.256.256.256", IPV4_VERSION) is False


class TestLocalHostFunction:
    """
    Tests for get_local_host function.
    """

    def test_get_local_host_ipv4(self):
        """
        Test get_local_host with IPv4.

        :return: None
        """
        # Mock psutil.net_if_addrs to return controlled data
        mock_addr = mock.MagicMock()
        mock_addr.address = "192.168.1.1"

        with mock.patch("psutil.net_if_addrs") as mock_if_addrs:
            mock_if_addrs.return_value = {"eth0": [mock_addr]}

            # Mock ipaddress.ip_address to return a controlled IPv4Address
            mock_ip = mock.MagicMock(spec=ipaddress.IPv4Address)
            mock_ip.is_multicast = False
            mock_ip.is_reserved = False
            mock_ip.is_link_local = False
            mock_ip.is_loopback = False
            mock_ip.version = IPV4_VERSION

            with mock.patch("ipaddress.ip_address", return_value=mock_ip):
                # Test get_local_host with IPv4
                result = get_local_host(IPV4_VERSION)

                # Verify result
                assert result == mock_ip
                mock_if_addrs.assert_called_once()

    def test_get_local_host_ipv6(self):
        """
        Test get_local_host with IPv6.

        :return: None
        """
        # Mock psutil.net_if_addrs to return controlled data
        mock_addr = mock.MagicMock()
        mock_addr.address = "2001:db8::1"

        with mock.patch("psutil.net_if_addrs") as mock_if_addrs:
            mock_if_addrs.return_value = {"eth0": [mock_addr]}

            # Mock ipaddress.ip_address to return a controlled IPv6Address
            mock_ip = mock.MagicMock(spec=ipaddress.IPv6Address)
            mock_ip.is_multicast = False
            mock_ip.is_reserved = False
            mock_ip.is_link_local = False
            mock_ip.is_loopback = False
            mock_ip.version = IPV6_VERSION

            with mock.patch("ipaddress.ip_address", return_value=mock_ip):
                # Test get_local_host with IPv6
                result = get_local_host(IPV6_VERSION)

                # Verify result
                assert result == mock_ip
                mock_if_addrs.assert_called_once()

    def test_get_local_host_with_invalid_version(self):
        """
        Test get_local_host with an invalid IP version.

        :return: None
        """
        # Test get_local_host with an invalid IP version
        with pytest.raises(ValueError) as excinfo:
            get_local_host(5)

        # Verify error message
        assert "Invalid IP version: 5" in str(excinfo.value)

    def test_get_local_host_no_valid_address(self):
        """
        Test get_local_host when no valid address is found.

        :return: None
        """
        # Mock psutil.net_if_addrs to return controlled data
        mock_addr = mock.MagicMock()
        mock_addr.address = "192.168.1.1"

        with mock.patch("psutil.net_if_addrs") as mock_if_addrs:
            mock_if_addrs.return_value = {"eth0": [mock_addr]}

            # Mock ipaddress.ip_address to return a controlled IPv4Address that doesn't meet criteria
            mock_ip = mock.MagicMock(spec=ipaddress.IPv4Address)
            mock_ip.is_multicast = True  # This will cause it to be filtered out
            mock_ip.is_reserved = False
            mock_ip.is_link_local = False
            mock_ip.is_loopback = False
            mock_ip.version = IPV4_VERSION

            with mock.patch("ipaddress.ip_address", return_value=mock_ip):
                # Test get_local_host
                with pytest.raises(OSError) as excinfo:
                    get_local_host(IPV4_VERSION)

                # Verify error message
                assert "No available local host found" in str(excinfo.value)
                mock_if_addrs.assert_called_once()

    def test_get_local_host_with_invalid_address(self):
        """
        Test get_local_host with an invalid address that raises ValueError during parsing.

        :return: None
        """
        # Mock psutil.net_if_addrs to return controlled data
        mock_addr = mock.MagicMock()
        mock_addr.address = "not a valid IP"

        with mock.patch("psutil.net_if_addrs") as mock_if_addrs:
            mock_if_addrs.return_value = {"eth0": [mock_addr]}

            # Mock ipaddress.ip_address to raise ValueError
            with mock.patch("ipaddress.ip_address", side_effect=ValueError("Invalid IP")):
                # Test get_local_host
                with pytest.raises(OSError) as excinfo:
                    get_local_host(IPV4_VERSION)

                # Verify error message
                assert "No available local host found" in str(excinfo.value)
                mock_if_addrs.assert_called_once()
