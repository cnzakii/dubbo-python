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
import enum

from .grpc.constants import MetadataKeys


class TriMetadataKeys(enum.StrEnum):
    """
    Metadata keys used in Triple protocol,
    which is based on gRPC with Dubbo-specific extensions.
    """

    # === Standard gRPC metadata keys (inherited from gRPC) ===
    GRPC_TIMEOUT = MetadataKeys.TIMEOUT
    GRPC_ENCODING = MetadataKeys.ENCODING
    GRPC_ACCEPT_ENCODING = MetadataKeys.ACCEPT_ENCODING
    GRPC_STATUS = MetadataKeys.STATUS
    GRPC_MESSAGE = MetadataKeys.MESSAGE
    GRPC_STATUS_DETAILS_BIN = MetadataKeys.STATUS_DETAILS_BIN

    # === Triple-specific metadata keys ===
    CONSUMER_APP_NAME = "tri-consumer-appname"
    SERVICE_VERSION = "tri-service-version"
    SERVICE_GROUP = "tri-service-group"
    SERVICE_TIMEOUT = "tri-service-timeout"
    HEADER_CONVERT = "tri-header-convert"
    EXCEPTION_CODE = "tri-exception-code"
