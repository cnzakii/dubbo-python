# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.

"""What the registration of services and of references shares: the host they register at and the parameters they carry."""

import time
from collections.abc import Collection, Mapping

from anyio import to_thread

from dubbo.constants import (
    APPLICATION_KEY,
    ARCHITECTURE_KEY,
    CLUSTER_KEY,
    DEFAULT_REGISTER,
    DEPRECATED_KEY,
    DUBBO_VERSION,
    DUBBO_VERSION_KEY,
    ENVIRONMENT_KEY,
    EXTRA_KEYS_KEY,
    GROUP_KEY,
    INTERFACE_KEY,
    LOADBALANCE_KEY,
    METHODS_KEY,
    ORGANIZATION_KEY,
    OWNER_KEY,
    PATH_KEY,
    PREFER_SERIALIZATION_KEY,
    REGISTER_KEY,
    RELEASE_KEY,
    SERIALIZATION_KEY,
    SIDE_KEY,
    SIMPLIFIED_KEY,
    TAG_KEY,
    TIMEOUT_KEY,
    TIMESTAMP_KEY,
    TOKEN_KEY,
    VERSION_KEY,
    WARMUP_KEY,
    WEIGHT_KEY,
)
from dubbo.protocol.exported import ExportedService
from dubbo.url import URL
from dubbo.utils.converters import boolean, comma_separated
from dubbo.utils.net import local_host
from dubbo.utils.release import RELEASE

__all__ = [
    "RELEASE",
    "provider_url",
    "published_parameters",
    "registered_host",
    "registered_parameters",
    "registered_provider_url",
    "registers",
    "registration_time",
    "simplified",
    "with_extra_keys",
]


# Hosts that listen on every interface, which consumers cannot call.
_WILDCARD_HOSTS = frozenset({"0.0.0.0", "::"})
# Parameters that a provider publishes and its consumers take, besides those of each method.
_PUBLISHED_KEYS = (
    APPLICATION_KEY,
    OWNER_KEY,
    ORGANIZATION_KEY,
    ARCHITECTURE_KEY,
    ENVIRONMENT_KEY,
    GROUP_KEY,
    VERSION_KEY,
    TIMEOUT_KEY,
    WEIGHT_KEY,
    WARMUP_KEY,
    TAG_KEY,
    TOKEN_KEY,
    DEPRECATED_KEY,
    SERIALIZATION_KEY,
)
# Parameters of each method that a provider publishes and its consumers take.
_PUBLISHED_METHOD_KEYS = (TIMEOUT_KEY, WEIGHT_KEY, DEPRECATED_KEY)


async def registered_host(host: str | None, registries: Collection[URL]) -> str:
    """Return the address to register for ``host``, the advertised address or the one a server listens on.

    It is ``host`` unless it is absent or listens on every interface, else
    the local address that reaches the first registry, else the address of
    the host name.
    """
    if host and host not in _WILDCARD_HOSTS:
        return host

    targets = [(url.host, url.port) for url in registries if url.host is not None]
    return await to_thread.run_sync(local_host, targets)


def published_parameters(parameters: Mapping[str, str], methods: Collection[str] | None = None) -> dict[str, str]:
    """Return the parameters among ``parameters`` that a provider publishes and its consumers take.

    They are what the provider says to its consumers, such as ``timeout``
    and ``token``, and the ``timeout``, ``weight`` and ``deprecated`` of a
    method, as in ``sayHello.timeout``: of each of ``methods``, or of any
    method when it is ``None``, as for a URL that lists none.
    """
    return {key: value for key, value in parameters.items() if _published(key, methods)}


def registered_parameters(methods: Collection[str]) -> dict[str, str]:
    """Return the parameters every registered URL carries: ``methods`` sorted, the versions, and the registration time."""
    return {
        METHODS_KEY: ",".join(sorted(methods)),
        DUBBO_VERSION_KEY: DUBBO_VERSION,
        RELEASE_KEY: RELEASE,
        TIMESTAMP_KEY: registration_time(),
    }


def registers(registry: URL) -> bool:
    """Return whether ``registry`` takes registrations: its ``register``, true by default.

    Raises:
        ValueError: ``register`` is not a boolean.
    """
    return registry.get_parameter(REGISTER_KEY, boolean, DEFAULT_REGISTER)


def simplified(registry: URL) -> bool:
    """Return whether ``registry`` takes URLs with only the parameters that identify them: its ``simplified``.

    Raises:
        ValueError: ``simplified`` is not a boolean.
    """
    return registry.get_parameter(SIMPLIFIED_KEY, boolean, False)


def registration_time() -> str:
    """Return the time to register now, in milliseconds since the epoch."""
    return str(time.time_ns() // 1_000_000)


def _published(key: str, methods: Collection[str] | None) -> bool:
    if key in _PUBLISHED_KEYS:
        return True

    method, _, name = key.rpartition(".")
    return name in _PUBLISHED_METHOD_KEYS and bool(method) and (methods is None or method in methods)


# Parameters that simplified provider URLs keep, besides those of their methods and the extra keys; disabled and
# enabled, which override rules set, tell consumers to call the provider no more.
_PROVIDER_KEYS = frozenset({
    APPLICATION_KEY, "codec", "exchanger", SERIALIZATION_KEY, PREFER_SERIALIZATION_KEY, CLUSTER_KEY, "connections",
    DEPRECATED_KEY, GROUP_KEY, LOADBALANCE_KEY, "mock", PATH_KEY, TIMEOUT_KEY, TOKEN_KEY, VERSION_KEY, WARMUP_KEY,
    WEIGHT_KEY, DUBBO_VERSION_KEY, RELEASE_KEY, SIDE_KEY, "ipv6", "serialize.packable.factory", "auth",
    "authenticator", "username", "password", "disabled", "enabled",
})  # fmt: skip


def provider_url(service: ExportedService) -> URL:
    """Return the URL that registries publish for ``service``: what its consumers take from it."""
    methods = [method.name for method in service.definition.methods]
    parameters = published_parameters(service.url.parameters, methods) | registered_parameters(methods)

    return service.url.replace(
        path=service.interface,
        parameters=parameters | {SIDE_KEY: "provider", INTERFACE_KEY: service.interface},
    )


def with_extra_keys(url: URL, served: URL, registry: URL) -> URL:
    """Return ``url`` with the parameters of ``served`` that the ``extra-keys`` of ``registry`` name."""
    extra = comma_separated(registry.parameters.get(EXTRA_KEYS_KEY, ""))
    return url.update_parameters({key: served.parameters[key] for key in extra if key in served.parameters})


def registered_provider_url(url: URL, registry: URL) -> URL:
    """Return ``url`` as ``registry`` takes it: whole, or simplified to what consumers need."""
    if not simplified(registry):
        return url

    parameters = url.parameters
    kept = _PROVIDER_KEYS | set(comma_separated(registry.parameters.get(EXTRA_KEYS_KEY, "")))
    # The interface is kept where the path differs, so that consumers still find the service by it.
    if url.path != parameters.get(INTERFACE_KEY, url.path):
        kept |= {INTERFACE_KEY}
    prefixes = tuple(f"{method}." for method in comma_separated(parameters.get(METHODS_KEY, "")))

    return url.replace(
        parameters={
            key: value for key, value in parameters.items() if value and (key in kept or key.startswith(prefixes))
        },
    )
