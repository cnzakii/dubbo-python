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

"""The schemas of one OpenAPI document, made together once every operation has asked for its own."""

from typing import Any

from google.protobuf.descriptor import FieldDescriptor

from dubbo.serialization.models import Mode, Models, merge_definitions
from dubbo.serialization.models.protobuf import field_schema, is_message, message_schema
from dubbo.utils.annotations import unannotated

__all__ = ["REF", "Schemas"]

REF = "#/components/schemas/{name}"
# Where the schemas of protobuf messages and fields refer to their definitions until their names are final.
_PROTOBUF = "dubbo-protobuf:"


class Schemas:
    """Collects the schemas the operations of a document ask for, so that their definitions are named together.

    A protobuf message, or a field of one, has the schema of the proto3 JSON
    mapping, named by its full name, without msgspec; any other type that of
    msgspec, with the classes of the installed models as they describe them.
    """

    def __init__(self, models: Models) -> None:
        self._models = models
        self._types: list[tuple[dict[str, Any], Any, Mode]] = []
        self._protobuf: list[tuple[dict[str, Any], Any]] = []

    def of(self, source: Any, mode: Mode) -> dict[str, Any]:
        """Return the schema of the type annotation or protobuf field ``source`` in ``mode``, empty until `resolve`."""
        slot: dict[str, Any] = {}
        if isinstance(source, FieldDescriptor) or is_message(unannotated(source)):
            self._protobuf.append((slot, source))
        else:
            self._types.append((slot, source, mode))
        return slot

    def resolve(self) -> dict[str, Any]:
        """Fill in every schema asked for, and return the definitions they refer to by name.

        Raises:
            TypeError: msgspec cannot describe a type.
        """
        schemas: list[dict[str, Any]] = []
        definitions: dict[str, Any] = {}
        if self._types:
            schemas, definitions = self._models.schemas([(source, mode) for _, source, mode in self._types], REF)
        found: dict[str, Any] = {}
        template = _PROTOBUF + "{name}"
        protobuf = [
            field_schema(source, found, template)
            if isinstance(source, FieldDescriptor)
            else message_schema(unannotated(source).DESCRIPTOR, found, template)
            for _, source in self._protobuf
        ]
        protobuf, definitions = merge_definitions(protobuf, definitions, [(_PROTOBUF, found)], REF)
        slots = [slot for slot, *_ in self._types] + [slot for slot, _ in self._protobuf]
        for slot, schema in zip(slots, [*schemas, *protobuf], strict=True):
            slot.update(schema)
        return definitions
