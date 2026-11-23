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

"""pydantic models, converted by pydantic itself, for the msgspec serializations and plain HTTP."""

import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

from dubbo.url import URL

from .base import Mode, ModelField

if TYPE_CHECKING:
    from pydantic import BaseModel

__all__ = ["PydanticSupport"]

_MODES: dict[Mode, Literal["validation", "serialization"]] = {"input": "validation", "output": "serialization"}


class PydanticSupport:
    """Converts pydantic models as pydantic does: by the aliases of their fields, writing them in JSON mode.

    It supports nothing until the application imports pydantic, which it
    never imports itself.
    """

    def __init__(self, url: URL) -> None:
        pass

    def supports(self, cls: type) -> bool:
        pydantic = sys.modules.get("pydantic")
        return pydantic is not None and issubclass(cls, pydantic.BaseModel)

    def to_builtins(self, value: Any) -> Any:
        from pydantic_core import PydanticSerializationError

        try:
            return value.model_dump(mode="json", by_alias=True, warnings="error")
        except PydanticSerializationError as error:
            raise TypeError(str(error)) from None

    def from_builtins(self, cls: type, data: Any) -> Any:
        from pydantic import ValidationError

        try:
            return cast("type[BaseModel]", cls).model_validate(data)
        except ValidationError as error:
            problems = (
                f"{'.'.join(map(str, item['loc']))}: {item['msg']}" if item["loc"] else item["msg"]
                for item in error.errors()
            )
            raise ValueError("; ".join(problems)) from None

    def schemas(
        self, entries: Sequence[tuple[type, Mode]], ref_template: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        from pydantic.json_schema import models_json_schema

        # pydantic names the definitions of a model whose modes differ Name-Input and Name-Output.
        modes = [(cast("type[BaseModel]", cls), _MODES[mode]) for cls, mode in entries]
        keys, top = models_json_schema(modes, ref_template=ref_template.replace("{name}", "{model}"))
        return [keys[key] for key in modes], top.get("$defs", {})

    def fields(self, cls: type) -> tuple[ModelField, ...] | None:
        result = []
        for name, info in cast("type[BaseModel]", cls).model_fields.items():
            alias = info.validation_alias if isinstance(info.validation_alias, str) else info.alias
            # The constraints of the field stay in its metadata, as pydantic keeps them.
            kind = Annotated[info.annotation, *info.metadata] if info.metadata else info.annotation  # ty: ignore[invalid-type-form]
            result.append(
                ModelField(key=alias or name, type=kind, required=info.is_required(), description=info.description)
            )
        return tuple(result)
