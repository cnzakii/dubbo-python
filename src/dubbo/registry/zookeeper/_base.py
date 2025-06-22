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
import abc

from dubbo.common import URL, constants

__all__ = ["BaseZookeeperRegistry"]


class BaseZookeeperRegistry(abc.ABC):
    """
    Base class for Zookeeper registry implementations.
    Provides common functionality for Zookeeper-based registries.
    """

    @property
    @abc.abstractmethod
    def root_path(self) -> str:
        """Returns the root path for Zookeeper nodes."""
        raise NotImplementedError()

    def get_service_path(self, url: URL) -> str:
        """
        Construct the service path based on the given URL.

        Structure:
            /{root}/{service}

        Example:
            /dubbo/com.example.MyService
        """
        service = url.get_service()
        root = self.root_path.strip("/")
        return f"/{root}/{service}" if root else f"/{service}"

    def get_category_path(self, url: URL) -> str:
        """
        Construct the full category path for the given URL.

        Structure:
            /{root}/{service}/{category}

        Example:
            /dubbo/com.example.MyService/providers
        """
        service_path = self.get_service_path(url)
        category = url.get_param(constants.CATEGORY_KEY, constants.DEFAULT_CATEGORY)
        return f"{service_path}/{category}"

    def get_all_category_paths(self, url: URL) -> list[str]:
        """
        Construct all category paths for the given URL.

        Structure:
            /{root}/{service}/{category}

        If the category is '*', this returns all predefined categories.

        Example:
            ["/dubbo/com.example.MyService/providers",
             "/dubbo/com.example.MyService/consumers"]
        """
        category = url.get_param(constants.CATEGORY_KEY, constants.DEFAULT_CATEGORY)
        categories = constants.CATEGORY_VALUES if category == constants.ANY_VALUE else [category]
        service_path = self.get_service_path(url)
        return [f"{service_path}/{item}" for item in categories]

    def get_registry_path(self, url: URL) -> str:
        """
        Get the registry path for the given URL.
        structure: /{root}/{service}/{category}/{full_url}
        example: /dubbo/com.example.MyService/providers/tri://localhost:50051?application=dubbo-python
        """
        return f"{self.get_category_path(url)}/{url.to_str(encode=True)}"
