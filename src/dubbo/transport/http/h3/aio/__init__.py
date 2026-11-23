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

from dubbo.utils import require_optional_dependency

# Checked here, so that every module of the package may import ngquic directly.
with require_optional_dependency("ngquic", extra="http3"):
    import ngquic  # noqa: F401

from .client import AioH3Client
from .stream import AioH3ClientStream, AioH3ServerStream, AioH3Stream

__all__ = ["AioH3Client", "AioH3ClientStream", "AioH3ServerStream", "AioH3Stream"]
