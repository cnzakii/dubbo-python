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

from dubbo.__about__ import __version__

USER_AGENT = f"dubbo-python/{__version__}".lower()

DEVELOPMENT_ENVIRONMENT = "develop"
TEST_ENVIRONMENT = "test"
PRODUCTION_ENVIRONMENT = "product"

ENVIRONMENT_VALUES = {
    DEVELOPMENT_ENVIRONMENT,
    TEST_ENVIRONMENT,
    PRODUCTION_ENVIRONMENT,
}

DEFAULT_TRI_PORT = 50051

ANY_VALUE = "*"
PROVIDERS_CATEGORY = "providers"
CONSUMERS_CATEGORY = "consumers"
ROUTERS_CATEGORY = "routers"
CONFIGURATORS_CATEGORY = "configurators"
DEFAULT_CATEGORY = PROVIDERS_CATEGORY

CATEGORY_VALUES = {
    PROVIDERS_CATEGORY,
    CONSUMERS_CATEGORY,
    ROUTERS_CATEGORY,
    CONFIGURATORS_CATEGORY,
}

DEFAULT_TIMEOUT_VALUE = 10.0  # units: seconds
DEFAULT_WEIGHT_VALUE = 100
DEFAULT_WARMUP_VALUE = 0
