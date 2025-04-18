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

import ssl
from typing import Optional, Union

from ..types import FilePath, StrOrBytes
from ._base import BaseConfig


def _create_ssl_context(
    purpose: Union[ssl.Purpose, int],
    cert_file: Optional[FilePath] = None,
    key_file: Optional[StrOrBytes] = None,
    key_password: Optional[StrOrBytes] = None,
    trust_store_path: Optional[FilePath] = None,
) -> ssl.SSLContext:
    """
    Create and configure an SSLContext for client or server use.

    :param purpose: Either a ssl.Purpose enum or a raw SSL protocol constant.
    :type purpose: Union[ssl.Purpose, int]
    :param cert_file: Path to a certificate chain file (PEM) for identity.
    :type cert_file: Optional[FilePath]
    :param key_file: Path to the private key file corresponding to cert_file.
    :type key_file: Optional[StrOrBytes]
    :param key_password: Password to decrypt the private key, if encrypted.
    :type key_password: Optional[StrOrBytes]
    :param trust_store_path: Directory or file path containing trusted CA certificates.
    :type trust_store_path: Optional[FilePath]
    :returns: An initialized and configured ssl.SSLContext instance.
    :rtype: ssl.SSLContext
    """
    if isinstance(purpose, ssl.Purpose):
        ctx = ssl.create_default_context(purpose)
    else:
        ctx = ssl.SSLContext(purpose)

    if cert_file:
        ctx.load_cert_chain(certfile=cert_file, keyfile=key_file, password=key_password)

    if trust_store_path:
        ctx.load_verify_locations(capath=trust_store_path)

    return ctx


class SSLConfig(BaseConfig):
    """
    Manages SSL contexts for both client and server roles.

    This class either accepts pre-built SSLContext objects or constructs new
    ones using certificate, key, and trust store files.
    """

    _client_ctx: Optional[ssl.SSLContext]
    _server_ctx: Optional[ssl.SSLContext]

    def __init__(
        self,
        *,
        client_ssl_context: Optional[ssl.SSLContext] = None,
        client_purpose: Union[ssl.Purpose, int] = ssl.Purpose.CLIENT_AUTH,
        client_cert_file: Optional[FilePath] = None,
        client_key_file: Optional[StrOrBytes] = None,
        client_key_password: Optional[StrOrBytes] = None,
        client_trust_store_path: Optional[FilePath] = None,
        server_ssl_context: Optional[ssl.SSLContext] = None,
        server_purpose: Union[ssl.Purpose, int] = ssl.Purpose.SERVER_AUTH,
        server_cert_file: Optional[FilePath] = None,
        server_key_file: Optional[StrOrBytes] = None,
        server_key_password: Optional[StrOrBytes] = None,
        server_trust_store_path: Optional[FilePath] = None,
    ):
        """
        Initialize SSLConfig with either provided contexts or file-based settings.

        :param client_ssl_context: An existing SSLContext for client-side use.
        :type client_ssl_context: Optional[ssl.SSLContext]
        :param client_purpose: Purpose for client context (e.g., CLIENT_AUTH).
        :type client_purpose: Union[ssl.Purpose, int]
        :param client_cert_file: Path to the client's certificate chain (PEM).
        :type client_cert_file: Optional[FilePath]
        :param client_key_file: Path to the client's private key file.
        :type client_key_file: Optional[StrOrBytes]
        :param client_key_password: Password for decrypting the client's private key.
        :type client_key_password: Optional[StrOrBytes]
        :param client_trust_store_path: Path to trusted CAs for client validation.
        :type client_trust_store_path: Optional[FilePath]
        :param server_ssl_context: An existing SSLContext for server-side use.
        :type server_ssl_context: Optional[ssl.SSLContext]
        :param server_purpose: Purpose for server context (e.g., SERVER_AUTH).
        :type server_purpose: Union[ssl.Purpose, int]
        :param server_cert_file: Path to the server's certificate chain (PEM).
        :type server_cert_file: Optional[FilePath]
        :param server_key_file: Path to the server's private key file.
        :type server_key_file: Optional[StrOrBytes]
        :param server_key_password: Password for decrypting the server's private key.
        :type server_key_password: Optional[StrOrBytes]
        :param server_trust_store_path: Path to trusted CAs for server validation.
        :type server_trust_store_path: Optional[FilePath]
        """
        if client_ssl_context:
            self._client_ctx = client_ssl_context
        else:
            self._client_ctx = _create_ssl_context(
                client_purpose, client_cert_file, client_key_file, client_key_password, client_trust_store_path
            )

        if server_ssl_context:
            self._server_ctx = server_ssl_context
        else:
            self._server_ctx = _create_ssl_context(
                server_purpose, server_cert_file, server_key_file, server_key_password, server_trust_store_path
            )

    @property
    def client_ctx(self) -> Optional[ssl.SSLContext]:
        """
        Return the configured client-side SSLContext.

        :returns: The SSLContext instance used for client operations.
        :rtype: Optional[ssl.SSLContext]
        """
        return self._client_ctx

    @property
    def server_ctx(self) -> Optional[ssl.SSLContext]:
        """
        Return the configured server-side SSLContext.

        :returns: The SSLContext instance used for server operations.
        :rtype: Optional[ssl.SSLContext]
        """
        return self._server_ctx
