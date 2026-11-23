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

"""The call model: service contracts, invocations, streams and call context."""

from .client import GenericService
from .context import IncomingCall, OutgoingCall, incoming, outgoing
from .definition import (
    Method,
    Parameter,
    RpcType,
    Service,
    get_definition,
    method,
    service,
)
from .invocation import Invocation, Invoke, Invoker, Result
from .params import (
    Body,
    Cookie,
    Form,
    Header,
    Param,
    ParamLocation,
    Path,
    Query,
)
from .rest import (
    HttpRoute,
    delete,
    get,
    patch,
    post,
    put,
    route,
)
from .stream import BidiStream, ClientStream, ReceiveStream, SendStream, ServerStream, Stream

__all__ = [
    "BidiStream",
    "Body",
    "ClientStream",
    "Cookie",
    "Form",
    "GenericService",
    "Header",
    "HttpRoute",
    "IncomingCall",
    "Invocation",
    "Invoke",
    "Invoker",
    "Method",
    "OutgoingCall",
    "Param",
    "ParamLocation",
    "Parameter",
    "Path",
    "Query",
    "ReceiveStream",
    "Result",
    "RpcType",
    "SendStream",
    "ServerStream",
    "Service",
    "Stream",
    "delete",
    "get",
    "get_definition",
    "incoming",
    "method",
    "outgoing",
    "patch",
    "post",
    "put",
    "route",
    "service",
]
